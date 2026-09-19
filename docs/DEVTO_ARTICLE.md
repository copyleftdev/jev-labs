---
title: "I Put Jev Behind a TLA+ Spec and Ran 1,680 Chaos-Tested Pharmacy Decisions. Zero Wrong Verdicts."
published: false
description: "Jev returns a probability, not prose. That made it possible to model-check the consensus around it, then try to break it. Two of the bugs were mine."
tags: jev, ai, rust, testing
cover_image: https://raw.githubusercontent.com/copyleftdev/jev-labs/main/assets/article/cover-1000x420.png
canonical_url: https://github.com/copyleftdev/jev-labs
series: Sources Under Challenge
---

{% youtube C_l8FI1oddE %}

A pharmacy system has one rule that matters more than being accurate.

It is allowed to say "I don't know." It is not allowed to be sure, and wrong.

That rule is easy to state and hard to test, because the thing making the
judgment is a language model, and a language model is not deterministic.
I sent TypeSafe's Jev the identical request five times and got back 0.03,
0.03, 0.03, 0.04, 0.04. Any property checker built on exact comparison calls
that a failure and is useless.

So I built the thing the way I would build a database: a TLA+ spec first,
model-checked until the quorum bound fell out of the math; an AsyncAPI
contract derived from the spec; Rust generated from the contract; and Jev
behind a single trait as the oracle. Then I ran 1,680 simulated pharmacy
decisions through it under seeded chaos and counted how many times it was
confidently wrong.

Zero. But two of the bugs I found along the way were mine, and the third is
a limit the model cannot cross. That part is the article.

## Why Jev, specifically

Jev (TypeSafe's "System One" model) does not return prose. Ask it whether
oxycodone is a controlled substance and you get `0.98`, not a paragraph
saying so.

That one property is what makes the rest possible. A number has a noise
floor you can measure. A paragraph does not. Once you can measure the noise
you can gate on it, replay it, and prove things about the protocol wrapped
around it.

Measured over 1,490 captured calls to `jev-1.13.0`, every one stored
verbatim with a SHA-256:

- Not deterministic, but the jitter is bounded: identity floor **0.042**,
  question-reorder **0.059**, paraphrase cohort **0.073**.
- Calibrated: accuracy 0.979, Brier 0.0187 on 240 constructed items.
- Latency flat in question count: 1 question 96.7 ms, 38 questions 98.0 ms.
- Billing meter linear to within one token across a 2,500× range.

None of those numbers came from the docs. They are what the API did.

## What I built

```
TLA+ spec  ->  AsyncAPI contract  ->  Rust kernel  ->  Jev, behind one trait
```

**The spec.** Paxos and Raft assume a correct process's proposed value is
stable. With a noisy oracle that assumption is false, and Byzantine models
do not capture it either: the agent is not lying, the oracle is noisy. So
the spec models vote instability as normal behavior, and the invariant is:

```tla
DecisionIsReproducible ==
    decided # ABSTAIN => Cardinality(StableVotesFor(decided)) >= Quorum
```

A vote is *stable* when its margin from 0.5 exceeds the measured noise
floor. TLC finds the naive rule (decide on any quorum) violates this in four
states. The stable rule holds at 1,049,750 distinct states with five agents,
quorum three, and two crashes.

**The quorum bound was derived, not copied.** I wrote the safety invariants,
then swept TLC across 24 configurations of `(agents, byzantine, quorum)` and
printed predicted vs actual per cell. `Safe iff 2Q > N and Q > 2f`. My first
guess was wrong; the sweep corrected it. In the Rust, `QuorumPolicy::new` is
the only constructor, so a configuration TLC proved unsafe cannot be built.

**The kernel.** 48 tests, including replays of TLC counterexample traces.
One test I would point a reviewer at first: `calibration_choice_changes_the_outcome`.
Identical probabilities decide under the identity floor and escalate under
the cohort floor. The measurement changes the behavior, which is the whole
point.

## Then I broke everything

Fourteen pharmacy scenarios in three tiers. *Golden* cases a pharmacist
answers without hesitation. *Nuanced* cases that are harder. *Ambiguous*
cases with no defensible answer, where the correct move is escalation.

Five agents, each asking a different paraphrase of the question. Quorum of
three stable votes. And a deterministic chaos layer driven by one seed:
adversarial text spliced into patient records, records truncated
mid-sentence, agents crashed before the round, rate limits, transport
errors. Any failing round replays exactly.

| chaos | golden rounds | correct | escalated | wrong |
|---|---:|---:|---:|---:|
| none | 360 | 360 | 0 | **0** |
| realistic | 360 | 360 | 0 | **0** |
| severe | 360 | 314 | 46 | **0** |

Zero wrong verdicts in 1,080 golden rounds bounds the true rate below
0.28% at 95% confidence. That is the rule of three. It does not prove the
rate is zero, and I am not going to round it up to "safe."

The escalation rate rose from 5.0% to 18.0% under severe chaos (z = 6.83).
The kernel declines more as evidence degrades. That is the designed
direction.

My favorite round: documented penicillin anaphylaxis, new order for
amoxicillin. A first-year student answers that. Under chaos, two agents got
rate-limited, a third came back at 0.54, sitting inside the noise floor.
Quorum not met. The kernel sent it to a human.

It declined a trivial question, and that is the design working.

## The two bugs that were mine

**Five agents on one prompt are one agent.** I measured it: identical
prompts across five agents spread by 0.010, inside the 0.042 floor. That is
not five judgments. It is one judgment sampled five times, and it guts the
Byzantine math, because `Q > 2f` assumes independent failures. Paraphrasing
raised the spread to 0.080 on hard cases.

Then I audited the paraphrases against records with known answers and found
two that were not paraphrases. "Can pregnancy be excluded on the basis of
this record *alone*?" scored 0.36 on a negative hCG. "Alone" reads as a
challenge to whether one test suffices. Jev was reading correctly. My
question was different from the one I thought I had asked.

**I mislabeled an ambiguous case.** Late period, declined test,
isotretinoin ordered. I labeled it "escalate," because the order obviously
needs a pharmacist. Jev said "pregnancy is not ruled out," 115 times out of
120.

Jev was right. That is the answer, and it is exactly what triggers the hold.
I had confused a property of the prescription with a property of the
question. I relabeled the case, not the model.

I built the ambiguous tier to catch the kernel deciding when it should not.
It caught me first.

## The limit that is real

"Reaction to antibiotics as a child, details unknown, mother reported
stomach upset." New order: amoxicillin.

Jev escalated that 86 times out of 120. It also decided it 34 times, and
split both ways when it did: 25 yes, 9 no, mean probability 0.52.

The stability gate catches jitter around a value. It cannot detect that no
value is warranted. That needs a separate question: "does this record
contain enough to answer?" I have not built it yet, and I would want a
compliance team to see this number before they see the zero.

## What Jev made possible, and what it did not

None of this works on prose. It works because the oracle hands you a
probability. Something you can measure, gate on, replay, and write a spec
against.

What it does not give you is independence. Five agents over one model fail
together against a systematic error. The cheapest fix is prompt diversity,
audited. The real fix is a second vendor chosen for a different training
lineage, and nobody ships signed inference receipts yet, so the last open
gap stays open.

And a first version failed fast on transport errors. Under chaos that voided
71% of rounds and pushed every one to a pharmacist for no clinical reason. A
network blip was creating its own hazard. Now a lost agent is marked
unavailable and the round continues. Zero rounds aborted across 1,680.

The code, the specs, every captured call, and the film pipeline are at
[github.com/copyleftdev/jev-labs](https://github.com/copyleftdev/jev-labs).
Every number in this article is read from a file in that repo; a
`provenance.py` script fails the build if one drifts.

---

*The pharmacy scenarios are synthetic, written to have unambiguous answers
so the harness can detect protocol failures. Nothing here is clinical
guidance. The claims are about the protocol under chaos, not about Jev's
pharmaceutical competence.*

*AI tools assisted with the build and revision. I verified the technical
claims and stand behind the final text.*
