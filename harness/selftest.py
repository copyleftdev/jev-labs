"""Negative control: does the harness actually detect violations?

A property harness that reports HELD is worthless unless you have shown it can
report VIOLATED. `choice_order_invariance` returned effect 0.0000 across every
case, which is either a genuinely perfect invariant or a broken detector. This
module distinguishes the two.

Method: wrap the client in a mutator that perturbs responses by a known
magnitude, then re-run the properties. A correct harness must:

  * report VIOLATED for every injected fault above the noise floor,
  * report HELD when the injected fault is below it,
  * and recover the injected magnitude in its reported effect.

This is the harness testing itself. Without it, "HELD" is an unfalsifiable
claim and the whole report is decoration.

Run: source env.sh && ./.venv/bin/python -m harness.selftest
"""

from __future__ import annotations

import copy
import json
import random
import sys
from pathlib import Path
from typing import Any

from typesafe_sdk import TypeSafeClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forensics import ForensicRecorder  # noqa: E402

from .noise import NoiseProfile, calibrate  # noqa: E402
from .properties import BY_NAME  # noqa: E402
from .runner import DB_PATH, Harness  # noqa: E402

REPORT_PATH = Path(__file__).resolve().parent.parent / "selftest_report.json"


class MutatingClient:
    """Wraps a client and perturbs responses to simulate a defective model.

    Each property compares a BASELINE call against a TRANSFORMED call. To
    simulate a broken invariant we must perturb only the transformed half of
    each pair -- biasing both halves equally cancels in the difference and the
    injected fault becomes invisible. So we mutate every second call.
    """

    def __init__(self, inner: Any, bias: float = 0.0, order_bias: float = 0.0) -> None:
        self.inner = inner
        self.bias = bias
        self.order_bias = order_bias
        self._call_n = 0

    def reset(self) -> None:
        self._call_n = 0

    def system_one(self, state: Any, questions: dict[str, Any], **kw: Any) -> Any:
        resp = self.inner.system_one(state, questions, **kw)
        self._call_n += 1
        # Mutate the second call of each (baseline, transformed) pair only.
        if self._call_n % 2 != 0:
            return resp
        if not (self.bias or self.order_bias):
            return resp

        # SDK answer objects are frozen pydantic models, so a fault must be
        # injected by rebuilding them. Silently failing to mutate would make
        # the self-test report false confidence, which is worse than no test.
        new_answers = {}
        for qid, ans in resp.answers.items():
            kind = getattr(ans, "type", None)
            if self.bias and kind == "noul":
                ans = ans.model_copy(
                    update={"noul": max(0.0, min(1.0, ans.noul + self.bias))}
                )
            elif self.order_bias and kind == "choice" and ans.probabilities:
                probs = dict(ans.probabilities)
                keys = list(probs)
                first = keys[0]
                moved = min(self.order_bias, 1.0 - probs[first])
                probs[first] += moved
                rest = [k for k in keys[1:] if probs[k] > 0]
                if rest:
                    per = moved / len(rest)
                    for k in rest:
                        probs[k] = max(0.0, probs[k] - per)
                ans = ans.model_copy(update={"probabilities": probs})
            new_answers[qid] = ans

        mutated = resp.model_copy(update={"answers": new_answers})
        # Confirm the injection actually landed; a silent no-op would invalidate
        # every conclusion drawn from this self-test.
        if self.bias:
            for qid, ans in mutated.answers.items():
                if getattr(ans, "type", None) == "noul":
                    assert abs(ans.noul - resp.answers[qid].noul) > 1e-9 or ans.noul in (0.0, 1.0), \
                        "fault injection did not land on a noul answer"
        return mutated

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def main() -> None:
    results: list[dict[str, Any]] = []

    with ForensicRecorder(DB_PATH) as rec:
        run_id = rec.start_run("selftest", notes="negative control: fault injection")
        with TypeSafeClient() as raw:
            recorded = rec.wrap(raw, run_id)

            print("calibrating (unmutated) ...")
            profile = calibrate(recorded, repeats=3)
            ident = profile.get("identity").threshold
            reorder = profile.get("reorder").threshold
            print(f"  identity threshold={ident:.4f}  reorder threshold={reorder:.4f}")

            # --- Noul-based property: question_independence -----------------
            for bias in (0.0, 0.01, 0.15, 0.40):
                mut = MutatingClient(recorded, bias=bias)
                h = Harness(mut, rec, run_id, profile)
                mut.reset()
                res = h.run_property(BY_NAME["question_independence"],
                                     n_cases=4, n_confirm=2, start_seed=900)
                expect_violation = bias > ident
                detected = not res["held"]
                recovered = res["effect_max"]
                results.append({
                    "property": "question_independence",
                    "injected_bias": bias,
                    "threshold": round(ident, 4),
                    "expected_violation": expect_violation,
                    "detected_violation": detected,
                    "correct": detected == expect_violation,
                    "max_effect_observed": recovered,
                    "magnitude_recovered": (
                        abs(recovered - bias) < 0.05 if bias > ident else None
                    ),
                })
                print(f"    bias={bias:<5} expect_viol={expect_violation!s:<5} "
                      f"detected={detected!s:<5} max_effect={recovered}")

            # --- Choice-based property: choice_order_invariance -------------
            for ob in (0.0, 0.02, 0.20, 0.50):
                mut = MutatingClient(recorded, order_bias=ob)
                h = Harness(mut, rec, run_id, profile)
                mut.reset()
                res = h.run_property(BY_NAME["choice_order_invariance"],
                                     n_cases=4, n_confirm=2, start_seed=900)
                expect_violation = ob > reorder
                detected = not res["held"]
                results.append({
                    "property": "choice_order_invariance",
                    "injected_bias": ob,
                    "threshold": round(reorder, 4),
                    "expected_violation": expect_violation,
                    "detected_violation": detected,
                    "correct": detected == expect_violation,
                    "max_effect_observed": res["effect_max"],
                })
                print(f"    order_bias={ob:<5} expect_viol={expect_violation!s:<5} "
                      f"detected={detected!s:<5} max_effect={res['effect_max']}")

        rec.end_run(run_id)
        integrity = rec.verify_integrity()

    passed = sum(1 for r in results if r["correct"])
    report = {
        "run_id": run_id,
        "results": results,
        "summary": {
            "checks": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "harness_trustworthy": passed == len(results),
        },
        "integrity": integrity,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2))

    print("\n" + "=" * 72)
    print(f"self-test: {passed}/{len(results)} checks correct")
    for r in results:
        mark = "ok " if r["correct"] else "FAIL"
        print(f"  [{mark}] {r['property']:28} bias={r['injected_bias']:<5} "
              f"expected={r['expected_violation']!s:<5} got={r['detected_violation']}")
    print(f"report: {REPORT_PATH}")
    if passed != len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
