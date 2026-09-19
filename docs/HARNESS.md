# jev-labs

A metamorphic property-testing harness for TypeSafe's Jev (System One) API,
plus a forensic capture layer that makes every finding reproducible.

Built on Antithesis's premise — *autonomous verification, not automated review;
tell me **why**, not just **what*** — adapted for a system under test that is
not deterministic.

## The central problem

Antithesis gets its power from perfect determinism: control the scheduler, and
any bug can be rewound and replayed exactly. Jev offers no such control. The
identical request, re-sent, returns different numbers:

```
mentions_consent, 5 identical requests -> 0.03, 0.03, 0.03, 0.04, 0.04
```

A property checker built on exact comparison reports that jitter as failure and
is worthless. So the determinism assumption is replaced, not abandoned:

| Antithesis | here |
|---|---|
| deterministic scheduler | **empirically measured noise floor** |
| exact replay | **seed-replayable inputs** + statistical gating |
| boolean assertion | **continuous effect size** |
| single failing trace | **confirmation re-runs** (N/N reproducibility) |
| minimized trace | **greedy case shrinking** |

The input side stays fully deterministic — every case regenerates from
`property:seed`. Only the oracle becomes statistical.

## Noise-floor calibration

Before any property runs, the harness measures how much the system varies when
**nothing has changed**. Three separate floors, because they are not the same
number:

- `identity` — byte-identical request, re-sent. Pure sampling noise.
- `reorder` — same questions, different dict order. Should be zero by the API's
  own contract.
- `cohort` — semantically identical restatements (whitespace, punctuation).
  Necessarily larger.

Each property is judged against the floor matching the transform it applies.
Judging a paraphrase property against the identity floor manufactures false
positives — that mistake is the usual reason ML test harnesses get ignored.

Threshold = `max_observed_deviation + 3*stdev`, with an absolute minimum so a
quiet calibration run cannot make the harness hypersensitive.

Measured floors run **~0.042–0.073**, three to seven times the naive ±0.01
jitter estimate, because noise compounds across multi-call transforms.

## Pipeline

```
SEARCH   -> N generated cases, continuous effect per case
GATE     -> discard anything inside the matching noise floor
CONFIRM  -> re-run survivors K times; unreproducible = noise, not a defect
SHRINK   -> reduce to the minimal input that still violates
REPORT   -> invariant + minimal case + effect + floor + reproducibility + call ids
```

## The harness tests itself

A harness that reports HELD is worthless until you have shown it can report
VIOLATED. `harness/selftest.py` injects known-magnitude faults and requires the
harness to detect every fault above the floor, stay silent below it, and recover
the injected magnitude.

```
self-test: 8/8 checks correct
```

This caught two real bugs in the harness itself:

1. Biasing both halves of a comparison pair cancelled in the difference — the
   injected fault was invisible.
2. SDK answer objects are **frozen pydantic models**; in-place mutation raised
   `ValidationError` which a broad `except` swallowed, so the self-test silently
   passed a no-op. Fixed with `model_copy`, plus an assertion that the injection
   landed.

Without the self-test, both would have produced a confident, wrong report.

## Forensic capture

Every API call is persisted to SQLite (WAL) before any conclusion is drawn:

- verbatim request body and **verbatim response bytes** (not a re-serialization)
- SHA-256 of both — `verify_integrity()` re-hashes and proves nothing was altered
- TypeSafe's `request_id` for server-side correlation
- wall-clock latency separated from `x-envoy-upstream-service-time`
- failures captured at the same fidelity as successes
- request headers never stored (they carry the API key)

SQLite over RocksDB because every question worth asking is analytical — group by
question id, correlate confidence with correctness, detect drift across model
versions. That needs relational scans, not key lookups.

## Usage

```bash
source env.sh                                    # maps API_KEY -> TYPESAFE_API_KEY

./.venv/bin/python -m harness.runner --cases 8   # full run
./.venv/bin/python -m harness.selftest           # prove the harness works
./.venv/bin/python -m harness.report             # generate docs/FINDINGS.md

./.venv/bin/python -m harness.runner --property question_independence
./.venv/bin/python -m harness.runner --replay irrelevant_context_stability:5

./.venv/bin/python forensic_report.py            # query the capture store
```

## Properties

| property | severity | invariant |
|---|---|---|
| `question_independence` | hard | Adding unrelated questions must not move an existing answer |
| `choice_order_invariance` | hard | Reordering Choice options must not change the distribution |
| `irrelevant_context_stability` | semantic | Irrelevant text must not move a judgment |
| `score_monotonicity` | calibration | Intensifying language must not lower an urgency Score |
| `choice_noul_agreement` | documented | Same judgment, Noul vs binary Choice |
| `injection_resistance` | semantic | Text in state must not steer the judgment |

Severity reflects what kind of guarantee breaks — `hard` is the API's own
documented contract, `documented` is published jaggedness being quantified
rather than discovered.

## Layout

```
forensics.py          capture layer (schema, hashing, integrity, drift views)
forensic_report.py    queries over the store
harness/
  core.py             Case / Measurement / Property / Violation
  noise.py            noise-floor calibration
  properties.py       the metamorphic relations
  runner.py           search -> gate -> confirm -> shrink -> report
  selftest.py         fault injection (negative control)
  report.py           docs/FINDINGS.md generator
experiments.py        documented-claim verification
jaggedness.py         the 9 published failure modes vs ground truth
probes.py             follow-ups on precedence and adversarial shifts
```

## Extending

Add a property by appending a `Property` to `harness/properties.py` with a
`generate`, an `evaluate` returning a continuous effect, an optional `shrink`,
and an entry in `FLOOR_FOR` naming which noise floor judges it. Then add a
fault-injection case to `selftest.py` — an unfalsifiable property is not a test.
