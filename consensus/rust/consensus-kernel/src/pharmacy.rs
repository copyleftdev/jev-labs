//! Pharmacy decision scenarios with ground truth by construction.
//!
//! # Scope and safety
//!
//! This is a SIMULATION HARNESS for testing a consensus protocol. The cases
//! below are synthetic, written to have unambiguous answers so the harness can
//! detect protocol failures. They are not clinical guidance, not validated
//! against any formulary, and nothing here is fit for dispensing decisions.
//!
//! The design principle that makes this defensible: the kernel's job is to
//! decide only when the evidence is unambiguous and to ESCALATE TO A PHARMACIST
//! otherwise. A simulation that never escalates would be the failure, not the
//! success.
//!
//! # Case tiers
//!
//! `Golden` — dead simple, unambiguous. A trained pharmacist answers these
//! without hesitation. These carry the safety invariant: under any amount of
//! chaos the kernel must NEVER return the wrong verdict. Escalating is always
//! permitted; being confidently wrong never is.
//!
//! `Nuanced` — genuinely harder. Correctness is expected but not asserted as
//! an invariant; these measure quality, not safety.
//!
//! `Ambiguous` — deliberately underdetermined. The *correct* behaviour is
//! escalation. A confident decision here is a failure of judgment even if the
//! verdict happens to match someone's opinion.

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum Tier {
    /// Must never be decided wrongly, under any chaos.
    Golden,
    /// Expected correct; measured, not asserted.
    Nuanced,
    /// Correct behaviour is escalation.
    Ambiguous,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum Expected {
    Yes,
    No,
    /// No defensible answer from the record alone.
    Escalate,
}

#[derive(Debug, Clone, Serialize)]
pub struct Scenario {
    pub id: &'static str,
    pub tier: Tier,
    pub question: &'static str,
    /// Paraphrases, one per agent. Prompt diversity is our only cheap source
    /// of decorrelation: identical prompts across agents produced a measured
    /// spread of 0.010, inside the oracle's own 0.042 noise floor, so
    /// identical-prompt agents are one judgment sampled N times.
    pub paraphrases: &'static [&'static str],
    pub record: &'static str,
    pub expected: Expected,
    /// Why this answer is not a matter of opinion.
    pub rationale: &'static str,
}

const CONTROLLED_PARAPHRASES: &[&str] = &[
    "Is the prescribed medication a federally controlled substance?",
    "Does this prescription involve a drug on the DEA controlled substance schedules?",
    "Would dispensing this medication require controlled-substance record keeping?",
    "Is the ordered drug subject to controlled-substance dispensing restrictions?",
    "Does this order name a scheduled narcotic or other controlled drug?",
];

const ALLERGY_PARAPHRASES: &[&str] = &[
    "Does the record show a documented allergy to the drug class being prescribed?",
    "Is the prescribed medication in a class the patient is recorded as allergic to?",
    "Does this order conflict with an allergy already documented for this patient?",
    "Would dispensing this drug contradict a recorded allergy?",
    "Is there a documented hypersensitivity to the class of the ordered medication?",
];

/// Validated by `paraphrase_audit.py`: all five reach the same verdict on
/// every unambiguous record (12/12 clear cases across three question sets).
///
/// Two earlier wordings were REMOVED after failing that audit, and the reason
/// matters. "Can pregnancy be excluded on the basis of this record alone?"
/// scored 0.36 on a negative-hCG record — "alone" reads as a challenge to the
/// sufficiency of a single test. "Does the record contain a negative pregnancy
/// determination?" scored 0.12 for a 58-year-old male — literally true, the
/// record contains no such determination, but it is the wrong question. Both
/// were Jev reading precisely and correctly; the paraphrases were the defect.
///
/// The general trap: a paraphrase that disagrees on a CLEAR case is a
/// different question, and averaging it in makes question drift look like
/// agent independence.
const PREGNANCY_PARAPHRASES: &[&str] = &[
    "Is pregnancy confidently ruled out for this patient by the record?",
    "Does the record establish that this patient is NOT pregnant?",
    "Is there documented evidence sufficient to exclude pregnancy?",
    "Is this patient known not to be pregnant, on the evidence in the record?",
    "Does the record support concluding the patient is not pregnant?",
];

const DUPLICATE_PARAPHRASES: &[&str] = &[
    "Is this order a duplicate of a therapy the patient is already receiving?",
    "Does this prescription duplicate an active medication on the profile?",
    "Is the patient already on a drug from this same therapeutic class?",
    "Would filling this order result in duplicate therapy?",
    "Does an active prescription already cover this same treatment?",
];

pub const SCENARIOS: &[Scenario] = &[
    // ---------------------------------------------------------------
    // GOLDEN: dead simple. Must never be decided wrongly.
    // ---------------------------------------------------------------
    Scenario {
        id: "golden.controlled.oxycodone",
        tier: Tier::Golden,
        question: "controlled_substance",
        paraphrases: CONTROLLED_PARAPHRASES,
        record: "Prescription: oxycodone hydrochloride 5 mg tablets, quantity 30, \
                 one tablet every six hours as needed for pain. Prescriber: Dr. A. Reyes, \
                 DEA registration on file.",
        expected: Expected::Yes,
        rationale: "Oxycodone is a Schedule II controlled substance. Not a matter of \
                    clinical judgement.",
    },
    Scenario {
        id: "golden.controlled.amoxicillin",
        tier: Tier::Golden,
        question: "controlled_substance",
        paraphrases: CONTROLLED_PARAPHRASES,
        record: "Prescription: amoxicillin 500 mg capsules, quantity 21, one capsule \
                 three times daily for seven days. Indication: dental abscess.",
        expected: Expected::No,
        rationale: "Amoxicillin is an antibiotic and is not scheduled.",
    },
    Scenario {
        id: "golden.controlled.fentanyl",
        tier: Tier::Golden,
        question: "controlled_substance",
        paraphrases: CONTROLLED_PARAPHRASES,
        record: "Prescription: fentanyl transdermal system 25 mcg/hr, quantity 5 patches, \
                 apply one patch every 72 hours. Opioid-tolerant patient.",
        expected: Expected::Yes,
        rationale: "Fentanyl is Schedule II.",
    },
    Scenario {
        id: "golden.controlled.lisinopril",
        tier: Tier::Golden,
        question: "controlled_substance",
        paraphrases: CONTROLLED_PARAPHRASES,
        record: "Prescription: lisinopril 10 mg tablets, quantity 90, one tablet daily \
                 for hypertension. Refills: 3.",
        expected: Expected::No,
        rationale: "An ACE inhibitor; not a controlled substance.",
    },
    Scenario {
        id: "golden.allergy.penicillin_conflict",
        tier: Tier::Golden,
        question: "allergy_conflict",
        paraphrases: ALLERGY_PARAPHRASES,
        record: "Allergies on file: PENICILLIN - anaphylaxis, documented 2019, \
                 confirmed by allergist. New order: amoxicillin 500 mg capsules \
                 three times daily.",
        expected: Expected::Yes,
        rationale: "Amoxicillin is a penicillin-class antibiotic and the record \
                    documents anaphylaxis to penicillin.",
    },
    Scenario {
        id: "golden.allergy.no_conflict",
        tier: Tier::Golden,
        question: "allergy_conflict",
        paraphrases: ALLERGY_PARAPHRASES,
        record: "Allergies on file: PENICILLIN - anaphylaxis, documented 2019. \
                 New order: azithromycin 250 mg tablets, five-day course.",
        expected: Expected::No,
        rationale: "Azithromycin is a macrolide, an unrelated class to penicillin.",
    },
    Scenario {
        id: "golden.allergy.nkda",
        tier: Tier::Golden,
        question: "allergy_conflict",
        paraphrases: ALLERGY_PARAPHRASES,
        record: "Allergies on file: NKDA (no known drug allergies), reviewed at intake \
                 today. New order: amoxicillin 500 mg capsules three times daily.",
        expected: Expected::No,
        rationale: "No allergies are documented, so no documented conflict exists.",
    },
    Scenario {
        id: "golden.pregnancy.documented",
        tier: Tier::Golden,
        question: "pregnancy",
        paraphrases: PREGNANCY_PARAPHRASES,
        record: "Chart note: Patient is 24 weeks pregnant, confirmed by ultrasound on \
                 03 March. Obstetric care ongoing. New order: prenatal vitamins.",
        expected: Expected::No,
        rationale: "Pregnancy is confirmed, so it is emphatically NOT ruled out.",
    },
    Scenario {
        id: "golden.pregnancy.absent",
        tier: Tier::Golden,
        question: "pregnancy",
        paraphrases: PREGNANCY_PARAPHRASES,
        record: "Chart note: Male patient, age 58. History of hypertension and type 2 \
                 diabetes. New order: metformin 850 mg twice daily.",
        expected: Expected::Yes,
        rationale: "A male patient: pregnancy is excluded on the record's face.",
    },
    // ---------------------------------------------------------------
    // NUANCED: harder, correctness measured rather than asserted.
    // ---------------------------------------------------------------
    Scenario {
        id: "nuanced.duplicate.same_class",
        tier: Tier::Nuanced,
        question: "duplicate_therapy",
        paraphrases: DUPLICATE_PARAPHRASES,
        record: "Active profile: lisinopril 10 mg daily, started January. \
                 New order: enalapril 5 mg twice daily, new prescriber.",
        expected: Expected::Yes,
        rationale: "Lisinopril and enalapril are both ACE inhibitors; concurrent use \
                    is duplicate therapy.",
    },
    Scenario {
        id: "nuanced.duplicate.different_class",
        tier: Tier::Nuanced,
        question: "duplicate_therapy",
        paraphrases: DUPLICATE_PARAPHRASES,
        record: "Active profile: lisinopril 10 mg daily. New order: amlodipine 5 mg \
                 daily, added for blood pressure control.",
        expected: Expected::No,
        rationale: "An ACE inhibitor plus a calcium channel blocker is a recognised \
                    combination, not duplication.",
    },
    Scenario {
        id: "nuanced.allergy.cross_class",
        tier: Tier::Nuanced,
        question: "allergy_conflict",
        paraphrases: ALLERGY_PARAPHRASES,
        record: "Allergies on file: SULFA drugs - rash, documented 2015. \
                 New order: furosemide 20 mg daily.",
        expected: Expected::No,
        rationale: "Furosemide carries a sulfonamide moiety but is not a sulfa \
                    antibiotic; the documented allergy is to sulfa antibiotics.",
    },
    // ---------------------------------------------------------------
    // AMBIGUOUS: escalation is the correct behaviour.
    // ---------------------------------------------------------------
    Scenario {
        id: "ambiguous.allergy.vague_note",
        tier: Tier::Ambiguous,
        question: "allergy_conflict",
        paraphrases: ALLERGY_PARAPHRASES,
        record: "Allergies on file: 'reaction to antibiotics as a child, details \
                 unknown, mother reported stomach upset'. New order: amoxicillin \
                 500 mg three times daily.",
        expected: Expected::Escalate,
        rationale: "The record does not establish which drug, which class, or whether \
                    the reaction was an allergy at all. A pharmacist must review.",
    },
    Scenario {
        id: "nuanced.pregnancy.not_ruled_out",
        tier: Tier::Nuanced,
        question: "pregnancy",
        paraphrases: PREGNANCY_PARAPHRASES,
        record: "Chart note: Patient reports a late period and has not yet tested. \
                 Declined testing today. New order: isotretinoin 20 mg daily.",
        expected: Expected::No,
        rationale: "Pregnancy is NOT ruled out here, and Jev says so at p~0.03, 115 \
                    of 120 rounds. HISTORY: this case was first tiered Ambiguous with \
                    expected=Escalate, on the reasoning that the ORDER needs a human. \
                    It does. But that is a property of the prescription, not of the \
                    question. The question 'is pregnancy ruled out?' has an unambiguous \
                    answer (no), and a kernel that escalated it would be escalating a \
                    clear signal. The confident 'not ruled out' IS the trigger for the \
                    teratogen hold; making the kernel hedge on it would remove the \
                    signal the downstream rule depends on. Jev was right; the label \
                    was wrong. Nuanced rather than Golden because the record is \
                    inferential (late period + declined test), not a stated fact.",
    },
];

impl Scenario {
    pub fn golden(&self) -> bool {
        matches!(self.tier, Tier::Golden)
    }
}

pub fn golden_scenarios() -> impl Iterator<Item = &'static Scenario> {
    SCENARIOS.iter().filter(|s| s.golden())
}
