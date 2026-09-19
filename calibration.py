"""Measure whether Jev's probabilities are actually calibrated.

This tests the foundational claim of the entire product. From the AI primer:

    "Outcomes assigned a probability of 0.2 should occur about 20% of the time.
     Outcomes assigned a probability of 0.8 should occur about 80% of the time."

RLCD -- Reinforcement Learning for Calibrated Decisions -- is the whole bet.
Every downstream pattern TypeSafe documents (confidence gating, composite
scoring, cascades, human escalation) is only sound if that sentence is true.
Nothing else in this repo tests it: the property harness tests *consistency*
(does the answer move when it shouldn't), which is a different question from
*calibration* (does the stated probability match reality).

## Getting defensible ground truth

The usual trap in evaluating a semantic model is that "ground truth" becomes
the tester's own opinion, and the report measures disagreement rather than
miscalibration. We avoid that by CONSTRUCTING items whose label is determined
by the state itself, not by our reading of it: each item carries a structured
record, and the question's correct answer follows from a field in that record.

Difficulty is then varied independently of truth, by controlling how much work
is required to connect the question to the field:

    L0 direct      the field is stated in the terms the question uses
    L1 paraphrase  the field is stated in different words
    L2 inference   the answer follows from combining two fields
    L3 obscured    as L2, with unrelated fields added as distractors

Hard levels deliberately include things jev is documented to be weak at. That
is not a flaw in the design -- it is the point. Calibration is the claim that
the probability TRACKS accuracy, whatever the accuracy happens to be. A model
that is 60% accurate on a bucket and says 0.6 is perfectly calibrated. We need
items spread across the difficulty range or every probability lands in one bin
and the measurement is vacuous.

## What is reported

    ECE   expected calibration error, sample-weighted over bins
    MCE   worst single-bin deviation
    Brier mean squared error of the probability against the outcome
    reliability table with a Wilson score interval per bin

The Wilson intervals matter more than the headline number. With a few dozen
samples in a bin, an observed frequency of 0.62 against a stated 0.70 is not
evidence of anything, and a report that claims otherwise is noise wearing a
lab coat.

Run: source env.sh && ./.venv/bin/python calibration.py --n 240
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from typesafe_sdk import Noul, TypeSafeClient

from forensics import ForensicRecorder

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "forensics.db"
RESULTS_PATH = ROOT / "calibration_results.json"


# ---------------------------------------------------------------------------
# Item construction
# ---------------------------------------------------------------------------

@dataclass
class Item:
    """One labeled judgment. `truth` follows from `state` by construction."""

    item_id: str
    question: str
    state: Any
    truth: bool
    level: str
    family: str
    criteria: dict[str, str] = field(default_factory=dict)


PRODUCTS = ["Model X keyboard", "Aurora monitor", "Pilot headset",
            "Nimbus router", "Cobalt webcam", "Vertex dock",
            "Halcyon speaker", "Quarry tablet", "Onyx stylus", "Lumen lamp"]
CITIES = ["Portland", "Denver", "Atlanta", "Boston", "Phoenix", "Seattle",
          "Tucson", "Raleigh", "Omaha", "Spokane"]
NAMES = ["Rivera", "Okafor", "Lindqvist", "Haddad", "Moreau", "Tanaka",
         "Whitfield", "Brennan", "Sorrentino", "Achebe"]
REFS = ["A-104", "B-2291", "C-77", "D-8410", "E-315", "F-6002",
        "G-118", "H-4523", "J-909", "K-7731"]
DISTRACTOR_FIELDS = [
    ("warehouse_note", "Restocked from the east aisle on the morning shift."),
    ("carrier_memo", "Driver reported light traffic on the interstate."),
    ("packaging", "Ships in a recyclable corrugated mailer."),
    ("marketing_tag", "Featured in the autumn catalogue spread."),
    ("survey_state", "Post-purchase survey not yet delivered."),
    ("badge", "Employee badges must be worn in the building."),
]


def _distractors(rng: random.Random, n: int) -> dict[str, str]:
    return dict(rng.sample(DISTRACTOR_FIELDS, min(n, len(DISTRACTOR_FIELDS))))


# --- Family A: shipment delivered -----------------------------------------

def _family_shipped(rng: random.Random, truth: bool, level: str) -> Item:
    product = rng.choice(PRODUCTS)
    city = rng.choice(CITIES)
    q = "Has this shipment been delivered to the customer?"

    if level == "L0":
        state = {"product": product, "destination": city,
                 "delivery_status": "delivered" if truth else "not delivered"}
    elif level == "L1":
        state = {"product": product, "destination": city,
                 "courier_update": ("Parcel handed to the recipient."
                                    if truth else "Parcel still on the vehicle.")}
    elif level == "L2":
        # Truth follows from combining scan history with the final scan type.
        scans = [{"stage": "origin", "done": True},
                 {"stage": "in_transit", "done": True},
                 {"stage": "out_for_delivery", "done": True},
                 {"stage": "handed_to_recipient", "done": bool(truth)}]
        state = {"product": product, "destination": city, "scan_history": scans}
    else:  # L3
        scans = [{"stage": "origin", "done": True},
                 {"stage": "in_transit", "done": True},
                 {"stage": "out_for_delivery", "done": True},
                 {"stage": "handed_to_recipient", "done": bool(truth)}]
        state = {"product": product, "destination": city, "scan_history": scans,
                 **_distractors(rng, 4)}

    return Item("", q, state, truth, level, "shipment_delivered",
                {"true": "The parcel has reached the customer.",
                 "false": "The parcel has not yet reached the customer."})


# --- Family B: account has an outstanding balance --------------------------

def _family_balance(rng: random.Random, truth: bool, level: str) -> Item:
    q = "Does this account currently owe money?"
    amount = rng.choice([40, 75, 120, 260, 18, 505, 92, 1340])
    holder = rng.choice(NAMES)
    ref = rng.choice(REFS)

    if level == "L0":
        state = {"account_holder": holder, "invoice_ref": ref,
                 "account": {"outstanding_balance": "yes" if truth else "no"}}
    elif level == "L1":
        state = {"account_holder": holder, "invoice_ref": ref,
                 "account": {"ledger_note": (f"An unpaid amount of ${amount} remains."
                                             if truth else "All invoices settled in full.")}}
    elif level == "L2":
        paid = "partial" if truth else "full"
        state = {"account_holder": holder, "invoice_ref": ref,
                 "account": {"invoice_total_label": f"invoice {ref} issued for ${amount}",
                             "payment_received": paid}}
    else:  # L3
        paid = "partial" if truth else "full"
        state = {"account_holder": holder, "invoice_ref": ref,
                 "account": {"invoice_total_label": f"invoice {ref} issued for ${amount}",
                             "payment_received": paid},
                 **_distractors(rng, 4)}

    return Item("", q, state, truth, level, "account_balance",
                {"true": "Money is still owed on the account.",
                 "false": "Nothing is owed on the account."})


# --- Family C: customer asked for a human ----------------------------------

def _family_escalation(rng: random.Random, truth: bool, level: str) -> Item:
    q = "Is the customer asking to speak with a person?"
    name = rng.choice(NAMES)
    ref = rng.choice(REFS)

    wants = [
        "Please connect me to a human agent.",
        "Can I be transferred to a live representative?",
        "I need to talk to an actual person about this.",
        "Put me through to someone on your support team please.",
    ]
    not_wants = [
        "Please send me the setup guide.",
        "Could you email me the documentation link?",
        "Just point me at the troubleshooting article.",
        "Send over the configuration steps when you can.",
    ]
    wants_soft = [
        "I'd rather not keep doing this over chat, can someone call me?",
        "Is there a phone number where a real agent picks up?",
        "This back-and-forth isn't working, who can I speak to directly?",
        "Can somebody from your team reach out to me by phone?",
    ]
    not_wants_soft = [
        "Is there a written walkthrough I can follow myself?",
        "I'd prefer to sort this out on my own if there's a guide.",
        "No need to call, just send instructions I can read.",
        "A self-service article would be ideal if you have one.",
    ]
    wants_hard = [
        "This bot loop isn't getting anywhere and I've tried twice now.",
        "Three automated replies later and I'm still stuck on the same step.",
        "The chatbot keeps repeating itself and none of it applies to me.",
        "I've been going in circles with automated answers since yesterday.",
    ]
    not_wants_hard = [
        "The article you linked covered it, all good now.",
        "Figured it out from the docs, no further help needed.",
        "That guide resolved it, thanks for sending it over.",
        "Worked through the steps myself and it's sorted.",
    ]

    if level == "L0":
        msg = rng.choice(wants if truth else not_wants)
    elif level == "L1":
        msg = rng.choice(wants_soft if truth else not_wants_soft)
    elif level == "L2":
        msg = rng.choice(wants_hard if truth else not_wants_hard)
    else:  # L3
        filler = " ".join(v for _, v in rng.sample(DISTRACTOR_FIELDS, 3))
        core = rng.choice(wants_hard if truth else not_wants_hard)
        msg = f"{filler} {core}"

    return Item("", q, {"customer": name, "ticket_ref": ref, "message": msg},
                truth, level, "wants_human",
                {"true": "The customer wants to reach a person.",
                 "false": "The customer does not want to reach a person."})


FAMILIES = [_family_shipped, _family_balance, _family_escalation]
LEVELS = ["L0", "L1", "L2", "L3"]


def build_corpus(n: int, seed: int = 20260918) -> list[Item]:
    """Balanced over truth value, level, and family -- and DISTINCT.

    Duplicate states are the classic way a calibration report lies to you: the
    same item repeated k times inflates the sample count in a reliability bin
    by k, which shrinks the confidence interval by sqrt(k) and manufactures
    statistical significance out of nothing. An earlier version of this corpus
    produced 240 "samples" from 103 distinct states, and every significance
    flag it emitted was overstated. Distinctness is enforced here rather than
    hoped for.
    """
    rng = random.Random(seed)
    items: list[Item] = []
    seen: set[str] = set()
    attempts = 0
    max_attempts = n * 200

    while len(items) < n and attempts < max_attempts:
        for fam in FAMILIES:
            for level in LEVELS:
                for truth in (True, False):
                    if len(items) >= n:
                        break
                    attempts += 1
                    it = fam(rng, truth, level)
                    key = json.dumps(it.state, sort_keys=True, default=str)
                    if key in seen:
                        continue
                    seen.add(key)
                    it.item_id = f"{it.family}:{level}:{'T' if truth else 'F'}:{len(items)}"
                    items.append(it)

    if len(items) < n:
        print(f"warning: corpus exhausted at {len(items)} distinct items "
              f"(requested {n}) -- add more surface variation to the families",
              file=sys.stderr)
    return items


# ---------------------------------------------------------------------------
# Calibration statistics (pure stdlib)
# ---------------------------------------------------------------------------

def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval -- correct for small n, unlike the normal approx."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def reliability(preds: list[float], truths: list[bool],
                n_bins: int = 10) -> list[dict[str, Any]]:
    """Bin predictions and compare stated probability to observed frequency."""
    bins: list[dict[str, Any]] = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        idx = [i for i, p in enumerate(preds)
               if (p >= lo and p < hi) or (b == n_bins - 1 and p == 1.0)]
        if not idx:
            bins.append({"bin": f"[{lo:.1f},{hi:.1f})", "n": 0})
            continue
        hits = sum(1 for i in idx if truths[i])
        mean_pred = statistics.mean(preds[i] for i in idx)
        obs = hits / len(idx)
        ci = wilson_interval(hits, len(idx))
        bins.append({
            "bin": f"[{lo:.1f},{hi:.1f})",
            "n": len(idx),
            "mean_predicted": round(mean_pred, 4),
            "observed_frequency": round(obs, 4),
            "ci95_low": round(ci[0], 4),
            "ci95_high": round(ci[1], 4),
            "gap": round(obs - mean_pred, 4),
            # A bin is only evidence of miscalibration if the stated probability
            # falls outside the interval the observations can support.
            "significant": not (ci[0] <= mean_pred <= ci[1]),
        })
    return bins


def ece_mce(bins: list[dict[str, Any]], total: int) -> tuple[float, float]:
    ece = 0.0
    mce = 0.0
    for b in bins:
        if not b["n"]:
            continue
        gap = abs(b["gap"])
        ece += (b["n"] / total) * gap
        mce = max(mce, gap)
    return ece, mce


def brier(preds: list[float], truths: list[bool]) -> float:
    return statistics.mean((p - (1.0 if t else 0.0)) ** 2
                           for p, t in zip(preds, truths))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=240)
    ap.add_argument("--bins", type=int, default=10)
    args = ap.parse_args()

    corpus = build_corpus(args.n)
    distinct = len({json.dumps(i.state, sort_keys=True, default=str) for i in corpus})
    print(f"corpus: {len(corpus)} items, {distinct} distinct states  "
          f"({sum(i.truth for i in corpus)} true / "
          f"{sum(not i.truth for i in corpus)} false)")
    if distinct < len(corpus):
        print(f"  WARNING: {len(corpus) - distinct} duplicate states -- "
              f"confidence intervals will be overstated", file=sys.stderr)

    preds: list[float] = []
    truths: list[bool] = []
    records: list[dict[str, Any]] = []

    with ForensicRecorder(DB_PATH) as rec:
        run_id = rec.start_run(
            "calibration",
            notes=f"Reliability of Jev probabilities, n={args.n}, constructed ground truth",
        )
        with TypeSafeClient() as raw:
            client = rec.wrap(raw, run_id)
            for k, item in enumerate(corpus):
                resp = client.system_one(
                    item.state,
                    {"q": Noul(instructions=item.question, criteria=item.criteria)},
                )
                p = resp.nouls["q"].noul
                preds.append(p)
                truths.append(item.truth)
                records.append({
                    "item_id": item.item_id, "family": item.family,
                    "level": item.level, "truth": item.truth, "p": round(p, 4),
                    "correct": (p > 0.5) == item.truth,
                })
                if (k + 1) % 20 == 0:
                    print(f"  {k + 1}/{len(corpus)}", flush=True)
        rec.end_run(run_id)
        integrity = rec.verify_integrity()
        cost = dict(rec.query("SELECT * FROM v_run_cost WHERE run_id = ?", (run_id,))[0])

    bins = reliability(preds, truths, args.bins)
    ece, mce = ece_mce(bins, len(preds))
    bs = brier(preds, truths)
    acc = statistics.mean(1.0 if r["correct"] else 0.0 for r in records)

    # Per-level breakdown: does the model KNOW which items are hard?
    by_level = {}
    for lvl in LEVELS:
        sub = [r for r in records if r["level"] == lvl]
        if not sub:
            continue
        sub_p = [r["p"] for r in sub]
        sub_t = [r["truth"] for r in sub]
        # Confidence here = distance from 0.5, the Noul analogue of certainty.
        by_level[lvl] = {
            "n": len(sub),
            "accuracy": round(statistics.mean(1.0 if r["correct"] else 0.0 for r in sub), 4),
            "mean_certainty": round(statistics.mean(abs(p - 0.5) * 2 for p in sub_p), 4),
            "brier": round(brier(sub_p, sub_t), 4),
        }

    by_family = {}
    for fam in {r["family"] for r in records}:
        sub = [r for r in records if r["family"] == fam]
        by_family[fam] = {
            "n": len(sub),
            "accuracy": round(statistics.mean(1.0 if r["correct"] else 0.0 for r in sub), 4),
            "brier": round(brier([r["p"] for r in sub], [r["truth"] for r in sub]), 4),
        }

    sig = [b for b in bins if b.get("n") and b.get("significant")]

    out = {
        "run_id": run_id,
        "n": len(preds),
        "headline": {
            "accuracy": round(acc, 4),
            "ECE": round(ece, 4),
            "MCE": round(mce, 4),
            "brier": round(bs, 4),
            "brier_of_always_0.5": 0.25,
            "significantly_miscalibrated_bins": len(sig),
        },
        "reliability": bins,
        "by_difficulty": by_level,
        "by_family": by_family,
        "forensics": {"integrity": integrity, "cost": cost},
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))

    print("\n" + "=" * 78)
    print(f"n={len(preds)}  accuracy={acc:.3f}  ECE={ece:.4f}  MCE={mce:.4f}  Brier={bs:.4f}")
    print("=" * 78)
    print(f"{'bin':<12}{'n':>5}{'stated':>9}{'observed':>10}{'95% CI':>18}{'gap':>8}  flag")
    for b in bins:
        if not b["n"]:
            continue
        ci = f"[{b['ci95_low']:.2f},{b['ci95_high']:.2f}]"
        flag = "MISCALIBRATED" if b["significant"] else ""
        print(f"{b['bin']:<12}{b['n']:>5}{b['mean_predicted']:>9.3f}"
              f"{b['observed_frequency']:>10.3f}{ci:>18}{b['gap']:>8.3f}  {flag}")
    print("\nby difficulty:")
    for lvl, v in by_level.items():
        print(f"  {lvl}  n={v['n']:<4} accuracy={v['accuracy']:.3f}  "
              f"mean_certainty={v['mean_certainty']:.3f}  brier={v['brier']:.4f}")
    print("\nby family:")
    for fam, v in by_family.items():
        print(f"  {fam:22} n={v['n']:<4} accuracy={v['accuracy']:.3f}  brier={v['brier']:.4f}")
    print(f"\nwrote {RESULTS_PATH}")


if __name__ == "__main__":
    main()
