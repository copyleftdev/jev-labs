"""Forensic queries over the captured TypeSafe interaction store.

    ./.venv/bin/python forensic_report.py [--db forensics.db]

Answers the questions the store exists to answer: what did we ask, what came
back, is the record intact, and did identical requests produce identical
answers.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from forensics import ForensicRecorder


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).parent / "forensics.db"))
    args = ap.parse_args()

    rec = ForensicRecorder(args.db)
    try:
        section("Integrity")
        integrity = rec.verify_integrity()
        print(f"response bodies checked: {integrity['checked']}")
        print(f"hash mismatches:         {len(integrity['mismatches'])}")
        print(f"record intact:           {integrity['intact']}")

        section("Runs")
        for r in rec.query("SELECT * FROM v_run_cost ORDER BY run_id"):
            print(
                f"{r['run_id']}  {r['label']:20} calls={r['calls']:<4} "
                f"questions={r['questions']:<4} in_tok={r['input_tokens']:<7} "
                f"mean={r['mean_latency_ms']}ms upstream={r['mean_upstream_ms']}ms "
                f"failures={r['failures']}"
            )

        section("Cost at list price ($42 / Btok input, output free)")
        row = rec.query("SELECT SUM(input_tokens) AS t FROM calls")[0]
        total = row["t"] or 0
        print(f"input tokens billed: {total:,}")
        print(f"cost so far:         ${total * 42 / 1e9:.8f}")

        section("Answer distribution by question type")
        for r in rec.query(
            """SELECT question_type, COUNT(*) n,
                      ROUND(AVG(confidence), 4) mean_conf,
                      ROUND(MIN(confidence), 4) min_conf
               FROM answers GROUP BY question_type"""
        ):
            print(f"{r['question_type']:8} n={r['n']:<4} mean_conf={r['mean_conf']} min_conf={r['min_conf']}")

        section("Low-confidence answers (conf < 0.5) -- would be gated in production")
        rows = rec.query(
            """SELECT a.question_id, a.question_type, a.choice, a.confidence,
                      a.probabilities, c.request_id
               FROM answers a JOIN calls c ON c.call_id = a.call_id
               WHERE a.confidence IS NOT NULL AND a.confidence < 0.5
               ORDER BY a.confidence"""
        )
        if not rows:
            print("(none)")
        for r in rows:
            print(
                f"{r['question_id']:12} {r['question_type']:7} choice={r['choice']!s:12} "
                f"conf={r['confidence']:.3f}  {r['probabilities']}  {r['request_id']}"
            )

        section("Repeated identical requests -- drift / non-determinism check")
        rows = rec.query("SELECT * FROM v_repeats ORDER BY spread DESC")
        if not rows:
            print("(no request was issued more than once)")
        for r in rows:
            flag = "DRIFT" if (r["spread"] or 0) > 1e-9 else "stable"
            print(
                f"{flag:7} {r['question_id']:20} {r['question_type']:7} "
                f"n={r['n_calls']} models={r['n_models']} spread={r['spread']}"
            )

        section("Observations recorded")
        for r in rec.query("SELECT claim, verdict FROM observations ORDER BY obs_id"):
            print(f"[{r['verdict']:15}] {r['claim']}")

        section("Slowest calls")
        for r in rec.query(
            """SELECT request_id, n_questions, latency_ms, upstream_ms, input_tokens
               FROM calls WHERE ok = 1 ORDER BY latency_ms DESC LIMIT 5"""
        ):
            overhead = (r["latency_ms"] or 0) - (r["upstream_ms"] or 0)
            print(
                f"{r['request_id']}  q={r['n_questions']:<3} "
                f"total={r['latency_ms']:.0f}ms upstream={r['upstream_ms']}ms "
                f"overhead={overhead:.0f}ms tokens={r['input_tokens']}"
            )

        section("Full provenance of one answer (traceability demo)")
        row = rec.query(
            """SELECT a.question_id, a.noul, a.choice, a.score, a.confidence,
                      c.request_id, c.model_served, c.request_hash,
                      c.response_hash, c.response_body, c.state_json
               FROM answers a JOIN calls c ON c.call_id = a.call_id
               WHERE c.ok = 1 LIMIT 1"""
        )[0]
        print(f"question:      {row['question_id']}")
        print(f"request_id:    {row['request_id']}")
        print(f"model_served:  {row['model_served']}")
        print(f"request_hash:  {row['request_hash'][:32]}...")
        print(f"response_hash: {row['response_hash'][:32]}...")
        print(f"state:         {row['state_json'][:90]}")
        print(f"raw response:  {row['response_body'][:160]}")
    finally:
        rec.close()


if __name__ == "__main__":
    main()
