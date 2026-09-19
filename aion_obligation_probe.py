"""Follow-up: is the obligation-strength failure Jev's, or my rubric's?

The first run scored 0.389 accuracy on a 3-class task (chance = 0.333), which
looks like the model cannot do it. But the per-class means told a different
story:

    MAY 1.483   SHOULD 1.700   MUST 1.905

The ORDER is right. Every class is shifted up toward "mandatory" and squeezed
into a narrow band. That is range restriction, not an inability to judge, and
the two have opposite remedies: a compressed-but-ordered signal is usable after
recentring, a confused signal is not.

Before blaming the model we have to eliminate the tester. Three competing
explanations:

  H1 my rubric is biased -- the level descriptions push toward "mandatory"
     because regulatory prose sounds obligatory regardless of its modal.
  H2 Score compresses, but ranking survives -- absolute levels are unusable
     while relative ordering is fine.
  H3 the model genuinely cannot separate these classes at all.

Discriminating between them:

  * PAIRWISE  give Jev two requirements of different true force and ask which
    is MORE binding. This removes the rubric's absolute anchors entirely. If
    H1 or H2 holds, pairwise accuracy is high. If H3 holds, it is near chance.
  * CHOICE    ask the same 3-way judgment as a Choice over named options
    instead of a Score. Isolates the primitive from the judgment.
  * RANK      Spearman correlation between predicted score and true force rank
    over the whole sample. Tests monotonicity independent of thresholds.

Run: source env.sh && ./.venv/bin/python aion_obligation_probe.py --n 45
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
from itertools import combinations
from pathlib import Path
from typing import Any

from typesafe_sdk import Choice, Score, TypeSafeClient

from forensics import ForensicRecorder
from aion_eval import MODAL, load_frr

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "forensics.db"
RESULTS = ROOT / "aion_obligation_probe.json"

FOLD = {"MUST": "MUST", "MUST NOT": "MUST", "SHOULD": "SHOULD",
        "SHOULD NOT": "SHOULD", "MAY": "MAY"}
LEVELS = ["MAY", "SHOULD", "MUST"]
RANK = {l: i for i, l in enumerate(LEVELS)}


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation, with average ranks for ties (stdlib only)."""
    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else 0.0


def balanced_sample(rows: list[dict], n: int, rng: random.Random) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(FOLD[r["force"]], []).append(r)
    per = max(1, n // 3)
    out = []
    for f in LEVELS:
        pool = by.get(f, [])
        out.extend(rng.sample(pool, min(per, len(pool))))
    rng.shuffle(out)
    return out


def masked(r: dict) -> str:
    return MODAL.sub("[MODAL]", r["statement"])


# ---------------------------------------------------------------------------
# 1. Score with the original rubric, larger sample -- establishes the baseline
#    and gives us the rank correlation.
# ---------------------------------------------------------------------------

def run_score(client: Any, sample: list[dict]) -> dict:
    q = {"strength": Score(
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
    )}
    recs = []
    for r in sample:
        a = client.system_one({"requirement": masked(r),
                               "applies_to": r.get("affects") or ["unspecified"]},
                              q).scores["strength"]
        truth = FOLD[r["force"]]
        pred = LEVELS[min(range(3), key=lambda i: abs(a.score - i))]
        recs.append({"truth": truth, "pred": pred, "score": a.score,
                     "confidence": a.confidence, "correct": pred == truth})

    acc = statistics.mean(1.0 if x["correct"] else 0.0 for x in recs)
    rho = spearman([x["score"] for x in recs], [RANK[x["truth"]] for x in recs])
    per_class = {f: {"n": sum(1 for x in recs if x["truth"] == f),
                     "mean_score": round(statistics.mean(
                         [x["score"] for x in recs if x["truth"] == f]), 3)}
                 for f in LEVELS}
    scores = [x["score"] for x in recs]
    return {
        "method": "Score, absolute 3-level rubric",
        "n": len(recs),
        "accuracy": round(acc, 4),
        "spearman_rho": round(rho, 4),
        "per_class": per_class,
        "score_range": [round(min(scores), 3), round(max(scores), 3)],
        "score_stdev": round(statistics.pstdev(scores), 4),
    }


# ---------------------------------------------------------------------------
# 2. Choice over named options -- same judgment, different primitive.
# ---------------------------------------------------------------------------

def run_choice(client: Any, sample: list[dict]) -> dict:
    q = {"strength": Choice(
        instructions=(
            "The statement is a requirement from a federal cloud-security "
            "standard, with its modal verb replaced by [MODAL]. Which level of "
            "obligation does its substance describe?"
        ),
        criteria={
            "optional": "A permitted action the party may take or skip freely.",
            "recommended": "Expected practice, but justified deviation is allowed.",
            "mandatory": "A firm obligation with no discretion to decline.",
        },
    )}
    name = {"optional": "MAY", "recommended": "SHOULD", "mandatory": "MUST"}
    recs = []
    for r in sample:
        a = client.system_one({"requirement": masked(r),
                               "applies_to": r.get("affects") or ["unspecified"]},
                              q).choices["strength"]
        truth = FOLD[r["force"]]
        pred = name[a.choice]
        recs.append({"truth": truth, "pred": pred, "correct": pred == truth,
                     "confidence": a.confidence,
                     "p_mandatory": a.probabilities.get("mandatory", 0.0)})
    acc = statistics.mean(1.0 if x["correct"] else 0.0 for x in recs)
    rho = spearman([x["p_mandatory"] for x in recs], [RANK[x["truth"]] for x in recs])
    dist: dict[str, int] = {}
    for x in recs:
        dist[x["pred"]] = dist.get(x["pred"], 0) + 1
    return {
        "method": "Choice over named obligation levels",
        "n": len(recs),
        "accuracy": round(acc, 4),
        "spearman_rho_on_p_mandatory": round(rho, 4),
        "prediction_distribution": dist,
        "mean_confidence": round(statistics.mean(x["confidence"] for x in recs), 3),
    }


# ---------------------------------------------------------------------------
# 3. Pairwise comparison -- no absolute anchors, pure relative judgment.
# ---------------------------------------------------------------------------

def run_pairwise(client: Any, sample: list[dict], n_pairs: int,
                 rng: random.Random) -> dict:
    by: dict[str, list[dict]] = {}
    for r in sample:
        by.setdefault(FOLD[r["force"]], []).append(r)

    pairs = []
    for a_f, b_f in combinations(LEVELS, 2):
        for _ in range(n_pairs // 3):
            if by.get(a_f) and by.get(b_f):
                pairs.append((rng.choice(by[a_f]), a_f, rng.choice(by[b_f]), b_f))
    rng.shuffle(pairs)

    recs = []
    for r1, f1, r2, f2 in pairs:
        # Randomise presentation order so position cannot encode the answer.
        flip = rng.random() < 0.5
        A, fa, B, fb = (r2, f2, r1, f1) if flip else (r1, f1, r2, f2)
        a = client.system_one(
            {"requirement_A": masked(A), "requirement_B": masked(B)},
            {"which": Choice(
                instructions=(
                    "Both are requirements from a federal cloud-security standard "
                    "with their modal verbs replaced by [MODAL]. Which one imposes "
                    "the stronger obligation on the party it applies to?"
                ),
                criteria={"A": "`requirement_A` is more binding.",
                          "B": "`requirement_B` is more binding.",
                          "equal": "Both impose the same level of obligation."},
            )},
        ).choices["which"]
        truth = "A" if RANK[fa] > RANK[fb] else "B"
        recs.append({"truth": truth, "pred": a.choice,
                     "correct": a.choice == truth,
                     "confidence": a.confidence,
                     "pair": f"{fa} vs {fb}",
                     "said_equal": a.choice == "equal"})

    decided = [x for x in recs if not x["said_equal"]]
    acc_all = statistics.mean(1.0 if x["correct"] else 0.0 for x in recs)
    acc_dec = (statistics.mean(1.0 if x["correct"] else 0.0 for x in decided)
               if decided else None)
    by_pair: dict[str, dict] = {}
    for x in recs:
        d = by_pair.setdefault(x["pair"], {"n": 0, "correct": 0, "equal": 0})
        d["n"] += 1
        d["correct"] += int(x["correct"])
        d["equal"] += int(x["said_equal"])
    for d in by_pair.values():
        d["accuracy"] = round(d["correct"] / d["n"], 3)

    return {
        "method": "pairwise: which requirement is more binding",
        "n": len(recs),
        "accuracy_all": round(acc_all, 4),
        "accuracy_excluding_equal": round(acc_dec, 4) if acc_dec is not None else None,
        "n_said_equal": sum(1 for x in recs if x["said_equal"]),
        "by_pair_type": by_pair,
        "chance_baseline": 0.5,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=45)
    ap.add_argument("--pairs", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    rows = load_frr()
    sample = balanced_sample(rows, args.n, rng)
    counts = {f: sum(1 for r in sample if FOLD[r["force"]] == f) for f in LEVELS}
    print(f"sample: {len(sample)} requirements ({counts})")

    out: dict[str, Any] = {}
    with ForensicRecorder(DB_PATH) as rec:
        run_id = rec.start_run("aion_obligation_probe",
                               notes="Is low obligation accuracy the rubric or the model?")
        with TypeSafeClient() as raw:
            client = rec.wrap(raw, run_id)
            print("1. Score, absolute rubric ...")
            out["score_absolute"] = run_score(client, sample)
            print("2. Choice, named levels ...")
            out["choice_named"] = run_choice(client, sample)
            print("3. Pairwise, relative ...")
            out["pairwise"] = run_pairwise(client, sample, args.pairs, rng)
        rec.end_run(run_id)
        out["_forensics"] = {"run_id": run_id, "integrity": rec.verify_integrity()}

    # Which hypothesis survives?
    s, c, p = out["score_absolute"], out["choice_named"], out["pairwise"]
    pw = p["accuracy_excluding_equal"] or 0.0
    if pw > 0.75 and s["accuracy"] < 0.6:
        verdict = ("H2: the judgment is intact but ABSOLUTE levels are compressed. "
                   "Relative ranking works; absolute classification does not.")
    elif c["accuracy"] > s["accuracy"] + 0.15:
        verdict = ("H1/primitive: Choice recovers what Score loses -- the Score "
                   "rubric, not the judgment, was the limiting factor.")
    elif pw < 0.6:
        verdict = "H3: the model cannot separate these classes by any method."
    else:
        verdict = "mixed; no single explanation dominates."
    out["verdict"] = verdict

    RESULTS.write_text(json.dumps(out, indent=2, default=str))
    print("\n" + "=" * 74)
    print(f"Score  (absolute)  accuracy={s['accuracy']}  rho={s['spearman_rho']}  "
          f"range={s['score_range']}")
    print(f"Choice (named)     accuracy={c['accuracy']}  "
          f"dist={c['prediction_distribution']}")
    print(f"Pairwise (relative) accuracy={p['accuracy_all']} "
          f"(excl. equal: {p['accuracy_excluding_equal']}, chance 0.5)")
    print("=" * 74)
    print(verdict)
    print(f"wrote {RESULTS}")


if __name__ == "__main__":
    main()
