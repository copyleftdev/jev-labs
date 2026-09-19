"""Independent audit of TypeSafe's billing meter.

## What this can and cannot establish

TypeSafe exposes no usage API -- /v1/usage, /v1/account, /v1/me, /v1/keys and
/v1/organization all return 404. Only /v1/systemone and /v1/models exist. So
there is no way for a customer to reconcile their own records against TypeSafe's
billing. That gap cannot be closed from outside; only TypeSafe can close it by
shipping a read-only usage endpoint.

What we CAN do honestly is audit the meter itself. Every response carries
`usage.input_tokens`, and input tokens are the entire billing basis (output is
free). If that number is deterministic, linear in payload size, and free of
unexplained inflation, then the per-call meter is trustworthy even though the
aggregate remains unverifiable. If it is not, that is a billing defect worth
reporting with evidence.

This is deliberately NOT a claim that our token totals match their invoice. We
cannot see their invoice. It is a claim about the only billing signal they
actually expose to us.

## Properties tested

  M1 DETERMINISM   identical request => identical input_tokens, always.
                   A meter that varies on fixed input cannot be audited at all.

  M2 LINEARITY     tokens should be an affine function of payload size:
                   tokens = rate * chars + overhead. Superlinear growth would
                   mean large payloads are billed disproportionately.

  M3 OVERHEAD      the constant term is the per-call fixed cost. It is the
                   single most decision-relevant number for an integrator,
                   because it determines when batching pays. TypeSafe documents
                   that batching is cheaper but never publishes this figure.

  M4 ADDITIVITY    does the state get billed once per call, or once per
                   question? The docs say state is ingested once and every
                   question evaluated against it in parallel. If billing were
                   per-question-times-state, the cost model would be completely
                   different from the documented one.

  M5 NO PHANTOM    an empty-ish request should not be billed as though it
                   carried content beyond the measured overhead.

Run: source env.sh && ./.venv/bin/python meter_audit.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from typesafe_sdk import Noul, TypeSafeClient

from forensics import ForensicRecorder

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "forensics.db"
RESULTS = ROOT / "meter_audit.json"

FILLER = ("The quarterly compliance review covers access control, audit "
          "logging, incident response, and configuration management. ")


def fit_affine(pts: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least squares y = a*x + b; returns (a, b, residual_stdev)."""
    n = len(pts)
    sx = sum(x for x, _ in pts)
    sy = sum(y for _, y in pts)
    sxy = sum(x * y for x, y in pts)
    sxx = sum(x * x for x, _ in pts)
    denom = n * sxx - sx * sx
    a = (n * sxy - sx * sy) / denom if denom else 0.0
    b = (sy - a * sx) / n
    resid = [y - (a * x + b) for x, y in pts]
    return a, b, statistics.pstdev(resid) if len(resid) > 1 else 0.0


def m1_determinism(client: Any, repeats: int = 6) -> dict[str, Any]:
    state = {"ticket": "The customer reports a duplicate charge on invoice A-104."}
    q = {"billing": Noul(instructions="Is this about billing?")}
    counts = []
    for _ in range(repeats):
        counts.append(client.system_one(state, q).usage.input_tokens)
    return {
        "property": "M1 identical request bills identically",
        "repeats": repeats,
        "token_counts": counts,
        "distinct": len(set(counts)),
        "deterministic": len(set(counts)) == 1,
    }


def m2_m3_linearity(client: Any) -> dict[str, Any]:
    """Vary state size with the question held fixed."""
    q = {"billing": Noul(instructions="Is this about billing?")}
    pts, rows = [], []
    for mult in (0, 1, 2, 4, 8, 16, 32, 64):
        state = {"ticket": "Duplicate charge on invoice A-104. " + FILLER * mult}
        payload_chars = len(json.dumps(state, sort_keys=True, separators=(",", ":")))
        tokens = client.system_one(state, q).usage.input_tokens
        pts.append((float(payload_chars), float(tokens)))
        rows.append({"filler_mult": mult, "state_chars": payload_chars,
                     "input_tokens": tokens})

    rate, overhead, resid = fit_affine(pts)

    # Superlinearity check: compare the rate on the small half vs the large half.
    half = len(pts) // 2
    r_small, _, _ = fit_affine(pts[:half + 1])
    r_large, _, _ = fit_affine(pts[half:])

    return {
        "property": "M2/M3 tokens are affine in payload size",
        "measurements": rows,
        "chars_per_token": round(1 / rate, 3) if rate else None,
        "tokens_per_char": round(rate, 5),
        "fixed_overhead_tokens": round(overhead, 1),
        "residual_stdev_tokens": round(resid, 2),
        "rate_small_payloads": round(r_small, 5),
        "rate_large_payloads": round(r_large, 5),
        "superlinear": bool(rate and r_large > r_small * 1.25),
        "linear_within_tolerance": bool(resid < 15),
    }


def m4_additivity(client: Any) -> dict[str, Any]:
    """Hold state fixed, vary question count. Is state billed once or per question?"""
    state = {"ticket": "Duplicate charge on invoice A-104. " + FILLER * 8}
    state_chars = len(json.dumps(state, sort_keys=True, separators=(",", ":")))
    rows, pts = [], []
    base_q = "Does this message concern topic {} of an internal taxonomy?"
    for nq in (1, 2, 4, 8, 16):
        qs = {f"q{i}": Noul(instructions=base_q.format(i)) for i in range(nq)}
        tokens = client.system_one(state, qs).usage.input_tokens
        rows.append({"n_questions": nq, "input_tokens": tokens})
        pts.append((float(nq), float(tokens)))

    per_q, base, resid = fit_affine(pts)
    one_q = rows[0]["input_tokens"]
    sixteen_q = rows[-1]["input_tokens"]

    # If state were re-billed per question, 16 questions would cost roughly
    # 16x the single-question call. If state is billed once, the growth is
    # only the marginal cost of the extra question text.
    naive_per_question_total = one_q * 16

    return {
        "property": "M4 state billed once per call, not once per question",
        "state_chars": state_chars,
        "measurements": rows,
        "marginal_tokens_per_question": round(per_q, 2),
        "intercept_tokens": round(base, 1),
        "tokens_1q": one_q,
        "tokens_16q": sixteen_q,
        "actual_growth_factor": round(sixteen_q / one_q, 3),
        "growth_if_state_rebilled": 16.0,
        "state_billed_once": bool(sixteen_q < naive_per_question_total * 0.5),
        "savings_vs_16_separate_calls": naive_per_question_total - sixteen_q,
        "savings_pct": round(100 * (1 - sixteen_q / naive_per_question_total), 1),
    }


def m5_floor(client: Any) -> dict[str, Any]:
    """Smallest meaningful request -- what does an empty-ish call cost?"""
    cases = {
        "minimal": ({"a": "x"}, {"q": Noul(instructions="Is this x?")}),
        "short_string_state": ("x", {"q": Noul(instructions="Is this x?")}),
        "typical_small": ({"ticket": "Refund please."},
                          {"q": Noul(instructions="Is the customer requesting a refund?")}),
    }
    rows = {}
    for name, (state, q) in cases.items():
        payload = len(json.dumps(state, sort_keys=True, separators=(",", ":")))
        tokens = client.system_one(state, q).usage.input_tokens
        rows[name] = {"payload_chars": payload, "input_tokens": tokens,
                      "cost_usd_at_list": round(tokens * 42 / 1e9, 9)}
    return {"property": "M5 minimum billable call", "cases": rows}


def main() -> None:
    out: dict[str, Any] = {}
    with ForensicRecorder(DB_PATH) as rec:
        run_id = rec.start_run("meter_audit",
                               notes="Independent audit of the input-token billing meter")
        with TypeSafeClient() as raw:
            client = rec.wrap(raw, run_id)
            print("M1 determinism ...")
            out["m1_determinism"] = m1_determinism(client)
            print("M2/M3 linearity and overhead ...")
            out["m2_m3_linearity"] = m2_m3_linearity(client)
            print("M4 additivity ...")
            out["m4_additivity"] = m4_additivity(client)
            print("M5 floor ...")
            out["m5_floor"] = m5_floor(client)
            for v in out.values():
                rec.observe(run_id, claim=v["property"], verdict="measured", detail=v)
        rec.end_run(run_id)
        out["_forensics"] = {"run_id": run_id, "integrity": rec.verify_integrity()}

    RESULTS.write_text(json.dumps(out, indent=2))

    m1, m23, m4, m5 = (out["m1_determinism"], out["m2_m3_linearity"],
                       out["m4_additivity"], out["m5_floor"])
    print("\n" + "=" * 74)
    print(f"M1 deterministic metering : {m1['deterministic']}  {m1['token_counts']}")
    print(f"M2 linear in payload      : {m23['linear_within_tolerance']} "
          f"(residual stdev {m23['residual_stdev_tokens']} tok)")
    print(f"   chars per token        : {m23['chars_per_token']}")
    print(f"M3 fixed overhead per call: {m23['fixed_overhead_tokens']} tokens")
    print(f"M4 state billed once      : {m4['state_billed_once']}  "
          f"(16q costs {m4['actual_growth_factor']}x a 1q call, not 16x)")
    print(f"   batching saves         : {m4['savings_pct']}% vs 16 separate calls")
    print(f"M5 minimum billable call  : "
          f"{m5['cases']['minimal']['input_tokens']} tokens")
    print("=" * 74)
    print(f"wrote {RESULTS}")


if __name__ == "__main__":
    main()
