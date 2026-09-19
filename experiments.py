"""Test TypeSafe's own documented claims against the live jev-1.13 API.

Each experiment targets a specific claim from docs.typesafe.ai and reports
whether the observed behavior matches. Run:

    source env.sh && ./.venv/bin/python experiments.py

Every API call is captured verbatim to forensics.db (see forensics.py) so any
number in the summary can be traced back to the exact request and response
bytes that produced it. results.json is a convenience summary, not the record
of truth -- the database is.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

from forensics import ForensicRecorder

RESULTS_PATH = Path(__file__).parent / "results.json"
DB_PATH = Path(__file__).parent / "forensics.db"

GDPR_BLURB = (
    "The General Data Protection Regulation (GDPR) is a regulation in EU law on "
    "information privacy in the European Union and the European Economic Area. "
    "It became enforceable on 25 May 2018. Controllers must implement appropriate "
    "technical and organisational measures, and report a personal data breach to "
    "the supervisory authority within 72 hours of becoming aware of it. Fines can "
    "reach 20 million euros or 4% of worldwide annual turnover, whichever is higher."
)

AMBIGUOUS_TICKET = "I'm not happy with the fit. What are my options here?"
DUPLICATE_TICKET = "I was charged twice for the same order. Can someone look into this?"


def timed(fn, *args, **kwargs) -> tuple[Any, float]:
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, (time.perf_counter() - start) * 1000


# --------------------------------------------------------------------------
# Claim 1: batching many questions into one call is far cheaper and faster
# than issuing one call per question, with no change in the answers.
# --------------------------------------------------------------------------
def experiment_batching(client: TypeSafeClient) -> dict[str, Any]:
    questions = {
        "is_regulation": Noul(instructions="Is the subject of this text a law or regulation?"),
        "mentions_deadline": Noul(instructions="Does this text state a reporting deadline?"),
        "mentions_fines": Noul(instructions="Does this text describe financial penalties?"),
        "mentions_scope": Noul(instructions="Does this text describe a geographic scope?"),
        "mentions_date": Noul(instructions="Does this text state a date it took effect?"),
        "mentions_controllers": Noul(instructions="Does this text place obligations on data controllers?"),
        "mentions_consent": Noul(instructions="Does this text discuss obtaining user consent?"),
        "mentions_dpo": Noul(instructions="Does this text mention a Data Protection Officer?"),
    }

    batched, batched_ms = timed(client.system_one, GDPR_BLURB, questions)
    batched_answers = {k: round(batched.nouls[k].noul, 4) for k in questions}
    batched_tokens = batched.usage.input_tokens

    serial_answers: dict[str, float] = {}
    serial_tokens = 0
    serial_start = time.perf_counter()
    for key, question in questions.items():
        resp = client.system_one(GDPR_BLURB, {key: question})
        serial_answers[key] = round(resp.nouls[key].noul, 4)
        serial_tokens += resp.usage.input_tokens
    serial_ms = (time.perf_counter() - serial_start) * 1000

    drift = {
        k: abs(batched_answers[k] - serial_answers[k])
        for k in questions
        if abs(batched_answers[k] - serial_answers[k]) > 1e-9
    }

    return {
        "claim": "batching is cheaper/faster with no change in answers",
        "n_questions": len(questions),
        "batched_ms": round(batched_ms),
        "serial_ms": round(serial_ms),
        "speedup": round(serial_ms / batched_ms, 2),
        "batched_input_tokens": batched_tokens,
        "serial_input_tokens": serial_tokens,
        "token_ratio": round(serial_tokens / batched_tokens, 2),
        "batched_answers": batched_answers,
        "serial_answers": serial_answers,
        "answer_drift": drift,
        "answers_identical": not drift,
    }


# --------------------------------------------------------------------------
# Claim 2 (jaggedness #8): structural invariants are NOT guaranteed.
# Same judgment as a Noul vs a yes/no Choice should disagree, and a Noul
# plus its own negation need not sum to 1.
# --------------------------------------------------------------------------
def experiment_invariants(client: TypeSafeClient) -> dict[str, Any]:
    resp = client.system_one(
        AMBIGUOUS_TICKET,
        {
            "refund_noul": Noul(instructions="Is the customer asking for a refund?"),
            "refund_choice": Choice(
                instructions="Is the customer asking for a refund?",
                criteria={"yes": None, "no": None},
            ),
        },
    )
    noul_v = resp.nouls["refund_noul"].noul
    choice = resp.choices["refund_choice"]

    neg = client.system_one(
        DUPLICATE_TICKET,
        {
            "refund": Noul(instructions="Is the customer asking for a refund?"),
            "not_refund": Noul(
                instructions="Is the customer asking for something other than a refund?"
            ),
        },
    )
    p_refund = neg.nouls["refund"].noul
    p_not = neg.nouls["not_refund"].noul

    return {
        "claim": "structural invariants are not guaranteed (docs predict divergence)",
        "primitive_disagreement": {
            "ticket": AMBIGUOUS_TICKET,
            "noul": round(noul_v, 4),
            "choice_yes": round(choice.probabilities["yes"], 4),
            "choice_no": round(choice.probabilities["no"], 4),
            "choice_confidence": round(choice.confidence, 4),
            "gap_noul_vs_choice_yes": round(abs(noul_v - choice.probabilities["yes"]), 4),
        },
        "negation_sum": {
            "ticket": DUPLICATE_TICKET,
            "refund": round(p_refund, 4),
            "not_refund": round(p_not, 4),
            "sum": round(p_refund + p_not, 4),
            "deviation_from_1": round(abs(p_refund + p_not - 1.0), 4),
        },
    }


# --------------------------------------------------------------------------
# Claim 3 (jaggedness #2): jev does not count reliably, and the recommended
# fix is one question per item, summed in code.
# --------------------------------------------------------------------------
def experiment_counting(client: TypeSafeClient) -> dict[str, Any]:
    items = [
        "typesafe", "apple", "california", "banana", "likes",
        "calibration", "orange", "vertex", "kiwi", "postgres",
    ]
    truth = {"apple", "banana", "orange", "kiwi"}
    expected = len(truth)

    direct = client.system_one(
        {"items": items},
        {
            "count": Choice(
                instructions="How many of the items in `items` are names of fruit?",
                criteria={str(i): None for i in range(len(items) + 1)},
            )
        },
    )
    direct_count = int(direct.choices["count"].choice)

    per_item = client.system_one(
        {"items": items},
        {
            f"item_{i}": Noul(instructions=f"Is `items[{i}]` the name of a fruit?")
            for i in range(len(items))
        },
    )
    flags = {items[i]: round(per_item.nouls[f"item_{i}"].noul, 4) for i in range(len(items))}
    decomposed_count = sum(v > 0.5 for v in flags.values())
    predicted = {k for k, v in flags.items() if v > 0.5}

    return {
        "claim": "counting in one question is unreliable; per-item decomposition works",
        "items": items,
        "ground_truth_count": expected,
        "direct_count": direct_count,
        "direct_correct": direct_count == expected,
        "direct_confidence": round(direct.choices["count"].confidence, 4),
        "decomposed_count": decomposed_count,
        "decomposed_correct": decomposed_count == expected,
        "per_item_nouls": flags,
        "misclassified": sorted(predicted ^ truth),
    }


# --------------------------------------------------------------------------
# Claim 4: confidence is a real signal -- it should drop on genuinely
# ambiguous input and stay high on unambiguous input.
# --------------------------------------------------------------------------
def experiment_confidence(client: TypeSafeClient) -> dict[str, Any]:
    cases = {
        "unambiguous_technical": "The API returns a 500 error on every POST to /v1/orders. Our integration is down.",
        "unambiguous_billing": "You charged my card $49 twice for invoice A-104. Please refund the duplicate.",
        "ambiguous_mixed": "My payouts are failing and I also want to know if upgrading would fix it.",
        "contentless": "Hi there, quick question when you get a moment. Thanks!",
    }
    dept = Choice(
        instructions="Which team should handle this ticket?",
        criteria={
            "billing": "Payment, invoicing, refunds, or subscription charges",
            "technical": "Bugs, outages, errors, or integration problems",
            "sales": "Pricing, upgrades, or new accounts",
        },
    )

    out = {}
    for name, text in cases.items():
        resp = client.system_one(text, {"department": dept})
        ans = resp.choices["department"]
        out[name] = {
            "choice": ans.choice,
            "confidence": round(ans.confidence, 4),
            "probabilities": {k: round(v, 4) for k, v in ans.probabilities.items()},
        }

    confs = {k: v["confidence"] for k, v in out.items()}
    clear = statistics.mean(
        [confs["unambiguous_technical"], confs["unambiguous_billing"]]
    )
    murky = statistics.mean([confs["ambiguous_mixed"], confs["contentless"]])

    return {
        "claim": "confidence drops on ambiguous input",
        "cases": out,
        "mean_confidence_clear": round(clear, 4),
        "mean_confidence_ambiguous": round(murky, 4),
        "separates_correctly": clear > murky,
    }


# --------------------------------------------------------------------------
# Claim 5: determinism. The docs claim batching produces "no change in
# answers", which presumes the model is deterministic for a fixed request.
# Issue the identical request N times and measure the spread. This also
# populates the v_repeats drift view in the forensic store.
# --------------------------------------------------------------------------
def experiment_determinism(client: TypeSafeClient, repeats: int = 5) -> dict[str, Any]:
    questions = {
        "is_regulation": Noul(instructions="Is the subject of this text a law or regulation?"),
        "mentions_consent": Noul(instructions="Does this text discuss obtaining user consent?"),
        "severity": Score(
            instructions="How severe are the penalties described?",
            criteria=["No penalties", "Moderate penalties", "Severe penalties"],
        ),
        "topic": Choice(
            instructions="What is the primary subject of this text?",
            criteria={
                "privacy_law": "Data protection or privacy regulation",
                "tax_law": "Taxation rules",
                "employment_law": "Labour or employment rules",
            },
        ),
    }

    samples: dict[str, list[float]] = {k: [] for k in questions}
    for _ in range(repeats):
        resp = client.system_one(GDPR_BLURB, questions)
        samples["is_regulation"].append(resp.nouls["is_regulation"].noul)
        samples["mentions_consent"].append(resp.nouls["mentions_consent"].noul)
        samples["severity"].append(resp.scores["severity"].score)
        samples["topic"].append(resp.choices["topic"].probabilities["privacy_law"])

    spreads = {k: round(max(v) - min(v), 6) for k, v in samples.items()}
    return {
        "claim": "identical requests return identical answers (determinism)",
        "repeats": repeats,
        "samples": {k: [round(x, 4) for x in v] for k, v in samples.items()},
        "spreads": spreads,
        "max_spread": max(spreads.values()),
        "fully_deterministic": all(s == 0 for s in spreads.values()),
    }


def main() -> None:
    results: dict[str, Any] = {}
    experiments = [
        ("batching", experiment_batching),
        ("invariants", experiment_invariants),
        ("counting", experiment_counting),
        ("confidence", experiment_confidence),
        ("determinism", experiment_determinism),
    ]

    with ForensicRecorder(DB_PATH) as rec:
        run_id = rec.start_run(
            "claim_verification",
            notes="Verifying documented TypeSafe claims against live jev-1.13",
        )
        with TypeSafeClient() as raw_client:
            client = rec.wrap(raw_client, run_id)
            for name, fn in experiments:
                print(f"running: {name} ...", flush=True)
                result = fn(client)
                results[name] = result
                # Record the finding alongside the calls that produced it.
                rec.observe(
                    run_id,
                    claim=result.get("claim", name),
                    verdict=_verdict(name, result),
                    detail=result,
                )
        rec.end_run(run_id)

        integrity = rec.verify_integrity()
        cost = [dict(r) for r in rec.query("SELECT * FROM v_run_cost WHERE run_id = ?", (run_id,))]

    results["_forensics"] = {
        "run_id": run_id,
        "db_path": str(DB_PATH),
        "integrity": integrity,
        "cost": cost,
    }

    RESULTS_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {RESULTS_PATH}")
    print(f"captured to {DB_PATH} (run {run_id})")
    print(f"integrity: {integrity}")
    for row in cost:
        print(f"cost: {row}")


def _verdict(name: str, result: dict[str, Any]) -> str:
    """Reduce an experiment result to a single verdict token."""
    if name == "batching":
        # Cost/latency and answer-stability are separate claims; report both.
        if not result["answers_identical"]:
            return "partial_answers_drifted"
        return "confirmed" if result["speedup"] > 1 else "refuted"
    if name == "determinism":
        return "confirmed" if result["fully_deterministic"] else "refuted"
    if name == "invariants":
        diverged = (
            result["primitive_disagreement"]["gap_noul_vs_choice_yes"] > 0.05
            or result["negation_sum"]["deviation_from_1"] > 0.05
        )
        return "confirmed" if diverged else "not_reproduced"
    if name == "counting":
        return "not_reproduced" if result["direct_correct"] else "confirmed"
    if name == "confidence":
        return "confirmed" if result["separates_correctly"] else "refuted"
    return "unknown"


if __name__ == "__main__":
    main()
