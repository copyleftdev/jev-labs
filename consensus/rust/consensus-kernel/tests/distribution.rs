//! Tests for the distribution and adversarial gaps closed in V3.
//!
//! Each test corresponds to a scenario TLC explored in
//! `spec/SemanticConsensusV3.tla`:
//!
//! | gap | spec invariant | test |
//! |---|---|---|
//! | forged stability | `SoundDecision` | `forged_evidence_*` |
//! | split observation | `Agreement` | `two_coordinators_*` |
//! | message loss | `Agreement` | `lost_votes_*` |
//! | coordinator crash | `Agreement` | `successor_*` |
//!
//! The quorum bound `2Q > N and Q > 2f` was derived by sweeping TLC over 22
//! configurations (`spec/sweep_quorum.sh`), not assumed from the literature.

use consensus_kernel::{
    Calibration, QuorumError, QuorumPolicy, Round, RoundConfig, RoundOutcome, ScriptedOracle,
};
use consensus_types::{StabilityEvidence, Verdict, Vote};

fn calib() -> Calibration {
    Calibration::identity("calib-2026-09-18-identity")
}

fn cfg(agents: usize, byzantine: usize, quorum: usize, max_asks: i32) -> RoundConfig {
    RoundConfig {
        round_id: "r1".into(),
        policy: QuorumPolicy::new(agents, byzantine, quorum).expect("sound quorum"),
        max_asks,
    }
}

// ---------------------------------------------------------------------------
// Gap A: forged stability evidence
// ---------------------------------------------------------------------------

/// An honest agent's claim is recomputable from its evidence. A vote whose
/// `stable` flag contradicts `margin > noise_floor` is detectable by anyone.
#[test]
fn forged_evidence_that_is_arithmetically_wrong_is_caught() {
    let ev = StabilityEvidence {
        probability: 0.51,
        threshold: 0.5,
        noise_floor: 0.042,
        margin: 0.01,
        stable: true, // lie: 0.01 is not > 0.042
        noise_floor_source: Some("forged".into()),
    };
    assert!(!ev.is_self_consistent());
    assert!(!ev.recomputed_stable());
}

/// The harder case: evidence that is internally consistent but fabricated.
/// Recomputation cannot detect this — the arithmetic checks out. This is why
/// the quorum bound must assume forgers exist rather than hope to spot them.
#[test]
fn forged_evidence_that_is_self_consistent_is_undetectable_locally() {
    let ev = StabilityEvidence {
        probability: 0.99, // never actually returned by the oracle
        threshold: 0.5,
        noise_floor: 0.042,
        margin: 0.49,
        stable: true,
        noise_floor_source: Some("forged".into()),
    };
    // Locally it passes every check we can make.
    assert!(ev.is_self_consistent());
    // Which is exactly why Q > 2f is required: soundness comes from the
    // honest majority inside any quorum, not from detecting the liar.
    assert!(QuorumPolicy::new(3, 1, 2).is_err(), "Q=2 <= 2f=2 is unsound");
    assert!(QuorumPolicy::new(3, 1, 3).is_ok());
}

/// The bound refuses configurations where forgers could carry a decision.
#[test]
fn unsound_quorum_is_rejected_at_construction() {
    // Q <= 2f: one forger plus one honest agent is a quorum of 2.
    assert_eq!(
        QuorumPolicy::new(4, 1, 2).unwrap_err(),
        QuorumError::QuorumsMayNotIntersect {
            agents: 4,
            quorum: 2
        }
    );
    assert_eq!(
        QuorumPolicy::new(5, 2, 3).unwrap_err(),
        QuorumError::NoHonestMajority {
            quorum: 3,
            byzantine: 2
        }
    );
}

// ---------------------------------------------------------------------------
// Gap B/C: split observation, message loss, coordinator succession
// ---------------------------------------------------------------------------

/// Simulate two coordinators with DIFFERENT observed subsets of the same
/// votes — the split-brain condition. With an intersecting quorum they cannot
/// commit different verdicts, because any two quorums share an agent and an
/// agent casts one vote.
#[test]
fn two_coordinators_with_split_views_cannot_diverge() {
    // 4 agents: two would vote yes, two would vote no.
    let votes = vec![
        mk_vote("a1", Verdict::Yes, 0.98),
        mk_vote("a2", Verdict::Yes, 0.97),
        mk_vote("a3", Verdict::No, 0.02),
        mk_vote("a4", Verdict::No, 0.03),
    ];

    // Quorum 3 of 4 satisfies 2Q > N.
    let policy = QuorumPolicy::new(4, 0, 3).expect("sound");
    let q = policy.quorum();

    // c1 sees {a1,a2,a3}; c2 sees {a2,a3,a4}. Neither can reach 3 agreeing.
    let c1: Vec<_> = votes[0..3].to_vec();
    let c2: Vec<_> = votes[1..4].to_vec();

    assert!(decide(&c1, q).is_none(), "no verdict has 3 votes in c1's view");
    assert!(decide(&c2, q).is_none(), "nor in c2's view");
}

/// The unsound configuration TLC flagged: quorum 2 of 4 permits disjoint
/// quorums, and two coordinators commit opposite verdicts. We assert the
/// policy layer refuses to build it at all.
#[test]
fn split_brain_configuration_is_unconstructible() {
    // This is the MCSplitBrain config: N=4, Q=2, two coordinators.
    // TLC: "Error: Invariant Agreement is violated."
    let err = QuorumPolicy::new(4, 0, 2).unwrap_err();
    assert_eq!(
        err,
        QuorumError::QuorumsMayNotIntersect {
            agents: 4,
            quorum: 2
        }
    );
    // And the safe sibling is accepted.
    assert!(QuorumPolicy::new(4, 0, 3).is_ok());
}

/// Message loss: a coordinator that never receives some votes must not decide
/// on fewer than a quorum. Losing votes degrades to escalation, never to a
/// weaker decision.
#[test]
fn lost_votes_degrade_to_escalation_not_to_a_weak_decision() {
    let all = vec![
        mk_vote("a1", Verdict::Yes, 0.98),
        mk_vote("a2", Verdict::Yes, 0.97),
        mk_vote("a3", Verdict::Yes, 0.96),
    ];
    let policy = QuorumPolicy::new(3, 0, 2).expect("sound");

    // All delivered: decides.
    assert_eq!(decide(&all, policy.quorum()), Some(Verdict::Yes));

    // One lost: still a quorum of 2.
    assert_eq!(decide(&all[0..2], policy.quorum()), Some(Verdict::Yes));

    // Two lost: below quorum, must not decide.
    assert_eq!(decide(&all[0..1], policy.quorum()), None);
}

/// A successor coordinator, taking over after a crash with only the votes it
/// can see, reaches the same verdict or none — never a conflicting one.
#[test]
fn successor_coordinator_cannot_contradict_its_predecessor() {
    let votes = vec![
        mk_vote("a1", Verdict::Yes, 0.98),
        mk_vote("a2", Verdict::Yes, 0.97),
        mk_vote("a3", Verdict::Yes, 0.95),
        mk_vote("a4", Verdict::No, 0.02),
        mk_vote("a5", Verdict::No, 0.01),
    ];
    let policy = QuorumPolicy::new(5, 0, 3).expect("sound");
    let q = policy.quorum();

    let predecessor = decide(&votes, q);
    assert_eq!(predecessor, Some(Verdict::Yes));

    // Every 3-subset a successor might observe.
    let n = votes.len();
    for i in 0..n {
        for j in (i + 1)..n {
            for k in (j + 1)..n {
                let view = vec![votes[i].clone(), votes[j].clone(), votes[k].clone()];
                match decide(&view, q) {
                    None => {}
                    Some(v) => assert_eq!(
                        v,
                        Verdict::Yes,
                        "successor decided {v:?}, contradicting the predecessor"
                    ),
                }
            }
        }
    }
}

/// Crash tolerance is a property of the configuration and should be stated,
/// not discovered in production.
#[test]
fn crash_tolerance_is_explicit() {
    let p = QuorumPolicy::new(5, 0, 3).unwrap();
    assert_eq!(p.crash_tolerance(), 2);
    assert!(p.tolerates_crashes(2));
    assert!(!p.tolerates_crashes(3), "3 crashes leaves 2 < quorum 3");
}

/// End to end with the real coordinator: a sound 5-agent configuration
/// tolerating one forger.
#[test]
fn byzantine_tolerant_round_decides_on_honest_majority() {
    // Q must exceed 2f = 2 and 2Q > 5, so Q = 3.
    let policy = QuorumPolicy::minimum(5, 1).expect("possible");
    assert_eq!(policy.quorum(), 3);

    let oracle = ScriptedOracle::new(
        vec![
            ("a1", vec![0.98]),
            ("a2", vec![0.97]),
            ("a3", vec![0.96]),
            ("a4", vec![0.95]),
            ("a5", vec![0.99]),
        ],
        calib(),
    );
    let mut round = Round::new(
        RoundConfig {
            round_id: "r-byz".into(),
            policy,
            max_asks: 2,
        },
        &["a1", "a2", "a3", "a4", "a5"],
        "q?",
        "state",
        &oracle,
    );
    let RoundOutcome::Decided(d) = round.run().expect("ok") else {
        panic!("a clear question with 5 agreeing agents must decide");
    };
    assert_eq!(*d.outcome, Verdict::Yes);
    assert!(d.supporting_votes.len() >= 3);
    assert_eq!(d.check_reproducible(), Ok(()));
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

fn mk_vote(agent: &str, verdict: Verdict, probability: f64) -> Vote {
    let (v, ev) = calib().classify(probability);
    assert_eq!(v, verdict, "{agent}: probability must yield the stated verdict");
    Vote {
        round_id: "r1".into(),
        agent_id: agent.into(),
        verdict: Box::new(verdict),
        stable: ev.stable,
        attempt: 1,
        evidence: Some(Box::new(ev)),
    }
}

/// The decision rule as a coordinator applies it to an observed vote set:
/// a verdict wins only with a quorum of stable votes.
fn decide(observed: &[Vote], quorum: usize) -> Option<Verdict> {
    for verdict in [Verdict::Yes, Verdict::No] {
        let n = observed
            .iter()
            .filter(|v| v.stable && *v.verdict == verdict)
            .count();
        if n >= quorum {
            return Some(verdict);
        }
    }
    None
}
