# Vendor selection for the consensus kernel

The question "which vendor?" has a prerequisite that turned out to matter more
than pricing: **do multiple agents over one oracle fail independently?** If they
do not, Byzantine tolerance is arithmetic without meaning and no vendor choice
fixes it.

We measured. Then we chose.

---

## 1. The measurement that reframes the question

Five agents, same state, same question, `jev-1.13.0`:

| case | design | mean | spread | stdev |
|---|---|---|---|---|
| clean | identical | 0.990 | **0.000** | 0.0000 |
| clean | paraphrased | 0.986 | 0.020 | 0.0080 |
| borderline | identical | 0.076 | **0.010** | 0.0049 |
| borderline | paraphrased | 0.064 | **0.080** | 0.0294 |
| injected | identical | 0.052 | **0.010** | 0.0040 |
| injected | paraphrased | 0.070 | 0.030 | 0.0126 |

Identical-question agents spread at most **0.010** — inside the oracle's own
0.042 identity noise floor. They are not five judgments. They are **one
judgment sampled five times.**

Consequences, stated plainly:

- `Q > 2f` and `N >= 3f+1` assume independent failures. Against a *systematic*
  oracle error — literal reading, a training blind spot, an injection that
  lands — every agent fails together. `f=1` tolerance is worthless when the
  fault affects `N`.
- Running 5 agents on identical prompts costs 5x and buys the variance
  reduction of roughly 1.04 agents.

**Paraphrasing decorrelates for free.** On the borderline case, spread rose
from 0.010 to **0.080** — an 8x increase, and above the 0.073 cohort floor, so
it is real signal rather than jitter. Same vendor, same call, different wording:
the cheapest fault tolerance available. Note it only helps where the question is
genuinely hard; the clean case stayed tight (0.020) because there is nothing to
disagree about.

So the first vendor decision is **not** "who do we buy from" but **"how many
vendors do we need in the quorum."**

---

## 2. What we actually need from a vendor

Ranked by what our verification chain depends on, not by marketing:

| need | why it binds | who offers it |
|---|---|---|
| Calibrated probabilities, not text | The stability gate needs a number with a noise floor. Parsing a float out of prose reintroduces the parse-failure mode System One removes. | TypeSafe (native); others via logprobs, awkwardly |
| Cheap fan-out | 19 tokens per extra question vs a 277-token floor; latency flat to 38 questions. Consensus is inherently N-way. | TypeSafe, measured |
| Signed responses (attestation) | Closes our last open gap: forged-but-self-consistent evidence | **nobody**, see §3 |
| Usage/reconciliation API | Closes `docs/VERIFIABILITY_GAPS.md` G1 | **nobody at TypeSafe**; OpenAI and Anthropic both have usage endpoints |
| Model pinning | Thresholds tuned on `jev-1.13.0` do not transfer across versions | TypeSafe (versioned IDs), OpenAI (snapshots), Anthropic (dated models) |

---

## 3. Attestation: nobody ships it, and that is the honest answer

Our one irreducible gap — an agent forging self-consistent stability evidence —
needs the vendor to sign what it actually returned. Current state of the art:

- **IETF drafts, not standards.** `draft-chueayen-attestation-receipts-02`
  (expires Feb 2027) defines Ed25519 receipts binding an outcome to a request
  hash. `AEX` (arXiv 2603.14283) proposes a non-intrusive signed attestation
  object for existing JSON LLM APIs. Both are proposals with reference
  prototypes, not vendor-shipped features.
- **ZK inference proofs are real but slow.** DeepProve verifies full LLM
  inference at 86–174 tokens/minute. Our workload is ~99 ms per call. Three
  orders of magnitude apart.
- **TEE attestation** (`I-D.tsyrulnikov-rats-attested-inference-receipt`)
  attests the *platform*, not that a specific probability was genuinely
  computed.

**Conclusion:** no vendor closes this gap today. Choosing a vendor on the
promise of attestation would be choosing on a roadmap. The right move is to
keep `Q > 2f` carrying the weight — which is exactly what it was derived for —
and ask vendors for receipts as a stated requirement.

Also worth knowing: **OpenAI's `seed` does not give determinism either.** Their
own community threads state all current models are non-deterministic and return
different logprobs for identical input. So the jitter we measured is not a
TypeSafe quirk — it is the category. Our stability gate is not a workaround for
one vendor's weakness; it is the correct design for any of them.

---

## 4. Recommendation

**Primary: TypeSafe / Jev.** Not from loyalty — from measurement. It is the
only vendor evaluated that returns calibrated probabilities as the product
rather than as a byproduct, and our entire stability gate is built on that
number. Measured: 0.979 accuracy on constructed ground truth, 0.900 on real
FedRAMP obligation classification, Brier 0.0187, flat latency to 38 questions,
89.6% cheaper batched.

**Diversify the quorum, not the primary.** Given the correlation result, a
5-agent quorum on one vendor is theatre. Two realistic designs:

1. **Prompt-diverse, single vendor** (cheap, available now). Five paraphrases,
   one batched call, ~19 tokens each. Measured 8x decorrelation on hard cases.
   This is the default and should ship first.
2. **Vendor-diverse quorum** (real independence, higher cost). Jev for the
   calibrated primary vote, plus a second provider for a minority of the
   quorum. This is the only configuration where `f=1` means anything against a
   systematic oracle fault, because the second vendor's failure modes are
   genuinely uncorrelated.

For design 2 the second vendor should be chosen for *difference*, not quality —
a different training lineage and a different failure profile. A vendor that
agrees with Jev on everything adds cost and no information.

**Carry these as procurement requirements** for whoever we add:

- signed response receipts (AEX-style or IETF attestation-receipts profile)
- a read-only usage API for reconciliation
- pinned model versions with a deprecation window
- published calibration data, or enough access to measure it ourselves

---

## 5. What would change this recommendation

Stated up front so the decision is falsifiable:

- A vendor shipping **signed receipts** would close our last gap and justify
  moving the primary, even at some accuracy cost.
- If **paraphrase decorrelation** fails to hold on our production question
  distribution, design 1 is insufficient and design 2 becomes mandatory
  rather than optional.
- If TypeSafe's **rate limits** (currently "adjusting dynamically" per their
  own docs) prove unstable under our fan-out, single-vendor dependence becomes
  an availability risk independent of correctness.

Reproduce the measurement: `source env.sh && ./.venv/bin/python correlation.py`
