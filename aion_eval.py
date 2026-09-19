"""Evaluate Jev against real regulatory corpora from the aion-context org.

Why these corpora matter for testing: every label here was authored by someone
other than the tester. FedRAMP's standards body assigned each rule its binding
force. California's legislature wrote the federal cross-references into statute
text. CISA assigned each CVE its remediation due date. That is ground truth we
did not invent, which is the scarcest ingredient in evaluating a semantic model
-- hand-labelled corpora mostly measure the labeller.

Three evaluations, each chosen because the label is structural:

  A. OBLIGATION STRENGTH (FedRAMP FRR, n=328)
     Each rule carries force = MUST / MUST NOT / SHOULD / SHOULD NOT / MAY.
     The modal verb appears verbatim in every statement, so the raw task is
     string matching and worthless. We MASK the modal and ask Jev to judge how
     binding the requirement is from its substance alone. That is a genuine
     System One judgment with a legislated answer.

  B. CROSS-CORPUS CITATION (selpa-aion -> aion-doe)
     84 sections of Cal. Educ. Code Part 30 cite 34 CFR by number, in text
     written by legislators. Whether a passage defers to federal law is a
     semantic judgment; whether it contains a federal citation is a regex.
     We compare the two.

  C. DATE ARITHMETIC ON REAL DATA (CISA KEV, n=1713)
     Every CVE carries dateAdded and dueDate. Jev's documented jaggedness says
     date comparison is unreliable. Earlier synthetic tests did not reproduce
     that. Real data with mixed real-world spacing is a fairer trial.

Every call is captured to forensics.db.

Run: source env.sh && ./.venv/bin/python aion_eval.py --n 60
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

from forensics import ForensicRecorder

AION = Path("/tmp/aion_probe")
ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "forensics.db"
RESULTS = ROOT / "aion_eval_results.json"

MODAL = re.compile(r"\b(MUST NOT|SHOULD NOT|MUST|SHOULD|MAY|SHALL NOT|SHALL)\b", re.I)
TITLE34 = re.compile(r"Title\s+34\s+of\s+the\s+Code\s+of\s+Federal\s+Regulations", re.I)
CFR_ANY = re.compile(r"C\.?F\.?R\.?", re.I)


# ---------------------------------------------------------------------------
# A. Obligation strength, with the modal masked
# ---------------------------------------------------------------------------

def load_frr() -> list[dict[str, Any]]:
    R = json.loads((AION / "fedramp-aion/data/rules.json").read_text())
    rows: list[dict[str, Any]] = []

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            if "statement" in n and "force" in n:
                rows.append(n)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(R["FRR"])
    return rows


def eval_obligation(client: Any, rows: list[dict], n: int, rng: random.Random) -> dict:
    # Three ordered levels. MUST NOT / SHOULD NOT are directional negatives of
    # the same strength axis, so they are folded into their positive partner --
    # the judgment under test is bindingness, not polarity.
    fold = {"MUST": "MUST", "MUST NOT": "MUST",
            "SHOULD": "SHOULD", "SHOULD NOT": "SHOULD", "MAY": "MAY"}
    levels = ["MAY", "SHOULD", "MUST"]

    by_force: dict[str, list[dict]] = {}
    for r in rows:
        by_force.setdefault(fold[r["force"]], []).append(r)

    # Balance across classes so accuracy is not dominated by MUST.
    per = max(1, n // 3)
    sample: list[dict] = []
    for f in levels:
        pool = by_force.get(f, [])
        sample.extend(rng.sample(pool, min(per, len(pool))))
    rng.shuffle(sample)

    q = {
        "strength": Score(
            instructions=(
                "The statement is a requirement from a federal cloud-security "
                "standard, with its modal verb replaced by [MODAL]. Judge how "
                "binding the requirement is, from its substance alone."
            ),
            criteria=[
                "Optional. A permitted action the party may choose to take or skip, "
                "with no consequence for declining.",
                "Recommended. Expected practice the party is meant to follow, but "
                "which allows justified deviation.",
                "Mandatory. A firm obligation the party is required to meet, with "
                "no discretion to decline.",
            ],
        )
    }

    records = []
    for r in sample:
        masked = MODAL.sub("[MODAL]", r["statement"])
        resp = client.system_one(
            {"requirement": masked, "applies_to": r.get("affects") or ["unspecified"]},
            q,
        )
        a = resp.scores["strength"]
        truth = fold[r["force"]]
        pred = levels[min(range(3), key=lambda i: abs(a.score - i))]
        records.append({
            "truth": truth, "pred": pred, "score": round(a.score, 3),
            "confidence": round(a.confidence, 3),
            "correct": pred == truth,
            "statement": masked[:180],
            "original_force": r["force"],
        })

    acc = statistics.mean(1.0 if x["correct"] else 0.0 for x in records)
    # Ordinal error: how far off in levels, which matters more than raw accuracy
    # for a Score primitive.
    idx = {l: i for i, l in enumerate(levels)}
    mae = statistics.mean(abs(idx[x["pred"]] - idx[x["truth"]]) for x in records)
    conf_right = [x["confidence"] for x in records if x["correct"]]
    conf_wrong = [x["confidence"] for x in records if not x["correct"]]

    cm = Counter((x["truth"], x["pred"]) for x in records)
    per_class = {}
    for f in levels:
        sub = [x for x in records if x["truth"] == f]
        if sub:
            per_class[f] = {
                "n": len(sub),
                "accuracy": round(statistics.mean(1.0 if x["correct"] else 0.0 for x in sub), 3),
                "mean_score": round(statistics.mean(x["score"] for x in sub), 3),
            }

    return {
        "task": "obligation strength from masked requirement text",
        "ground_truth": "FedRAMP FRR `force` field (authored by the standards body)",
        "n": len(records),
        "accuracy": round(acc, 4),
        "mean_ordinal_error": round(mae, 4),
        "mean_confidence_correct": round(statistics.mean(conf_right), 3) if conf_right else None,
        "mean_confidence_wrong": round(statistics.mean(conf_wrong), 3) if conf_wrong else None,
        "confidence_separates": (
            bool(conf_right and conf_wrong
                 and statistics.mean(conf_right) > statistics.mean(conf_wrong))
        ),
        "per_class": per_class,
        "confusion": {f"{t}->{p}": c for (t, p), c in sorted(cm.items())},
        "errors": [x for x in records if not x["correct"]][:6],
    }


# ---------------------------------------------------------------------------
# B. Cross-corpus federal deference
# ---------------------------------------------------------------------------

def load_selpa_paragraphs() -> list[dict[str, Any]]:
    out = []
    for f in sorted((AION / "selpa-aion/data").glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        for p in d.get("paragraphs", []):
            t = p.get("text", "")
            if len(t) < 80:
                continue
            out.append({
                "citation": p.get("citation"),
                "text": t,
                "has_federal_cite": bool(TITLE34.search(t) or CFR_ANY.search(t)),
            })
    return out


def eval_citation(client: Any, paras: list[dict], n: int, rng: random.Random) -> dict:
    pos = [p for p in paras if p["has_federal_cite"]]
    neg = [p for p in paras if not p["has_federal_cite"]]
    half = max(1, n // 2)
    sample = rng.sample(pos, min(half, len(pos))) + rng.sample(neg, min(half, len(neg)))
    rng.shuffle(sample)

    q = {
        "defers": Noul(
            instructions=(
                "Does this passage of California statute explicitly incorporate or "
                "defer to a federal regulation or federal statute by reference?"
            ),
            criteria={
                "true": "The passage names or points to federal law as governing "
                        "some part of its meaning or requirement.",
                "false": "The passage states California law without pointing to "
                         "any federal provision.",
            },
        )
    }

    records = []
    for p in sample:
        resp = client.system_one({"statute_text": p["text"]}, q)
        v = resp.nouls["defers"].noul
        records.append({
            "citation": p["citation"],
            "truth": p["has_federal_cite"],
            "noul": round(v, 4),
            "pred": v > 0.5,
            "correct": (v > 0.5) == p["has_federal_cite"],
            "text": p["text"][:160],
        })

    acc = statistics.mean(1.0 if x["correct"] else 0.0 for x in records)
    tp = sum(1 for x in records if x["truth"] and x["pred"])
    fp = sum(1 for x in records if not x["truth"] and x["pred"])
    fn = sum(1 for x in records if x["truth"] and not x["pred"])
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0

    return {
        "task": "does a statute passage defer to federal law",
        "ground_truth": "regex over legislator-authored citation text",
        "n": len(records),
        "accuracy": round(acc, 4),
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "false_positives": [x for x in records if not x["truth"] and x["pred"]][:5],
        "false_negatives": [x for x in records if x["truth"] and not x["pred"]][:5],
    }


# ---------------------------------------------------------------------------
# C. Date arithmetic on real CISA KEV data
# ---------------------------------------------------------------------------

def eval_kev_dates(client: Any, n: int, rng: random.Random) -> dict:
    K = json.loads((AION / "fedramp-aion/data/kev.json").read_text())["vulnerabilities"]
    usable = [v for v in K if v.get("dateAdded") and v.get("dueDate")]
    sample = rng.sample(usable, min(n, len(usable)))

    q = {
        "over_14_days": Noul(
            instructions=(
                "Is the remediation window longer than 14 days? The window runs "
                "from `added` to `due`."
            ),
            criteria={"true": "More than 14 days separate the two dates.",
                      "false": "14 days or fewer separate the two dates."},
        )
    }

    records = []
    for v in sample:
        d0 = date.fromisoformat(v["dateAdded"])
        d1 = date.fromisoformat(v["dueDate"])
        days = (d1 - d0).days
        truth = days > 14
        resp = client.system_one({"cve": v["cveID"], "added": v["dateAdded"],
                                  "due": v["dueDate"]}, q)
        p = resp.nouls["over_14_days"].noul
        records.append({
            "cve": v["cveID"], "added": v["dateAdded"], "due": v["dueDate"],
            "actual_days": days, "truth": truth,
            "noul": round(p, 4), "pred": p > 0.5,
            "correct": (p > 0.5) == truth,
        })

    acc = statistics.mean(1.0 if x["correct"] else 0.0 for x in records)
    # Near-boundary cases are where date reasoning should break first.
    near = [x for x in records if 7 <= x["actual_days"] <= 28]
    far = [x for x in records if x not in near]

    return {
        "task": "is the remediation window longer than 14 days",
        "ground_truth": "date subtraction on CISA-assigned dates",
        "n": len(records),
        "accuracy": round(acc, 4),
        "accuracy_near_boundary_7_28d": (
            round(statistics.mean(1.0 if x["correct"] else 0.0 for x in near), 4)
            if near else None
        ),
        "n_near_boundary": len(near),
        "accuracy_far_from_boundary": (
            round(statistics.mean(1.0 if x["correct"] else 0.0 for x in far), 4)
            if far else None
        ),
        "day_spread": sorted({x["actual_days"] for x in records})[:12],
        "errors": [x for x in records if not x["correct"]][:6],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60, help="items per evaluation")
    ap.add_argument("--seed", type=int, default=20260918)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    frr = load_frr()
    paras = load_selpa_paragraphs()
    print(f"corpora: {len(frr)} FedRAMP rules, {len(paras)} SELPA paragraphs")

    out: dict[str, Any] = {}
    with ForensicRecorder(DB_PATH) as rec:
        run_id = rec.start_run("aion_eval",
                               notes="Jev vs real regulatory corpora (aion-context)")
        with TypeSafeClient() as raw:
            client = rec.wrap(raw, run_id)

            print("A. obligation strength (modal masked) ...")
            out["obligation_strength"] = eval_obligation(client, frr, args.n, rng)

            print("B. cross-corpus federal deference ...")
            out["federal_deference"] = eval_citation(client, paras, args.n, rng)

            print("C. KEV date arithmetic ...")
            out["kev_dates"] = eval_kev_dates(client, args.n, rng)

            for k, v in out.items():
                rec.observe(run_id, claim=v["task"], verdict="measured", detail=v)
        rec.end_run(run_id)
        out["_forensics"] = {
            "run_id": run_id,
            "integrity": rec.verify_integrity(),
            "cost": dict(rec.query("SELECT * FROM v_run_cost WHERE run_id=?", (run_id,))[0]),
        }

    RESULTS.write_text(json.dumps(out, indent=2, default=str))
    print("\n" + "=" * 74)
    for k in ("obligation_strength", "federal_deference", "kev_dates"):
        v = out[k]
        print(f"{k:22} n={v['n']:<4} accuracy={v['accuracy']}")
    print("=" * 74)
    print(f"wrote {RESULTS}")


if __name__ == "__main__":
    main()
