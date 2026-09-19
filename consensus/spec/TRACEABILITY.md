# Traceability: TLA+ spec → AsyncAPI contract

Every element of `api/asyncapi.yaml` derives from `spec/SemanticConsensusV2.tla`.
This table exists so that a reviewer can check the wire protocol against the
verified model rather than trusting that they agree.

Run `spec/check.sh` to re-verify the spec; run
`npx @asyncapi/cli@6.1.0 validate api/asyncapi.yaml` to re-validate the contract.

## Actions → operations

| TLA+ action | AsyncAPI operation | Channel | Notes |
|---|---|---|---|
| `Consult(a)` | `requestJudgment` | `consensus.{roundId}.consult` | First oracle call; `attempt = 1` |
| `Reconsult(a)` | `requestJudgment` | `consensus.{roundId}.consult` | Enabled only when `~stable[a]`; `attempt > 1` |
| — (oracle reply) | `receiveJudgment` | `consensus.{roundId}.consult` | Carries the stability classification |
| post-state of `Consult` | `announceVote` | `consensus.{roundId}.vote` | Publishes `vote[a]` **and** `stable[a]` |
| `Crash(a)` | — | `consensus.{roundId}.liveness` | `CrashNotice`; removes `a` from `Live` |
| `Decide` | `publishOutcome` | `consensus.{roundId}.outcome` | `Decision` message |
| `Escalate` | `publishOutcome` | `consensus.{roundId}.outcome` | `Escalation` message |

## Variables → payload fields

| TLA+ variable | Type in spec | Wire field | Message |
|---|---|---|---|
| `vote[a]` | `Verdicts ∪ {abstain}` | `verdict` | `VoteAnnounce`, `ConsultResult` |
| `stable[a]` | `BOOLEAN` | `stable`, `evidence.stable` | `VoteAnnounce` |
| `asked[a]` | `0..MaxAsks` | `attempt` | `ConsultRequest`, `VoteAnnounce` |
| `crashed[a]` | `BOOLEAN` | presence of `CrashNotice` | `CrashNotice` |
| `decided` | `Outcomes` | `outcome` | `Decision`, `Escalation` |
| `Quorum` | constant | `quorum` | `Decision` |
| `MaxAsks` | constant | bound on `attempt` | `ConsultRequest` |

## Invariants → wire-level obligations

Each verified property imposes a constraint the implementation must honour.
The contract is shaped so that a **receiver can check these independently**
rather than trusting the coordinator.

### `DecisionIsReproducible`

```tla
decided \in Verdicts => Cardinality(StableVotesFor(decided)) >= Quorum
```

`Decision.supportingVotes` carries the votes that justified the decision.
Every entry must have `stable = true`, and the array length must be at least
`quorum`. A receiver verifies the invariant from the message alone.

**If violated:** the kernel commits a verdict that the same agents, re-asked,
would not support. TLC finds this in 4 states when the stability gate is
removed (`MCNaive`).

### `EscalationIsJustified`

```tla
decided = UNCERTAIN => NoStableQuorum
```

`Escalation.observedVotes` carries *every* vote seen, stable or not, so a
reader can confirm no stable quorum existed.

### `EventuallyDecided`

```tla
<>(decided # ABSTAIN)
```

Guaranteed by two contract features acting together: `attempt` is bounded by
`MaxAsks`, and `Escalation` is a permitted terminal message. Remove either and
the livelock returns — TLC reaches `State 9: Stuttering` in `MCNoEscalate`.

### `Irrevocable`

Exactly one message is published to `consensus.{roundId}.outcome` per round.
The transport must not redeliver it as a *new* outcome; duplicate delivery of
the same `roundId` outcome is idempotent and must be dropped by receivers.

## The one field that is not a model parameter

`StabilityEvidence.noiseFloor` is **empirical**. It comes from measuring the
oracle's variation under a no-op transform, not from configuration:

| transform class | measured threshold |
|---|---|
| identity (same request re-sent) | 0.042 |
| question reorder | 0.059 |
| semantic restatement | 0.073 |

Source: 1,406 captured oracle calls, this repository's `forensics.db`.

A vote is `stable` only when `margin > noiseFloor` for its transform class.
Hardcoding a guess here voids the verification: the TLA+ proof assumes
`stable` means "would not flip on re-ask", and that meaning is only true if
the threshold reflects the oracle's actual jitter.

`noiseFloorSource` records which calibration run produced the number, so any
decision traces back to the measurement that justified it.

## Gaps: closed, and open

Kept honest and current. A gap is "closed" only when a model check and a test
both cover it.

### Closed

| gap | how | evidence |
|---|---|---|
| Irreproducible decision | stability gate on quorum | `MCNaive` fails, `MCStable` passes; `tlc_counterexample_is_rejected` |
| Jitter livelock | bounded ask budget + `UNCERTAIN` outcome | `MCNoEscalate` fails, `MCEscalate` passes; `livelock_scenario_terminates_via_escalation` |
| Crash faults | crashed agents leave `Live` | `MCEscalate` with `MaxCrashes`; `crashed_agent_is_excluded_from_quorum` |
| **Byzantine agents** | `Q > 2f`, derived by sweep | `MCByzantine`; `forged_evidence_*`, `unsound_quorum_is_rejected_at_construction` |
| **Message loss** | quorum on *observed* votes; loss degrades to escalation | `SemanticConsensusV3` `Deliver`; `lost_votes_degrade_to_escalation_not_to_a_weak_decision` |
| **Concurrent coordinators** | `2Q > N` so quorums intersect | `MCSplitBrain` fails, `MCIntersect` passes; `two_coordinators_with_split_views_cannot_diverge` |
| **Coordinator crash** | successor decides from a subset; intersection forbids contradiction | `successor_coordinator_cannot_contradict_its_predecessor` |
| **Liveness under Byzantine faults** | monotonicity: forgers can only add to a claimed set | `MCByzLive` (138,129 states), `MCVacuity` proves non-vacuity; `forgers_cannot_force_escalation_against_an_honest_quorum` |

The quorum bound was **derived, not assumed**. `spec/sweep_quorum.sh` runs TLC
across 22 `(N, f, Q)` configurations and confirms cell by cell:

```
safe iff 2Q > N and Q > 2f
```

`QuorumPolicy::new` is the only constructor for a quorum, so an unsound
configuration cannot reach the coordinator.

### Two results worth stating plainly

**Forgers cannot damage availability.** An honest agent's claim equals its
truth, so the claimed-stable set always contains the honest-stable set. A
forger can only *add* to a claimed set, never subtract. An honest quorum
therefore always surfaces as a claimed quorum, escalation stays disabled, and
the round decides. Checked at N=5/f=1/Q=3, at the tight N=4/f=1/Q=3, and even
at the deliberately unsound N=3/f=1/Q=2.

The consequence for operators: `Q > 2f` buys **correctness, not availability**.
There is no availability argument for weakening it, which removes the usual
temptation.

**A liveness cliff at `N < 3f+1`.** Safety forces `Q >= 2f+1`, but only `N-f`
agents are honest. When `N-f < Q` the deployment is still safe — no wrong
decision is possible — yet a *silent* adversary can stall every round, because
no quorum forms without its cooperation. That reduces to the classical
Byzantine threshold `N >= 3f+1`, which falls out of our derived bound rather
than being assumed. `QuorumPolicy::honest_can_decide()` reports it; it is not
enforced, because a stallable configuration is a legitimate choice when `f` is
a worst case you do not expect to hit.

### Open, with reasons

- **Forged-but-self-consistent evidence is undetectable locally.** An agent
  that fabricates a plausible probability and noise floor passes recomputation.
  Soundness therefore rests on `Q > 2f`, not on catching the liar. Closing this
  further needs an attestation channel the oracle does not offer — there is no
  usage or signing API (see `docs/VERIFIABILITY_GAPS.md` G1).
- **Multiple concurrent rounds.** `roundId` exists throughout and votes are
  rejected across rounds (`round_id_mismatch_is_caught`), but the spec models
  one round at a time. Cross-round interference is unverified.
- **Network transport.** The contract specifies NATS; the kernel calls agents
  in-process. Delivery is modelled in V3 as an explicit `Deliver` action, so
  the *protocol* tolerates loss, but no wire implementation exists yet.
