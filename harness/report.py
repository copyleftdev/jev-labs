"""Turn harness findings into a report an engineer can act on.

A bug report that says "the model was wrong" gets closed as wontfix. A report
that says "this invariant, broken by this minimal input, by this magnitude,
against this measured noise floor, reproducing N/N times, here are the request
ids" gets fixed.

    ./.venv/bin/python -m harness.report
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "harness_report.json"
SELFTEST = ROOT / "selftest_report.json"
OUT = ROOT / "docs" / "FINDINGS.md"

SEVERITY_ORDER = {"hard": 0, "semantic": 1, "calibration": 2, "documented": 3}


def main() -> None:
    if not REPORT.exists():
        sys.exit("no harness_report.json -- run harness.runner first")
    r = json.loads(REPORT.read_text())
    st = json.loads(SELFTEST.read_text()) if SELFTEST.exists() else None

    lines: list[str] = []
    add = lines.append

    add("# Jev property-harness findings")
    add("")
    add(f"Run `{r['run_id']}` | {r['elapsed_s']}s | "
        f"{r['forensics']['cost']['calls']} API calls | "
        f"{r['forensics']['cost']['input_tokens']:,} input tokens")
    add("")

    # --- trust statement first -------------------------------------------
    add("## Can this report be trusted")
    add("")
    if st:
        s = st["summary"]
        add(f"Harness self-test: **{s['passed']}/{s['checks']} checks correct**. "
            "Known-magnitude faults were injected into the client and the harness "
            "was required to detect every fault above the noise floor, stay silent "
            "on every fault below it, and recover the injected magnitude.")
        if not s["harness_trustworthy"]:
            add("")
            add("> **The self-test did not fully pass. Treat HELD results as unproven.**")
    else:
        add("No self-test report found. HELD results are unproven.")
    add("")
    add(f"Forensic store integrity: "
        f"{r['forensics']['integrity']['checked']} response bodies re-hashed, "
        f"{len(r['forensics']['integrity']['mismatches'])} mismatches.")
    add("")

    # --- noise floor ------------------------------------------------------
    add("## Measured noise floor")
    add("")
    add("Jev is not deterministic, so every effect is judged against the system's "
        "own variation under a no-op transform, measured at runtime in this run.")
    add("")
    add("| transform | samples | max deviation | stdev | violation threshold |")
    add("|---|---|---|---|---|")
    for kind, f in r["noise_profile"].items():
        add(f"| {kind} | {f['n_samples']} | {f['max_abs_deviation']} | "
            f"{f['stdev']} | **{f['threshold']}** |")
    add("")
    add("An effect below the threshold for its transform is discarded as jitter, "
        "not reported.")
    add("")

    # --- summary table ----------------------------------------------------
    add("## Results")
    add("")
    add("| property | severity | verdict | max effect | threshold |")
    add("|---|---|---|---|---|")
    props = sorted(r["properties"],
                   key=lambda p: (p["held"], SEVERITY_ORDER.get(p["severity"], 9)))
    for p in props:
        verdict = "HELD" if p["held"] else f"**VIOLATED** ({len(p['violations'])})"
        add(f"| `{p['property']}` | {p['severity']} | {verdict} | "
            f"{p['effect_max']} | {p['threshold']} |")
    add("")

    # --- findings ---------------------------------------------------------
    add("## Findings")
    add("")
    n = 0
    for p in props:
        if p["held"]:
            continue
        for v in p["violations"]:
            n += 1
            add(f"### F{n}. {p['property']} ({v['severity']})")
            add("")
            add(f"**Invariant.** {v['hypothesis']}")
            add("")
            add(f"**Why it matters.** {v['why_it_matters']}")
            add("")
            add(f"**Effect.** {v['effect']} "
                f"(**{v['margin_over_noise']}x** the measured noise floor of "
                f"{v['noise_floor']})")
            add("")
            add(f"**Reproducibility.** {v['confirmations']} confirmation re-runs "
                f"exceeded the floor.")
            add("")
            if v["shrunk_from"]:
                add(f"**Minimized.** Reduced from case `{v['shrunk_from']}` in "
                    f"{v['shrink_steps']} shrink steps.")
                add("")
            add("**Minimal reproducer.**")
            add("")
            add("```")
            add(v["replay"])
            add("```")
            add("")
            add("**Observed.**")
            add("")
            add("```json")
            add(json.dumps(v["detail"], indent=2))
            add("```")
            add("")
            add(f"**Forensic call ids.** `{', '.join(v['forensic_call_ids'][:6])}`")
            add("")
            add("Each id maps to a row in `forensics.db` holding the verbatim "
                "request body, the verbatim response bytes, the TypeSafe "
                "`request_id`, and a SHA-256 of both.")
            add("")

    if n == 0:
        add("No property violations survived statistical gating and confirmation.")
        add("")

    # --- held properties --------------------------------------------------
    held = [p for p in r["properties"] if p["held"]]
    if held:
        add("## Invariants that held")
        add("")
        for p in held:
            add(f"- `{p['property']}` — max effect {p['effect_max']} over "
                f"{p['cases_run']} cases (threshold {p['threshold']}). "
                f"{p['hypothesis']}")
        add("")

    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT}  ({n} findings)")


if __name__ == "__main__":
    main()
