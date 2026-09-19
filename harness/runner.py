"""The harness runner: search, gate, confirm, shrink, report.

Pipeline for every property:

  1. SEARCH      run N generated cases, record the continuous effect for each.
  2. GATE        compare each effect against the empirically measured noise
                 floor for the transform that property applies. Anything inside
                 the floor is discarded -- it is indistinguishable from the
                 system's own jitter and reporting it would be dishonest.
  3. CONFIRM     re-run each surviving case K times. A non-deterministic system
                 demands this: a single excursion is an anecdote, a repeated one
                 is a finding. Cases that do not reproduce are downgraded, not
                 reported as defects.
  4. SHRINK      walk the case toward the smallest input that still violates,
                 re-confirming at each step. A minimal reproducer is the
                 difference between "your model is weird" and "this exact input
                 breaks this exact invariant".
  5. REPORT      emit the invariant, the minimal case, the effect, the floor it
                 was judged against, reproducibility, and the forensic call ids.

Usage:
    source env.sh
    ./.venv/bin/python -m harness.runner --cases 12
    ./.venv/bin/python -m harness.runner --property question_independence
    ./.venv/bin/python -m harness.runner --replay question_independence:41
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from typesafe_sdk import TypeSafeClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forensics import ForensicRecorder  # noqa: E402

from .core import Case, Measurement, Property, Severity, Violation  # noqa: E402
from .noise import NoiseProfile, calibrate  # noqa: E402
from .properties import BY_NAME, PROPERTIES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "forensics.db"
REPORT_PATH = ROOT / "harness_report.json"

# Which noise floor each property is judged against. Getting this mapping wrong
# is the fastest way to manufacture false positives.
FLOOR_FOR = {
    "question_independence": "identity",
    "choice_order_invariance": "reorder",
    "irrelevant_context_stability": "cohort",
    "score_monotonicity": "identity",
    "choice_noul_agreement": "identity",
    "injection_resistance": "cohort",
}


class Harness:
    def __init__(self, client: Any, recorder: ForensicRecorder, run_id: str,
                 profile: NoiseProfile) -> None:
        self.client = client
        self.rec = recorder
        self.run_id = run_id
        self.profile = profile
        self.call_cursor = 0

    # -- forensic linkage --------------------------------------------------

    def _calls_since(self, mark: int) -> list[str]:
        rows = self.rec.query(
            "SELECT call_id FROM calls WHERE run_id = ? AND seq > ? ORDER BY seq",
            (self.run_id, mark),
        )
        return [r["call_id"] for r in rows]

    def _mark(self) -> int:
        row = self.rec.query(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM calls WHERE run_id = ?",
            (self.run_id,),
        )[0]
        return row["m"]

    def measure(self, prop: Property, case: Case) -> Measurement:
        mark = self._mark()
        try:
            m = prop.evaluate(self.client, case)
        except Exception as exc:  # a crash is itself a finding
            return Measurement(case=case, effect=float("nan"),
                               error=f"{type(exc).__name__}: {exc}")
        m.call_ids = self._calls_since(mark)
        return m

    # -- pipeline ----------------------------------------------------------

    def run_property(self, prop: Property, n_cases: int, n_confirm: int,
                     start_seed: int = 0) -> dict[str, Any]:
        floor_kind = FLOOR_FOR.get(prop.name, "identity")
        floor = self.profile.get(floor_kind)
        threshold = prop.floor_override or floor.threshold

        print(f"\n  {prop.name}  (floor={floor_kind} threshold={threshold:.4f})")

        # 1. SEARCH
        measurements: list[Measurement] = []
        for i in range(n_cases):
            case = prop.generate(start_seed + i)
            m = self.measure(prop, case)
            measurements.append(m)
            flag = "!" if m.ok and abs(m.effect) > threshold else "."
            print(flag, end="", flush=True)
        print()

        errors = [m for m in measurements if not m.ok]
        effects = [abs(m.effect) for m in measurements if m.ok]

        # 2. GATE
        suspects = [m for m in measurements if m.ok and abs(m.effect) > threshold]

        # 3. CONFIRM + 4. SHRINK
        violations: list[Violation] = []
        for suspect in sorted(suspects, key=lambda m: -abs(m.effect)):
            confirmations, attempts = self._confirm(prop, suspect.case, threshold, n_confirm)
            if confirmations == 0:
                continue  # noise excursion, not a defect
            shrunk, steps = self._shrink(prop, suspect, threshold)
            final = self.measure(prop, shrunk.case) if shrunk.case != suspect.case else suspect
            if not final.ok or abs(final.effect) <= threshold:
                final = suspect
                shrunk_from = None
            else:
                shrunk_from = suspect.case.case_id if shrunk.case != suspect.case else None

            violations.append(Violation(
                property_name=prop.name,
                hypothesis=prop.hypothesis,
                why_it_matters=prop.why_it_matters,
                severity=prop.severity,
                case=final.case,
                effect=final.effect,
                noise_floor=threshold,
                margin=floor.margin(final.effect),
                n_confirmations=confirmations,
                n_attempts=attempts,
                shrunk_from=shrunk_from,
                shrink_steps=steps,
                detail=final.detail,
                call_ids=final.call_ids,
            ))

        return {
            "property": prop.name,
            "hypothesis": prop.hypothesis,
            "why_it_matters": prop.why_it_matters,
            "severity": prop.severity.value,
            "floor_kind": floor_kind,
            "threshold": round(threshold, 5),
            "cases_run": n_cases,
            "errors": len(errors),
            "error_messages": [m.error for m in errors][:3],
            "effect_max": round(max(effects), 5) if effects else 0.0,
            "effect_mean": round(statistics.mean(effects), 5) if effects else 0.0,
            "effect_p50": round(statistics.median(effects), 5) if effects else 0.0,
            "suspects": len(suspects),
            "violations": [v.to_dict() for v in violations],
            "held": not violations,
        }

    def _confirm(self, prop: Property, case: Case, threshold: float,
                 n: int) -> tuple[int, int]:
        """Re-run a suspect case; count how often it exceeds the floor again."""
        hits = 0
        for _ in range(n):
            m = self.measure(prop, case)
            if m.ok and abs(m.effect) > threshold:
                hits += 1
        return hits, n

    def _shrink(self, prop: Property, m: Measurement,
                threshold: float) -> tuple[Measurement, int]:
        """Greedily reduce the case while the violation survives."""
        if prop.shrink is None:
            return m, 0
        best, steps = m, 0
        improved = True
        while improved:
            improved = False
            for candidate in prop.shrink(best.case):
                trial = self.measure(prop, candidate)
                steps += 1
                if trial.ok and abs(trial.effect) > threshold:
                    best = trial
                    improved = True
                    break
        return best, steps


def main() -> None:
    ap = argparse.ArgumentParser(description="Metamorphic property harness for Jev")
    ap.add_argument("--cases", type=int, default=8, help="cases per property")
    ap.add_argument("--confirm", type=int, default=3, help="confirmation re-runs")
    ap.add_argument("--calib-repeats", type=int, default=3)
    ap.add_argument("--property", help="run only this property")
    ap.add_argument("--replay", help="PROPERTY:SEED -- rerun one case verbatim")
    ap.add_argument("--seed", type=int, default=0, help="starting seed")
    args = ap.parse_args()

    props = PROPERTIES
    if args.property:
        if args.property not in BY_NAME:
            sys.exit(f"unknown property: {args.property}")
        props = [BY_NAME[args.property]]

    started = time.time()
    with ForensicRecorder(DB_PATH) as rec:
        run_id = rec.start_run("harness", notes=f"property harness: {args.__dict__}")
        with TypeSafeClient() as raw:
            client = rec.wrap(raw, run_id)

            if args.replay:
                name, _, seed = args.replay.partition(":")
                prop = BY_NAME[name]
                case = prop.generate(int(seed))
                h = Harness(client, rec, run_id, NoiseProfile())
                m = h.measure(prop, case)
                print(json.dumps({
                    "property": name, "seed": int(seed), "case_id": case.case_id,
                    "effect": m.effect, "detail": m.detail,
                    "forensic_call_ids": m.call_ids,
                }, indent=2))
                rec.end_run(run_id)
                return

            print("calibrating noise floor ...")
            profile = calibrate(client, repeats=args.calib_repeats)
            for kind, f in profile.floors.items():
                print(f"  {kind:10} max={f.max_abs:.4f} stdev={f.stdev:.4f} "
                      f"threshold={f.threshold:.4f}  ({f.n_calls} calls)")

            h = Harness(client, rec, run_id, profile)
            results = [h.run_property(p, args.cases, args.confirm, args.seed)
                       for p in props]

        rec.end_run(run_id)
        integrity = rec.verify_integrity()
        cost = dict(rec.query("SELECT * FROM v_run_cost WHERE run_id = ?", (run_id,))[0])

    all_violations = [v for r in results for v in r["violations"]]
    report = {
        "run_id": run_id,
        "elapsed_s": round(time.time() - started, 1),
        "noise_profile": profile.to_dict(),
        "config": {"cases": args.cases, "confirm": args.confirm, "seed": args.seed},
        "properties": results,
        "summary": {
            "properties_run": len(results),
            "properties_held": sum(1 for r in results if r["held"]),
            "violations": len(all_violations),
            "by_severity": {
                s.value: sum(1 for v in all_violations if v["severity"] == s.value)
                for s in Severity
            },
        },
        "forensics": {"integrity": integrity, "cost": cost},
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2))

    print("\n" + "=" * 72)
    for r in results:
        status = "HELD" if r["held"] else f"VIOLATED ({len(r['violations'])})"
        print(f"{status:16} {r['property']:32} max_effect={r['effect_max']:.4f} "
              f"threshold={r['threshold']:.4f}")
    print("=" * 72)
    print(f"run {run_id}  {report['elapsed_s']}s  calls={cost['calls']} "
          f"tokens={cost['input_tokens']:,}  integrity={integrity['intact']}")
    print(f"report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
