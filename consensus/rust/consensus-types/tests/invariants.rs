//! Tests that the wire types enforce the model-checked invariants.
//!
//! The headline test is `tlc_counterexample_is_rejected`: it replays the exact
//! 4-state trace TLC produced for the naive decision rule and asserts the Rust
//! types reject it. That closes the loop from specification to implementation --
//! the bug the model checker found cannot silently reappear in code.

use consensus_types::{
    invariants::stable_votes_for, DecisionPayload, EscalationPayload, EscalationReason, Outcome,
    InvariantError, StabilityEvidence, Verdict, Vote,
};

fn evidence(probability: f64, noise_floor: f64) -> StabilityEvidence {
    let threshold = 0.5;
    let margin = (probability - threshold).abs();
    StabilityEvidence {
        probability,
        threshold,
        noise_floor,
        margin,
        stable: margin > noise_floor,
        noise_floor_source: Some("calib-2026-09-18".into()),
    }
}

fn vote(agent: &str, verdict: Verdict, probability: f64, noise_floor: f64) -> Vote {
    let ev = evidence(probability, noise_floor);
    Vote {
        round_id: "r1".into(),
        agent_id: agent.into(),
        verdict: Box::new(verdict),
        stable: ev.stable,
        attempt: 1,
        evidence: Some(Box::new(ev)),
    }
}

/// TLC counterexample for the naive rule (MCNaive, 4 states):
///
/// ```text
/// State 2: a1 votes yes, stable = FALSE
/// State 3: a2 votes yes, stable = FALSE
/// State 4: DECIDED yes    <- quorum of two votes that could both flip
/// Error: Invariant DecisionIsReproducible is violated.
/// ```
///
/// Both agents sit inside the measured jitter band (margin 0.02 < floor 0.042),
/// so neither vote is stable and the decision must be rejected.
#[test]
fn tlc_counterexample_is_rejected() {
    let decision = DecisionPayload {
        round_id: "r1".into(),
        outcome: Box::new(Verdict::Yes),
        quorum: 2,
        // 0.52 is only 0.02 from the 0.5 boundary -- inside the 0.042 floor.
        supporting_votes: vec![
            vote("a1", Verdict::Yes, 0.52, 0.042),
            vote("a2", Verdict::Yes, 0.52, 0.042),
        ],
    };

    // Precondition: these really are the unstable votes from the trace.
    assert!(!decision.supporting_votes[0].stable);
    assert!(!decision.supporting_votes[1].stable);

    match decision.check_reproducible() {
        Err(InvariantError::UnstableVoteInQuorum { agent_id }) => {
            assert_eq!(agent_id, "a1");
        }
        other => panic!("expected the TLC counterexample to be rejected, got {other:?}"),
    }
}

/// The same shape, but with votes that clear the jitter band, must be accepted.
#[test]
fn stable_quorum_is_accepted() {
    let decision = DecisionPayload {
        round_id: "r1".into(),
        outcome: Box::new(Verdict::Yes),
        quorum: 2,
        supporting_votes: vec![
            vote("a1", Verdict::Yes, 0.97, 0.042),
            vote("a2", Verdict::Yes, 0.93, 0.042),
        ],
    };
    assert!(decision.supporting_votes.iter().all(|v| v.stable));
    assert_eq!(decision.check_reproducible(), Ok(()));
}

#[test]
fn quorum_shortfall_is_rejected() {
    let decision = DecisionPayload {
        round_id: "r1".into(),
        outcome: Box::new(Verdict::Yes),
        quorum: 3,
        supporting_votes: vec![
            vote("a1", Verdict::Yes, 0.97, 0.042),
            vote("a2", Verdict::Yes, 0.95, 0.042),
        ],
    };
    assert_eq!(
        decision.check_reproducible(),
        Err(InvariantError::InsufficientStableVotes {
            required: 3,
            found: 2
        })
    );
}

/// A sender that lies -- claims `stable: true` while the evidence says
/// otherwise -- must be caught. The receiver recomputes rather than trusts.
#[test]
fn forged_stability_flag_is_caught() {
    let mut v = vote("a1", Verdict::Yes, 0.51, 0.042);
    v.stable = true; // forged
    if let Some(ev) = v.evidence.as_mut() {
        ev.stable = true; // forged in the evidence too
    }
    let decision = DecisionPayload {
        round_id: "r1".into(),
        outcome: Box::new(Verdict::Yes),
        quorum: 1,
        supporting_votes: vec![v],
    };
    assert_eq!(
        decision.check_reproducible(),
        Err(InvariantError::StabilityContradictsEvidence {
            agent_id: "a1".into()
        })
    );
}

#[test]
fn supporting_vote_must_match_the_outcome() {
    let decision = DecisionPayload {
        round_id: "r1".into(),
        outcome: Box::new(Verdict::Yes),
        quorum: 2,
        supporting_votes: vec![
            vote("a1", Verdict::Yes, 0.97, 0.042),
            vote("a2", Verdict::No, 0.02, 0.042),
        ],
    };
    assert_eq!(
        decision.check_reproducible(),
        Err(InvariantError::VoteVerdictMismatch {
            agent_id: "a2".into()
        })
    );
}

/// EscalationIsJustified: escalating while a stable quorum existed is a bug.
#[test]
fn escalation_with_available_quorum_is_rejected() {
    let esc = EscalationPayload {
        round_id: "r1".into(),
        outcome: Box::new(Outcome::Uncertain),
        reason: Box::new(EscalationReason::NoStableQuorum),
        observed_votes: vec![
            vote("a1", Verdict::Yes, 0.98, 0.042),
            vote("a2", Verdict::Yes, 0.96, 0.042),
        ],
    };
    assert_eq!(
        esc.check_justified(2),
        Err(InvariantError::EscalationWithStableQuorum { found: 2 })
    );
}

/// The livelock scenario from MCNoEscalate: everyone exhausted, only one
/// stable vote, no quorum. Escalation is the correct outcome.
#[test]
fn escalation_without_quorum_is_justified() {
    let esc = EscalationPayload {
        round_id: "r1".into(),
        outcome: Box::new(Outcome::Uncertain),
        reason: Box::new(EscalationReason::BudgetExhausted),
        observed_votes: vec![
            vote("a1", Verdict::Yes, 0.52, 0.042),
            vote("a2", Verdict::Yes, 0.49, 0.042),
            vote("a3", Verdict::No, 0.01, 0.042),
        ],
    };
    assert_eq!(esc.check_justified(2), Ok(()));
}

#[test]
fn round_id_mismatch_is_caught() {
    let mut v = vote("a1", Verdict::Yes, 0.97, 0.042);
    v.round_id = "r2".into();
    let decision = DecisionPayload {
        round_id: "r1".into(),
        outcome: Box::new(Verdict::Yes),
        quorum: 1,
        supporting_votes: vec![v],
    };
    assert!(matches!(
        decision.check_reproducible(),
        Err(InvariantError::RoundIdMismatch { .. })
    ));
}

/// The noise floor is a measured input, not a constant. A vote stable under a
/// tight floor can be unstable under a looser one -- which is exactly why the
/// floor travels with the evidence.
#[test]
fn stability_depends_on_the_measured_noise_floor() {
    let identity_floor = 0.042; // same request re-sent
    let cohort_floor = 0.073; // semantic restatement

    let p = 0.56; // margin 0.06
    assert!(evidence(p, identity_floor).stable, "clears the tight floor");
    assert!(
        !evidence(p, cohort_floor).stable,
        "does not clear the looser floor"
    );
}

#[test]
fn stable_votes_are_counted_per_verdict() {
    let votes = vec![
        vote("a1", Verdict::Yes, 0.98, 0.042),
        vote("a2", Verdict::Yes, 0.51, 0.042), // unstable
        vote("a3", Verdict::No, 0.01, 0.042),
    ];
    assert_eq!(stable_votes_for(&votes, &Verdict::Yes), 1);
    assert_eq!(stable_votes_for(&votes, &Verdict::No), 1);
}

/// Wire round-trip: the JSON on the wire must deserialize to the same value.
#[test]
fn decision_round_trips_through_json() {
    let decision = DecisionPayload {
        round_id: "r1".into(),
        outcome: Box::new(Verdict::Yes),
        quorum: 2,
        supporting_votes: vec![
            vote("a1", Verdict::Yes, 0.97, 0.042),
            vote("a2", Verdict::Yes, 0.93, 0.042),
        ],
    };
    let json = serde_json::to_string(&decision).expect("serialize");
    assert!(json.contains("\"roundId\""), "camelCase on the wire: {json}");
    assert!(json.contains("\"yes\""), "verdict renders as yes: {json}");

    let back: DecisionPayload = serde_json::from_str(&json).expect("deserialize");
    assert_eq!(back.check_reproducible(), Ok(()));
    assert_eq!(back.supporting_votes.len(), 2);
}
