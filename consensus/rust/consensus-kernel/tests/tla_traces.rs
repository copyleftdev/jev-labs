//! Replay the TLA+ model-checker's traces through the real coordinator.
//!
//! These tests are the executable end of the verification chain. The spec
//! proves the protocol correct; these prove *this implementation* behaves the
//! way the proof assumes, on the exact scenarios TLC identified.
//!
//! Re-check the spec with `spec/check.sh` if any of these fail: either the
//! code drifted from the model, or the model changed.

use consensus_kernel::{
    Calibration, Oracle, QuorumPolicy, Round, RoundConfig, RoundOutcome, ScriptedOracle,
};
use consensus_types::{EscalationReason, Verdict};

fn cfg(agents: usize, quorum: usize, max_asks: i32) -> RoundConfig {
    RoundConfig {
        round_id: "r1".into(),
        policy: QuorumPolicy::new(agents, 0, quorum).expect("sound quorum"),
        max_asks,
    }
}

/// Identity floor: the oracle re-asked with a byte-identical request.
/// Measured at 0.042 over 1,406 calls.
fn calib() -> Calibration {
    Calibration::identity("calib-2026-09-18-identity")
}

/// **MCNaive counterexample.**
///
/// ```text
/// State 2: a1 votes yes, stable = FALSE
/// State 3: a2 votes yes, stable = FALSE
/// State 4: DECIDED yes    <- naive rule commits on flippable votes
/// Error: Invariant DecisionIsReproducible is violated.
/// ```
///
/// Both agents sit at 0.52 -- margin 0.02, inside the 0.042 floor. The naive
/// rule would decide `yes` here. Our coordinator must NOT, because the stable
/// gate is what makes a decision reproducible.
#[test]
fn naive_counterexample_does_not_decide() {
    let oracle = ScriptedOracle::new(
        vec![
            ("a1", vec![0.52]),
            ("a2", vec![0.52]),
            ("a3", vec![0.52]),
        ],
        calib(),
    );
    let mut round = Round::new(cfg(3, 2, 1), &["a1", "a2", "a3"], "q?", "state", &oracle);
    let outcome = round.run().expect("oracle ok");

    match outcome {
        RoundOutcome::Escalated(e) => {
            assert_eq!(*e.reason, EscalationReason::BudgetExhausted);
            assert_eq!(e.observed_votes.len(), 3);
            assert!(
                e.observed_votes.iter().all(|v| !v.stable),
                "every vote was inside the jitter band"
            );
        }
        RoundOutcome::Decided(d) => panic!(
            "committed to {:?} on unstable votes -- DecisionIsReproducible violated",
            d.outcome
        ),
    }
}

/// **MCNoEscalate livelock.**
///
/// ```text
/// State 7: asked = (a1:2, a2:2, a3:2)   <- everyone exhausted
///          stable = (a1:FALSE, a2:FALSE, a3:TRUE)
/// State 9: Stuttering                   <- hangs forever without escalation
/// ```
///
/// One stable vote, quorum of 2, budget spent. Without the escalation path
/// this hangs. With it, the round terminates in bounded time.
#[test]
fn livelock_scenario_terminates_via_escalation() {
    let oracle = ScriptedOracle::new(
        vec![
            ("a1", vec![0.52, 0.49]), // never settles
            ("a2", vec![0.51, 0.53]), // never settles
            ("a3", vec![0.01, 0.01]), // stable no, but alone
        ],
        calib(),
    );
    let mut round = Round::new(cfg(3, 2, 2), &["a1", "a2", "a3"], "q?", "state", &oracle);
    let outcome = round.run().expect("oracle ok");

    let RoundOutcome::Escalated(e) = outcome else {
        panic!("expected escalation, the scenario has no stable quorum");
    };
    assert_eq!(*e.reason, EscalationReason::BudgetExhausted);

    // Liveness is bounded, not merely eventual: each agent asked at most twice.
    for a in ["a1", "a2", "a3"] {
        assert!(round.asked(a) <= 2, "{a} exceeded its ask budget");
    }
    // The stable minority vote is preserved as evidence.
    assert_eq!(e.observed_votes.iter().filter(|v| v.stable).count(), 1);
}

/// A clear question decides on the first pass, with no wasted consultations.
#[test]
fn stable_quorum_decides_immediately() {
    let oracle = ScriptedOracle::new(
        vec![
            ("a1", vec![0.97]),
            ("a2", vec![0.96]),
            ("a3", vec![0.02]),
        ],
        calib(),
    );
    let mut round = Round::new(cfg(3, 2, 3), &["a1", "a2", "a3"], "q?", "state", &oracle);
    let outcome = round.run().expect("oracle ok");

    let RoundOutcome::Decided(d) = outcome else {
        panic!("expected a decision");
    };
    assert_eq!(*d.outcome, Verdict::Yes);
    assert_eq!(d.supporting_votes.len(), 2);
    assert!(d.supporting_votes.iter().all(|v| v.stable));

    // The decision must satisfy the invariant it claims to satisfy.
    assert_eq!(d.check_reproducible(), Ok(()));

    // Budget discipline: nobody was re-asked after settling.
    assert!(oracle.call_count() <= 3, "no wasted oracle calls");
}

/// An unstable vote that settles on re-ask should be used. This is the case
/// that justifies `Reconsult` existing at all.
#[test]
fn unstable_vote_settles_on_reconsult() {
    let oracle = ScriptedOracle::new(
        vec![
            ("a1", vec![0.52, 0.98]), // jittery, then decisive
            ("a2", vec![0.51, 0.97]),
            ("a3", vec![0.50, 0.50]),
        ],
        calib(),
    );
    let mut round = Round::new(cfg(3, 2, 3), &["a1", "a2", "a3"], "q?", "state", &oracle);
    let outcome = round.run().expect("oracle ok");

    let RoundOutcome::Decided(d) = outcome else {
        panic!("expected a decision once the votes settled");
    };
    assert_eq!(*d.outcome, Verdict::Yes);
    assert_eq!(d.check_reproducible(), Ok(()));
    assert!(
        d.supporting_votes.iter().all(|v| v.attempt >= 2),
        "the decisive votes came from re-asks"
    );
}

/// TLA+ `Crash(a)`: a crashed agent leaves `Live` and stops counting toward
/// quorum, even if it had already voted.
#[test]
fn crashed_agent_is_excluded_from_quorum() {
    let oracle = ScriptedOracle::new(
        vec![
            ("a1", vec![0.98]),
            ("a2", vec![0.97]),
            ("a3", vec![0.96]),
        ],
        calib(),
    );
    let mut round = Round::new(cfg(3, 3, 1), &["a1", "a2", "a3"], "q?", "state", &oracle);

    // One consultation each, then lose an agent before deciding.
    for a in ["a1", "a2", "a3"] {
        round.consult(a).expect("oracle ok");
    }
    round.mark_crashed("a3");

    let outcome = round.try_decide().expect("round must terminate");
    let RoundOutcome::Escalated(e) = outcome else {
        panic!("quorum of 3 is unreachable with only 2 live agents");
    };
    assert_eq!(*e.reason, EscalationReason::InsufficientLiveAgents);
    assert_eq!(e.observed_votes.len(), 2, "crashed agent's vote is dropped");
}

/// TLA+ `Irrevocable`: once an outcome is reached it never changes.
#[test]
fn outcome_is_irrevocable() {
    let oracle = ScriptedOracle::new(
        vec![("a1", vec![0.99]), ("a2", vec![0.98])],
        calib(),
    );
    let mut round = Round::new(cfg(2, 2, 3), &["a1", "a2"], "q?", "state", &oracle);
    let first = round.run().expect("oracle ok");
    let second = round.try_decide().expect("outcome is retained");
    assert_eq!(first, second);

    // Further consultation after the outcome is refused.
    assert!(!round.consult("a1").expect("oracle ok"));
}

/// The ask budget is a hard bound. This is what makes liveness *bounded*
/// rather than merely eventual.
#[test]
fn ask_budget_is_never_exceeded() {
    let oracle = ScriptedOracle::new(
        vec![
            ("a1", vec![0.52, 0.51, 0.52, 0.51]),
            ("a2", vec![0.49, 0.48, 0.49, 0.48]),
        ],
        calib(),
    );
    let mut round = Round::new(cfg(2, 2, 2), &["a1", "a2"], "q?", "state", &oracle);
    let outcome = round.run().expect("oracle ok");

    assert!(!outcome.is_decided(), "no stable quorum was ever available");
    assert_eq!(round.asked("a1"), 2);
    assert_eq!(round.asked("a2"), 2);
    assert_eq!(oracle.call_count(), 4, "exactly max_asks * agents");
}

/// An oracle failure must be recorded, not silently treated as a vote — but
/// it must also not abort a round the remaining agents can still complete.
///
/// A transport blip that voids an otherwise sound consensus is itself a hazard
/// in a regulated setting: it pushes load onto human review for no clinical
/// reason. The failing agent drops out; the round proceeds or escalates on its
/// own terms.
#[test]
fn oracle_failure_degrades_rather_than_aborting() {
    let oracle = ScriptedOracle::new(
        vec![("a1", vec![0.9]), ("a2", vec![0.9])],
        calib(),
    )
    .failing_on_call(1);
    let mut round = Round::new(cfg(2, 2, 2), &["a1", "a2"], "q?", "state", &oracle);

    let outcome = round.run().expect("a failing agent must not abort the round");

    // The failure is surfaced, not swallowed. (The scripted oracle counts only
    // successful calls, so `failing_on_call(1)` keeps firing until the agent
    // exhausts its budget — which is itself the behaviour we want: a
    // persistently unreachable agent drops out rather than looping forever.)
    assert!(!round.oracle_failures().is_empty());
    assert!(round.oracle_failures()[0].1.contains("transport"));

    // With one agent lost and a quorum of 2, the round escalates with evidence
    // rather than returning a raw transport error.
    assert!(!outcome.is_decided());
}

/// The floor travels with the evidence: the same probabilities decide under a
/// tight calibration and escalate under a loose one. This is why the noise
/// floor is measured per transform class rather than fixed globally.
#[test]
fn calibration_choice_changes_the_outcome() {
    let probs = vec![("a1", vec![0.56]), ("a2", vec![0.57])];

    let tight = ScriptedOracle::new(probs.clone(), Calibration::new(0.5, 0.042, "identity"));
    let mut r1 = Round::new(cfg(2, 2, 1), &["a1", "a2"], "q?", "s", &tight);
    assert!(r1.run().expect("ok").is_decided(), "clears the tight floor");

    let loose = ScriptedOracle::new(probs, Calibration::new(0.5, 0.073, "cohort"));
    let mut r2 = Round::new(cfg(2, 2, 1), &["a1", "a2"], "q?", "s", &loose);
    assert!(
        !r2.run().expect("ok").is_decided(),
        "same probabilities, looser floor, no decision"
    );
}

/// Escalation payloads must carry enough evidence for a receiver to confirm
/// `EscalationIsJustified` without trusting the coordinator.
#[test]
fn escalation_is_independently_verifiable() {
    let oracle = ScriptedOracle::new(
        vec![
            ("a1", vec![0.52]),
            ("a2", vec![0.51]),
            ("a3", vec![0.98]),
        ],
        calib(),
    );
    let mut round = Round::new(cfg(3, 2, 1), &["a1", "a2", "a3"], "q?", "state", &oracle);
    let RoundOutcome::Escalated(e) = round.run().expect("ok") else {
        panic!("one stable vote cannot meet a quorum of 2");
    };
    assert_eq!(e.check_justified(2), Ok(()));
}
