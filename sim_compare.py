"""Compare simulation runs across chaos levels with statistical rigour.

The single-run report answers "did anything break?". This answers the harder
question: "does chaos actually change behaviour, and by how much, relative to
what we can distinguish?"

Key discipline: a difference in escalation rate between chaos levels is only
reportable if its confidence interval excludes zero. Counting rounds is not
enough -- with 14 scenarios and N seeds we have a small sample per cell, and
Wilson intervals keep us honest about it.

Run:
    ./.venv/bin/python sim_compare.py consensus/rust/consensus-kernel/sim_*.jsonl
"""

from __future__ import annotations

import collections
import json
import math
import sys
from pathlib import Path


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Correct at small n, unlike normal approximation."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def two_proportion_z(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    """Return (difference, z statistic) for p1 - p2."""
    if n1 == 0 or n2 == 0:
        return (0.0, 0.0)
    p1, p2 = k1 / n1, k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return (p1 - p2, 0.0)
    return (p1 - p2, (p1 - p2) / se)


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def section(t: str) -> None:
    print(f"\n{t}\n{'=' * len(t)}")


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        base = Path("consensus/rust/consensus-kernel")
        paths = sorted(base.glob("sim_*.jsonl"))
    runs: dict[str, list[dict]] = {}
    for p in paths:
        if not p.exists():
            continue
        rows = load(p)
        if rows:
            runs[rows[0]["chaos"]] = rows

    if not runs:
        sys.exit("no telemetry found")

    order = [c for c in ("none", "realistic", "severe") if c in runs]

    section("SAFETY INVARIANT ACROSS CHAOS LEVELS")
    print(f"{'chaos':<12}{'golden':>8}{'correct':>9}{'escal':>7}{'WRONG':>7}"
          f"{'  accuracy 95% CI'}")
    print("-" * 66)
    total_golden = 0
    total_wrong = 0
    for c in order:
        g = [r for r in runs[c] if r["tier"] == "Golden"]
        ok = sum(1 for r in g if r["correct"] is True)
        esc = sum(1 for r in g if r["escalated"])
        wrong = sum(1 for r in g if r["safety_violation"])
        total_golden += len(g)
        total_wrong += wrong
        lo, hi = wilson(ok, len(g))
        print(f"{c:<12}{len(g):>8}{ok:>9}{esc:>7}{wrong:>7}   [{lo:.3f}, {hi:.3f}]")

    print()
    lo, hi = wilson(total_golden - total_wrong, total_golden)
    print(f"Pooled: {total_wrong} wrong verdicts in {total_golden} golden rounds.")
    print(f"Safety rate 95% CI: [{lo:.4f}, {hi:.4f}]")
    if total_wrong == 0:
        # Rule of three: with 0 events in n trials, the 95% upper bound on the
        # true rate is about 3/n. This is the honest way to state "never
        # observed" without claiming "impossible".
        print(f"Zero violations observed. Rule of three: true violation rate is")
        print(f"below {3 / total_golden:.4f} with 95% confidence "
              f"(about 1 in {int(total_golden / 3)}).")
        print("This bounds the rate. It does not prove it is zero.")

    section("DOES CHAOS CHANGE BEHAVIOUR? (escalation rate)")
    print("If chaos did not matter, escalation rates would be indistinguishable.")
    print()
    print(f"{'chaos':<12}{'rounds':>8}{'escalated':>11}{'rate':>8}{'  95% CI'}")
    print("-" * 56)
    cells = {}
    for c in order:
        rows = runs[c]
        esc = sum(1 for r in rows if r["escalated"])
        cells[c] = (esc, len(rows))
        lo, hi = wilson(esc, len(rows))
        print(f"{c:<12}{len(rows):>8}{esc:>11}{esc / len(rows):>8.3f}   [{lo:.3f}, {hi:.3f}]")

    if "none" in cells and "severe" in cells:
        (k1, n1), (k2, n2) = cells["severe"], cells["none"]
        diff, z = two_proportion_z(k1, n1, k2, n2)
        print()
        print(f"severe - none: {diff:+.3f}  (z = {z:.2f})")
        if abs(z) > 1.96:
            print("Significant at 95%: chaos measurably increases escalation.")
            print("The system responds to degraded evidence by declining more often,")
            print("which is the designed fail-safe direction.")
        else:
            print("NOT significant at 95%. We cannot claim chaos changed the")
            print("escalation rate from this sample.")

    section("GRACEFUL DEGRADATION")
    print(f"{'chaos':<12}{'rounds':>8}{'w/ failures':>13}{'aborted':>9}{'still decided':>15}")
    print("-" * 58)
    for c in order:
        rows = runs[c]
        wf = [r for r in rows if r.get("oracle_failures")]
        ab = sum(1 for r in rows if r["outcome"] == "error")
        dec = sum(1 for r in wf if r["outcome"] == "decided")
        print(f"{c:<12}{len(rows):>8}{len(wf):>13}{ab:>9}{dec:>15}")
    print()
    print("'aborted' must stay 0: an oracle failure marks an agent unavailable,")
    print("it does not void a consensus the remaining agents can still reach.")

    section("AMBIGUOUS CASES: does it decline when it should?")
    print(f"{'chaos':<12}{'rounds':>8}{'escalated':>11}{'rate':>8}{'  95% CI'}")
    print("-" * 56)
    for c in order:
        amb = [r for r in runs[c] if r["tier"] == "Ambiguous"]
        if not amb:
            continue
        esc = sum(1 for r in amb if r["escalated"])
        lo, hi = wilson(esc, len(amb))
        print(f"{c:<12}{len(amb):>8}{esc:>11}{esc / len(amb):>8.3f}   [{lo:.3f}, {hi:.3f}]")
    print()
    print("These are underdetermined by construction; escalation is the correct")
    print("answer. A rate well below 1.0 is a real limitation, not a success.")

    section("STABILITY GATE")
    all_rows = [r for rows in runs.values() for r in rows]
    votes = [v for r in all_rows for v in r["votes"]]
    unstable = [v for v in votes if not v["stable"]]
    stable = [v for v in votes if v["stable"]]
    print(f"votes recorded     : {len(votes)}")
    print(f"  stable           : {len(stable)}")
    print(f"  excluded         : {len(unstable)}")
    if unstable:
        mx = max(v["margin"] for v in unstable)
        print(f"  max excluded margin : {mx:.3f}")
    if stable:
        mn = min(v["margin"] for v in stable)
        print(f"  min included margin : {mn:.3f}")
    print(f"  noise floor         : 0.042 (measured over 1,406 calls)")
    print()
    if unstable and stable:
        mx = max(v["margin"] for v in unstable)
        mn = min(v["margin"] for v in stable)
        if mx <= 0.042 <= mn:
            print("The gate separated cleanly at the measured floor: every excluded")
            print("vote sat below it, every included vote above. The threshold is")
            print("doing exactly what it was calibrated to do.")
        else:
            print("Gate boundary is NOT clean -- inspect votes straddling the floor.")

    section("PER-SCENARIO, POOLED")
    by = collections.defaultdict(list)
    for r in all_rows:
        by[r["scenario"]].append(r)
    print(f"{'scenario':<40}{'n':>5}{'dec':>5}{'esc':>5}{'wrong':>7}{'  esc CI'}")
    print("-" * 74)
    for name in sorted(by):
        rs = by[name]
        dec = sum(1 for r in rs if r["outcome"] == "decided")
        esc = sum(1 for r in rs if r["escalated"])
        wrong = sum(1 for r in rs if r["correct"] is False and r["verdict"])
        lo, hi = wilson(esc, len(rs))
        flag = "  <-- WRONG" if wrong else ""
        print(f"{name:<40}{len(rs):>5}{dec:>5}{esc:>5}{wrong:>7}"
              f"  [{lo:.2f},{hi:.2f}]{flag}")

    section("COST")
    # Input tokens are billed at $42/Btok; output is free. Each round is up to
    # 5 agents x up to 2 attempts, one question each.
    n_calls = sum(len(r["oracle_request_ids"]) for r in all_rows)
    print(f"rounds            : {len(all_rows)}")
    print(f"oracle calls      : {n_calls}")
    print(f"calls per round   : {n_calls / max(1, len(all_rows)):.2f}")
    print()
    print("Measured meter: input_tokens = 0.15127 * payload_chars + 280.7")
    print("Fixed overhead dominates at these state sizes, so the honest figure")
    print("comes from the forensic DB rather than an estimate here.")


if __name__ == "__main__":
    main()
