"""Build the visual story's data spine from real telemetry.

Every number the story shows must come from a captured round. This script
extracts the specific moments the narrative needs -- not aggregates invented to
sound good, but individual rounds with their seeds, probabilities and the
oracle's own request ids, so any frame on screen can be traced to a call that
actually happened.

Output: story/story_data.json

Run:
    ./.venv/bin/python build_story_data.py
"""

from __future__ import annotations

import glob
import json
import math
import sqlite3
from pathlib import Path

OUT = Path("story/story_data.json")
NOISE_FLOOR = 0.042


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def load_runs() -> dict[str, list[dict]]:
    runs = {}
    for f in sorted(glob.glob("consensus/rust/consensus-kernel/sim_*.jsonl")):
        rows = [json.loads(l) for l in open(f) if l.strip()]
        if rows:
            runs[rows[0]["chaos"]] = rows
    return runs


def main() -> None:
    runs = load_runs()
    if not runs:
        raise SystemExit("no sim_*.jsonl telemetry found")
    all_rows = [r for rows in runs.values() for r in rows]

    story: dict = {"generated_from": {k: len(v) for k, v in runs.items()}}

    # ---- Act I: the problem -------------------------------------------
    # Non-determinism, measured. Pulled from the forensic DB rather than
    # re-asserted, so the story's opening claim is backed by stored calls.
    db = sqlite3.connect("forensics.db")
    n_calls = db.execute("select count(*) from calls").fetchone()[0]
    n_answers = db.execute("select count(*) from answers").fetchone()[0]
    story["forensics"] = {
        "captured_calls": n_calls,
        "captured_answers": n_answers,
        "note": "every call hash-verified, stored verbatim",
    }

    # ---- Act II: the kernel under chaos --------------------------------
    safety = {}
    for chaos, rows in runs.items():
        g = [r for r in rows if r["tier"] == "Golden"]
        wrong = sum(1 for r in g if r["safety_violation"])
        ok = sum(1 for r in g if r["correct"] is True)
        esc = sum(1 for r in g if r["escalated"])
        safety[chaos] = {
            "golden_rounds": len(g),
            "correct": ok,
            "escalated": esc,
            "violations": wrong,
            "accuracy_ci": wilson(ok, len(g)),
        }
    story["safety_by_chaos"] = safety

    total_golden = sum(s["golden_rounds"] for s in safety.values())
    total_violations = sum(s["violations"] for s in safety.values())
    story["headline"] = {
        "total_rounds": len(all_rows),
        "total_golden": total_golden,
        "total_violations": total_violations,
        # Rule of three: 0 events in n trials => 95% upper bound ~3/n.
        "violation_rate_upper_95": round(3 / total_golden, 5) if total_golden else None,
        "total_votes": sum(len(r["votes"]) for r in all_rows),
        "total_oracle_calls": sum(len(r["oracle_request_ids"]) for r in all_rows),
    }

    # ---- The stability gate, as a scatter ------------------------------
    votes = [v for r in all_rows for v in r["votes"]]
    unstable = [v for v in votes if not v["stable"]]
    stable = [v for v in votes if v["stable"]]
    story["gate"] = {
        "total_votes": len(votes),
        "stable": len(stable),
        "excluded": len(unstable),
        "max_excluded_margin": round(max((v["margin"] for v in unstable), default=0), 4),
        "min_included_margin": round(min((v["margin"] for v in stable), default=0), 4),
        "noise_floor": NOISE_FLOOR,
        # A thinned sample for plotting: full set is too dense to render.
        "sample": [
            {"m": round(v["margin"], 3), "s": v["stable"]}
            for v in votes[:: max(1, len(votes) // 400)]
        ],
    }

    # ---- Act III: named moments ----------------------------------------
    moments = {}

    # A clean decision with provenance.
    clean = [
        r for r in all_rows
        if r["tier"] == "Golden" and r["correct"] is True
        and r["chaos"] == "none" and len(r["oracle_request_ids"]) >= 3
    ]
    if clean:
        r = clean[0]
        moments["clean_decision"] = {
            "scenario": r["scenario"],
            "verdict": r["verdict"],
            "votes": [
                {"agent": v["agent"], "p": v["probability"], "margin": v["margin"]}
                for v in r["votes"]
            ],
            "request_ids": r["oracle_request_ids"][:3],
            "elapsed_ms": r["elapsed_ms"],
        }

    # The kernel declining a trivial question because chaos degraded evidence.
    declined = [
        r for r in all_rows
        if r["tier"] == "Golden" and r["escalated"] and r["chaos"] != "none"
    ]
    if declined:
        r = sorted(declined, key=lambda x: -len(x["votes"]))[0]
        moments["declined_under_chaos"] = {
            "scenario": r["scenario"],
            "seed": r["seed"],
            "chaos": r["chaos"],
            "expected": r["expected"],
            "votes": [
                {"agent": v["agent"], "p": v["probability"],
                 "margin": v["margin"], "stable": v["stable"]}
                for v in r["votes"]
            ],
            "oracle_failures": r["oracle_failures"],
            "replay": f"--base-seed {r['seed']} --seeds 1 --chaos {r['chaos']}",
        }

    # Degraded availability that still produced a sound decision.
    degraded = [
        r for r in all_rows
        if r.get("oracle_failures") and r["outcome"] == "decided"
        and r["tier"] == "Golden" and r["correct"] is True
    ]
    if degraded:
        r = sorted(degraded, key=lambda x: -len(x["oracle_failures"]))[0]
        moments["degraded_but_decided"] = {
            "scenario": r["scenario"],
            "seed": r["seed"],
            "chaos": r["chaos"],
            "verdict": r["verdict"],
            "lost_agents": r["oracle_failures"],
            "surviving_votes": [
                {"agent": v["agent"], "p": v["probability"], "margin": v["margin"]}
                for v in r["votes"]
            ],
        }

    # An ambiguous case correctly escalated.
    amb = [r for r in all_rows if r["tier"] == "Ambiguous" and r["escalated"]]
    if amb:
        r = amb[0]
        moments["ambiguous_escalated"] = {
            "scenario": r["scenario"],
            "seed": r["seed"],
            "votes": [
                {"agent": v["agent"], "p": v["probability"],
                 "margin": v["margin"], "stable": v["stable"]}
                for v in r["votes"]
            ],
        }
    # ---- The one we got wrong, and the one Jev can't resolve ------------
    # Both live in the Ambiguous tier of THIS run. One turned out to be a
    # mislabel on our side (Jev was right); the other is a real limitation.
    def agg(scn):
        rs=[r for r in all_rows if r["scenario"]==scn]
        if not rs: return None
        import collections as _c
        ps=[v["probability"] for r in rs for v in r["votes"]]
        return {"n":len(rs),"escalated":sum(1 for r in rs if r["escalated"]),
                "verdicts":dict(_c.Counter(str(r["verdict"]) for r in rs)),
                "p_mean":round(sum(ps)/len(ps),3),"p_min":round(min(ps),3),"p_max":round(max(ps),3)}
    story["honest"] = {
        "mislabelled_by_us": {"scenario":"ambiguous.pregnancy.unclear",
            "what_we_asked":"Is pregnancy ruled out? (late period, declined testing, isotretinoin ordered)",
            "what_we_labelled":"Escalate", "what_jev_said":"No, consistently",
            "verdict_on_us":"Jev was right. 'Not ruled out' is the correct answer and is the trigger for the hold. Relabelled to nuanced.pregnancy.not_ruled_out, expected No.",
            "data":agg("ambiguous.pregnancy.unclear")},
        "real_limitation": {"scenario":"ambiguous.allergy.vague_note",
            "record":"'reaction to antibiotics as a child, details unknown, mother reported stomach upset' + amoxicillin",
            "verdict_on_jev":"Genuinely underdetermined. It escalated most of the time, but decided in a minority of rounds and split both ways when it did. The stability gate is not a substitute for an answerability check.",
            "data":agg("ambiguous.allergy.vague_note")},
    }
    story["moments"] = moments

    # ---- The paraphrase audit: a bug we found in ourselves --------------
    try:
        audit = json.load(open("paraphrase_audit.json"))
        story["paraphrase_audit"] = {
            "violations_after_fix": len(audit.get("violations", [])),
            "sets_checked": list(audit.get("sets", {}).keys()),
        }
    except FileNotFoundError:
        pass

    # ---- Escalation rates, with intervals ------------------------------
    esc = {}
    for chaos, rows in runs.items():
        k = sum(1 for r in rows if r["escalated"])
        esc[chaos] = {"n": len(rows), "escalated": k,
                      "rate": round(k / len(rows), 4), "ci": wilson(k, len(rows))}
    story["escalation_by_chaos"] = esc

    # ---- Per-scenario, for the detail panel ----------------------------
    by: dict[str, dict] = {}
    for r in all_rows:
        d = by.setdefault(r["scenario"], {"n": 0, "decided": 0, "escalated": 0,
                                          "wrong": 0, "tier": r["tier"]})
        d["n"] += 1
        d["decided"] += r["outcome"] == "decided"
        d["escalated"] += bool(r["escalated"])
        d["wrong"] += bool(r["correct"] is False and r["verdict"])
    story["scenarios"] = by

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(story, indent=1))
    print(f"wrote {OUT}")
    print(f"  rounds      : {story['headline']['total_rounds']}")
    print(f"  golden      : {total_golden}")
    print(f"  violations  : {total_violations}")
    print(f"  votes       : {story['headline']['total_votes']}")
    print(f"  oracle calls: {story['headline']['total_oracle_calls']}")
    print(f"  moments     : {list(moments.keys())}")


if __name__ == "__main__":
    main()
