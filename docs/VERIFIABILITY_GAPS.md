# Verifiability gaps in the TypeSafe API

Findings from an independent evaluation, 2026-09-18. Every number below is
reproducible from `forensics.db` in this repository; each captured call carries
TypeSafe's own `x-typesafe-request-id` and a SHA-256 of the verbatim response.

Evidence base: 1,406+ captured calls, 521k input tokens, $0.022 at list price.
Integrity re-verified on every response body, zero mismatches.

---

## G1. No usage or account API (unresolvable from outside)

`/v1/systemone` and `/v1/models` are the only endpoints that exist. All of the
following return 404:

    /v1/usage  /v1/account  /v1/me  /v1/keys  /v1/organization

**Consequence.** A customer cannot reconcile their own records against
TypeSafe's billing by any programmatic means. We hold 1,406 hash-verified call
records with server-issued request IDs and no endpoint to check them against.
The only reconciliation path is a human reading a dashboard.

This gap **cannot be closed by the customer**. It is listed here because it is
the one finding in this document that requires action from TypeSafe rather than
from us.

**Suggested fix.** A read-only `GET /v1/usage?start=&end=` returning input
tokens, request count, and ideally per-request-id rows. For a company selling
auditable, machine-native infrastructure, usage that is only visible to humans
through a web page is off-thesis.

**Related symptom.** A real account with 1,406 successful calls displayed no
visible usage on the dashboard during the session in which those calls were
made. Cause not determined from outside; candidates are sub-cent rounding
($0.0219 total), UTC/local date bucketing (all calls fell on 2026-09-19 UTC but
2026-09-18 PDT), or aggregation lag.

---

## G2. The per-call fixed overhead is undocumented

Measured under controlled conditions (`meter_audit.py`), then confirmed across
a 2,500x payload range:

    tokens = 0.15127 * payload_chars + 280.7
    residual stdev: 0.0 tokens over the fit range
    prediction error at 121,904 chars: 1.1 tokens

**Every call carries ~281 input tokens of fixed cost** before any content. The
smallest possible billable call is 277 tokens. That overhead is equivalent to
~1,856 characters of state.

**Why it matters.** It dominates the bill for small states:

| state size | overhead share of the bill |
|---|---|
| 200 chars | 90.3% |
| 1,000 chars | 65.0% |
| 5,000 chars | 27.1% |
| 20,000 chars | 8.5% |

An integrator sizing a high-volume, small-state workload (routing short
messages, scoring individual passages) will mis-model their cost by an order of
magnitude if they assume billing tracks their content. The docs state the price
per token and that batching is cheaper, but never publish the constant.

**Suggested fix.** Document the fixed per-request token overhead on the Models
or Pricing page.

---

## G3. The batching saving is real but unquantified in the docs

State is billed **once per call**, not once per question. Measured with state
held fixed while question count varied:

| questions | input tokens |
|---|---|
| 1 | 439 |
| 2 | 458 |
| 4 | 496 |
| 8 | 572 |
| 16 | 730 |

Marginal cost per additional question: **19.4 tokens**. Cost of asking that
same question in a separate call: **277 tokens** (the floor).

    16 questions batched   =   730 tokens
    16 questions separately = 7,024 tokens
    saving                  = 89.6%

Combined with a measured server-side latency that is flat in question count
(1 question 98.9ms upstream, 38 questions 98.0ms, n=731 observational calls),
**speculative fan-out is close to free**. The docs describe this qualitatively
("batching is cheaper", "adding questions barely changes response time"); the
measurements say the latency cost is indistinguishable from zero and the token
cost is ~7% of a separate call.

**Suggested fix.** Publish the marginal-question cost. It converts a vague
recommendation into a design rule: batch everything that shares a state.

---

## G4. The documented context limit is not enforced

The Models page states a 32k-token budget for `state` plus the longest single
question, within a 64k total.

A single call with a 121,904-character state — **18,720 input tokens by
TypeSafe's own meter** — was accepted and answered normally, with no error, no
warning, and no truncation signal in the response.

**It is not being silently truncated.** A needle planted at the very *end* of
an oversized state was retrieved correctly:

| state | tokens | needle found |
|---|---|---|
| 1,053 chars (in limit) | 488 | 0.99 |
| 30,565 chars (~at limit) | 4,952 | 0.99 |
| 121,957 chars (~6x over) | 18,776 | 0.99 |

So the tail of a 122k-character state is genuinely read. That rules out the
correctness risk and leaves the simpler reading: **the documented limit is
stale or conservative, and the real capacity is substantially higher.**

**Why it still matters.** An integrator reading the Models page will
pre-chunk state at 32k tokens, adding engineering complexity and extra calls
(at 277 tokens of overhead each) to respect a bound that is not enforced.
Conversely, anyone relying on the limit as a guardrail has none: there is no
`422`, so a runaway payload is billed rather than rejected.

**Suggested fix.** Either publish the real bound, or enforce the documented one
with a `422`. Silent acceptance well past a published limit means the docs and
the service disagree, and the caller has no way to tell which is right.

---

## What we verified rather than assumed

The meter itself is sound. This is stated as a positive finding because it is
the part of billing a customer *can* audit:

- **Deterministic.** 6 identical requests returned 291 tokens every time. Across
  the whole capture store, 110 distinct repeated request hashes showed **zero**
  variation in billed tokens.
- **Linear.** No superlinear growth; residual stdev 0.0 tokens over the fit
  range and ~1 token at 121k chars.
- **Honest about state reuse.** 16 questions cost 1.66x a single-question call,
  not 16x, confirming the documented parallel-evaluation model.

So the per-call meter can be trusted. What cannot be checked is whether the sum
of those calls is what appears on the invoice — that is G1, and only TypeSafe
can fix it.

---

## Reproducing

    source env.sh
    ./.venv/bin/python meter_audit.py        # G2, G3, and the meter properties
    ./.venv/bin/python forensic_report.py    # integrity + capture summary

Raw evidence: `meter_audit.json`, `forensics.db` (tables `calls`, `answers`,
`observations`; views `v_repeats`, `v_run_cost`).
