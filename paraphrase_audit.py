"""Are our paraphrases semantically equivalent, or are they different questions?

Prompt diversity is our only cheap decorrelation source. But it has a failure
mode that would invalidate the whole design: if the five "paraphrases" actually
ask five DIFFERENT questions, then disagreement between agents is not useful
independence -- it is us asking about different things and averaging the
answers. That would be a bug dressed up as a feature.

This is the falsification test. For each paraphrase set we take records with
KNOWN unambiguous answers and check that every paraphrase agrees on them. A
paraphrase that systematically disagrees on a clear-cut case is not a
paraphrase.

The discipline: disagreement on AMBIGUOUS records is the signal we want.
Disagreement on UNAMBIGUOUS records means the questions differ in meaning.
Only the second invalidates the design.

Run:
    source env.sh && ./.venv/bin/python paraphrase_audit.py
"""

from __future__ import annotations

import json
import os
import statistics

from typesafe_sdk import Noul, TypeSafeClient

from forensics import ForensicRecorder

MODEL = "jev-1.13.0"

# (name, paraphrases, [(record, expected_bool, label)])
SETS = [
    (
        "controlled_substance",
        [
            "Is the prescribed medication a federally controlled substance?",
            "Does this prescription involve a drug on the DEA controlled substance schedules?",
            "Would dispensing this medication require controlled-substance record keeping?",
            "Is the ordered drug subject to controlled-substance dispensing restrictions?",
            "Does this order name a scheduled narcotic or other controlled drug?",
        ],
        [
            ("Prescription: oxycodone hydrochloride 5 mg tablets, quantity 30.", True, "oxycodone"),
            ("Prescription: fentanyl transdermal system 25 mcg/hr, quantity 5 patches.", True, "fentanyl"),
            ("Prescription: amoxicillin 500 mg capsules, quantity 21.", False, "amoxicillin"),
            ("Prescription: lisinopril 10 mg tablets, quantity 90, one daily.", False, "lisinopril"),
            ("Prescription: atorvastatin 20 mg tablets, quantity 30, one daily.", False, "atorvastatin"),
        ],
    ),
    (
        "allergy_conflict",
        [
            "Does the record show a documented allergy to the drug class being prescribed?",
            "Is the prescribed medication in a class the patient is recorded as allergic to?",
            "Does this order conflict with an allergy already documented for this patient?",
            "Would dispensing this drug contradict a recorded allergy?",
            "Is there a documented hypersensitivity to the class of the ordered medication?",
        ],
        [
            ("Allergies: PENICILLIN - anaphylaxis, documented 2019. New order: amoxicillin 500 mg.", True, "pcn_amox"),
            ("Allergies: PENICILLIN - anaphylaxis, documented 2019. New order: azithromycin 250 mg.", False, "pcn_azithro"),
            ("Allergies: NKDA, reviewed today. New order: amoxicillin 500 mg.", False, "nkda"),
            ("Allergies: SULFAMETHOXAZOLE - hives, 2020. New order: sulfamethoxazole-trimethoprim DS.", True, "smx_smx"),
        ],
    ),
    (
        "pregnancy_ruled_out",
        [
            "Is pregnancy confidently ruled out for this patient by the record?",
            "Does the record establish that this patient is NOT pregnant?",
            "Is there documented evidence sufficient to exclude pregnancy?",
            "Is this patient known not to be pregnant, on the evidence in the record?",
            "Does the record support concluding the patient is not pregnant?",
        ],
        [
            ("Chart: Patient is 24 weeks pregnant, confirmed by ultrasound 03 March.", False, "pregnant"),
            ("Chart: Male patient, age 58. Hypertension and type 2 diabetes.", True, "male"),
            ("Chart: Serum hCG negative, drawn today. Patient counselled on contraception.", True, "hcg_neg"),
        ],
    ),
]


def main() -> None:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise SystemExit("TYPESAFE_API_KEY not set -- run: source env.sh")
    raw = TypeSafeClient(api_key=key)
    rec = ForensicRecorder()
    run_id = rec.start_run("paraphrase_audit", "paraphrase equivalence on clear cases")
    client = rec.wrap(raw, run_id)

    report = {}
    print("PARAPHRASE EQUIVALENCE AUDIT")
    print("Question: do these paraphrases mean the same thing on CLEAR cases?\n")

    all_violations = []

    for set_name, paraphrases, cases in SETS:
        print(f"\n{set_name}")
        print("=" * len(set_name))
        # One call per record, all paraphrases batched -- they share a state,
        # so batching is both cheaper and closer to how the kernel runs.
        per_case = {}
        for record, expected, label in cases:
            questions = {
                f"p{i}": Noul(instructions=q) for i, q in enumerate(paraphrases)
            }
            resp = client.system_one(state=record, questions=questions, model=MODEL)
            probs = [resp.nouls[f"p{i}"].noul for i in range(len(paraphrases))]
            per_case[label] = (probs, expected)

            spread = max(probs) - min(probs)
            verdicts = [p > 0.5 for p in probs]
            agree = len(set(verdicts)) == 1
            correct = all(v == expected for v in verdicts)

            status = "OK " if (agree and correct) else "!! "
            print(f"  {status}{label:<14} expected={str(expected):<5} "
                  f"spread={spread:.3f}  probs={[f'{p:.2f}' for p in probs]}")

            if not agree:
                # Which paraphrase dissented?
                majority = statistics.mode(verdicts)
                for i, v in enumerate(verdicts):
                    if v != majority:
                        all_violations.append(
                            f"{set_name}/{label}: paraphrase {i} "
                            f"(p={probs[i]:.2f}) disagreed with the rest"
                        )
                        print(f"      dissent: [{i}] {paraphrases[i]!r} p={probs[i]:.2f}")
            elif not correct:
                all_violations.append(
                    f"{set_name}/{label}: ALL paraphrases agreed but were WRONG "
                    f"(expected {expected})"
                )

        # Per-paraphrase accuracy across this set's clear cases.
        print(f"\n  per-paraphrase accuracy on clear cases:")
        for i, q in enumerate(paraphrases):
            hits = sum(
                1 for (probs, exp) in per_case.values() if (probs[i] > 0.5) == exp
            )
            n = len(per_case)
            marker = "" if hits == n else "   <-- outlier"
            print(f"    [{i}] {hits}/{n}  {q[:58]}{marker}")

        report[set_name] = {
            label: {"probs": p, "expected": e} for label, (p, e) in per_case.items()
        }

    print("\n" + "=" * 62)
    if all_violations:
        print(f"EQUIVALENCE VIOLATIONS: {len(all_violations)}")
        for v in all_violations:
            print(f"  - {v}")
        print()
        print("A paraphrase that disagrees on a CLEAR case is asking a different")
        print("question. Either fix its wording or drop it -- otherwise agent")
        print("disagreement in the kernel is question drift, not independence.")
    else:
        print("ALL PARAPHRASES EQUIVALENT ON CLEAR CASES.")
        print()
        print("Every paraphrase reached the same verdict on every unambiguous")
        print("record. Disagreement seen in the simulation therefore comes from")
        print("genuine uncertainty, not from the questions meaning different")
        print("things. This is what licenses treating them as independent voices.")

    rec.end_run(run_id)
    with open("paraphrase_audit.json", "w") as f:
        json.dump({"sets": report, "violations": all_violations}, f, indent=2)
    print("\nwrote paraphrase_audit.json")


if __name__ == "__main__":
    main()
