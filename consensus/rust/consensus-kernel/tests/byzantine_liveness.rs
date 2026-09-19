//! Liveness under Byzantine faults: the availability question.
//!
//! Safety asks "can a forger make us decide wrongly?" — answered by `Q > 2f`
//! in `distribution.rs`. This file asks the *availability* question:
//!
//!   Can a forger make us escalate when the honest agents alone held a
//!   decisive stable quorum?
//!
//! Every forced escalation costs a human review, so an adversary able to
//! trigger them at will has a denial-of-service against the review queue even
//! though no wrong decision is ever made.
//!
//! The answer, model-checked in `spec/SemanticConsensusV4.tla` across 138,129
//! states, is **no** — and the reason is monotonicity, which is what these
//! tests pin down:
//!
//! > An honest agent's claim equals its truth, so the claimed-stable set is
//! > always a superset of the honest-stable set. A forger can only ADD to a
//! > claimed set, never remove from one. An honest quorum therefore always
//! > surfaces as a claimed quorum, which disables escalation.
//!
//! The operationally important corollary: `Q > 2f` buys *correctness*, not
//! *availability*. There is no availability argument for weakening it.

use consensus_kernel::{Calibration, QuorumPolicy, Round, RoundConfig, RoundOutcome, ScriptedOracle};
use consensus_types::{Verdict, Vote};

fn calib() -> Calibration {
    Calibration::identity("calib-2026-09-18-identity")
}

fn mk_vote(agent: &str, probability: f64) -> Vote {
    let (verdict, ev) = calib().classify(probability);
    Vote {
        round_id: "r1".into(),
        agent_id: agent.into(),
        verdict: Box::new(verdict),
        stable: ev.stable,
        attempt: 1,
        evidence: Some(Box::new(ev)),
    }
}

/// A forged vote: the agent reports whatever it likes, with self-consistent
/// evidence that recomputation cannot catch.
fn forged_vote(agent: &str, verdict: Verdict, claimed_probability: f64) -> Vote {
    let (_, ev) = calib().classify(claimed_probability);
    Vote {
        round_id: "r1".into(),
        agent_id: agent.into(),
        verdict: Box::new(verdict),
        stable: ev.stable,
        attempt: 1,
        evidence: Some(Box::new(ev)),
    }
}

/// The coordinator's decision rule over an observed vote set.
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

/// TLA+ `ClaimedCoversHonest`: adding forged votes never removes honest ones.
///
/// This is the structural fact the whole liveness result rests on. If it ever
/// stopped holding, `NoForcedEscalation` would need re-deriving.
#[test]
fn forged_votes_can_only_add_never_subtract() {
    let honest = vec![mk_vote("a1", 0.98), mk_vote("a2", 0.97), mk_vote("a3", 0.96)];
    let honest_yes = honest.iter().filter(|v| v.stable).count();

    let mut with_forger = honest.clone();
    with_forger.push(forged_vote("a4", Verdict::No, 0.02));
    with_forger.push(forged_vote("a5", Verdict::No, 0.01));

    let still_yes = with_forger
        .iter()
        .filter(|v| v.stable && *v.verdict == Verdict::Yes)
        .count();

    assert_eq!(
        still_yes, honest_yes,
        "forged votes must not diminish the honest stable set"
    );
}

/// The headline: two forgers voting the opposite way cannot force escalation
/// when the honest agents hold a quorum.
#[test]
fn forgers_cannot_force_escalation_against_an_honest_quorum() {
    let policy = QuorumPolicy::minimum(5, 1).expect("sound");
    assert_eq!(policy.quorum(), 3);

    // Three honest agents genuinely agree and are stable.
    let mut votes = vec![mk_vote("a1", 0.98), mk_vote("a2", 0.97), mk_vote("a3", 0.96)];
    assert_eq!(decide(&votes, policy.quorum()), Some(Verdict::Yes));

    // A forger joins, claiming a stable vote for the opposite verdict.
    votes.push(forged_vote("a5", Verdict::No, 0.02));

    assert_eq!(
        decide(&votes, policy.quorum()),
        Some(Verdict::Yes),
        "the honest quorum still decides; the forger only adds noise"
    );
}

/// The forger cannot flip the outcome either — `NoContradictionOfHonestQuorum`.
#[test]
fn forgers_cannot_contradict_an_honest_quorum() {
    let policy = QuorumPolicy::minimum(5, 1).expect("sound");

    let votes = vec![
        mk_vote("a1", 0.98),
        mk_vote("a2", 0.97),
        mk_vote("a3", 0.96),
        forged_vote("a4", Verdict::No, 0.01),
        forged_vote("a5", Verdict::No, 0.02),
    ];

    // Two forgers is beyond the f=1 the policy was sized for, yet they still
    // cannot reach quorum 3 on their own.
    assert_eq!(decide(&votes, policy.quorum()), Some(Verdict::Yes));
}

/// With `f` forgers at the sound bound, a false quorum is arithmetically out
/// of reach. This is `Q > 2f` doing its job.
///
/// It also exposes a **liveness cliff**: safety forces `Q >= 2f+1`, but the
/// honest agents only number `N-f`. When `N-f < Q` the honest majority cannot
/// decide *without* the forgers' cooperation, so a silent adversary can stall
/// every round. The condition is `N < 3f+1` — the classical BFT requirement,
/// which falls out here rather than being assumed.
#[test]
fn forgers_alone_cannot_reach_quorum() {
    for (n, f) in [(3usize, 1usize), (5, 1), (7, 2), (9, 3), (10, 3)] {
        let policy = QuorumPolicy::minimum(n, f).expect("sound config exists");
        let q = policy.quorum();

        // Safety: forgers alone never make a quorum.
        assert!(f < q, "N={n} f={f} Q={q}: forgers alone must not make a quorum");

        // Liveness: can the honest agents decide by themselves?
        let honest_can_decide = n - f >= q;
        let bft_threshold_met = n >= 3 * f + 1;
        assert_eq!(
            honest_can_decide, bft_threshold_met,
            "N={n} f={f} Q={q}: honest-liveness must coincide with N >= 3f+1"
        );
    }
}

/// The cliff, stated directly: below `N >= 3f+1` the deployment is safe but
/// can be stalled. `QuorumPolicy` reports this rather than hiding it, so an
/// operator sizing a cluster sees the constraint before deploying.
#[test]
fn honest_liveness_requires_the_bft_threshold() {
    // N=3, f=1: safe (Q=3) but the 2 honest agents cannot reach 3 alone.
    let p = QuorumPolicy::minimum(3, 1).expect("sound");
    assert_eq!(p.quorum(), 3);
    assert!(!p.honest_can_decide(), "3 < 3f+1 = 4: stallable");

    // N=4, f=1: the classical threshold. Honest agents alone suffice.
    let p = QuorumPolicy::minimum(4, 1).expect("sound");
    assert_eq!(p.quorum(), 3);
    assert!(p.honest_can_decide(), "4 >= 3f+1 = 4: live without forgers");

    // N=7, f=2: comfortably above.
    let p = QuorumPolicy::minimum(7, 2).expect("sound");
    assert_eq!(p.quorum(), 5);
    assert!(p.honest_can_decide());

    // N=6, f=2: safe but stallable (6 < 7).
    let p = QuorumPolicy::minimum(6, 2).expect("sound");
    assert!(!p.honest_can_decide());
}

/// The operator-facing conclusion, made executable: shrinking the quorum below
/// the bound costs correctness, and buys nothing in availability.
///
/// Both configurations decide here — availability is identical. The difference
/// is that the unsound one *also* lets forgers manufacture a quorum, which
/// `QuorumPolicy` refuses to construct.
#[test]
fn weakening_the_quorum_buys_no_availability() {
    let honest = vec![mk_vote("a1", 0.98), mk_vote("a2", 0.97), mk_vote("a3", 0.96)];

    // Sound: N=5, f=1 -> Q=3.
    let sound = QuorumPolicy::minimum(5, 1).expect("sound");
    assert_eq!(decide(&honest, sound.quorum()), Some(Verdict::Yes));

    // A smaller quorum decides the same case — no availability gained.
    assert_eq!(decide(&honest, 2), Some(Verdict::Yes));

    // But Q=2 with f=1 is unconstructible, because forgers could carry it.
    assert!(
        QuorumPolicy::new(5, 1, 2).is_err(),
        "the unsound quorum must be refused"
    );
}

/// End to end through the real coordinator: honest agents settle, the round
/// decides, and no escalation occurs.
#[test]
fn byzantine_tolerant_round_does_not_escalate_unnecessarily() {
    let policy = QuorumPolicy::minimum(5, 1).expect("sound");
    // a4 and a5 jitter forever — they stand in for agents whose judgments
    // never settle, the worst case for availability short of forgery.
    let oracle = ScriptedOracle::new(
        vec![
            ("a1", vec![0.98]),
            ("a2", vec![0.97]),
            ("a3", vec![0.96]),
            ("a4", vec![0.51, 0.52]),
            ("a5", vec![0.49, 0.48]),
        ],
        calib(),
    );
    let mut round = Round::new(
        RoundConfig {
            round_id: "r-live".into(),
            policy,
            max_asks: 2,
        },
        &["a1", "a2", "a3", "a4", "a5"],
        "q?",
        "state",
        &oracle,
    );

    let RoundOutcome::Decided(d) = round.run().expect("ok") else {
        panic!("three stable agreeing agents are a quorum; escalation is wrong here");
    };
    assert_eq!(*d.outcome, Verdict::Yes);
    assert_eq!(d.check_reproducible(), Ok(()));
}

/// The converse, so the previous test is not vacuous: when the honest agents
/// genuinely have no quorum, the round *does* escalate.
#[test]
fn escalation_still_happens_when_it_should() {
    let policy = QuorumPolicy::minimum(5, 1).expect("sound");
    let quorum = policy.quorum() as i32;
    let oracle = ScriptedOracle::new(
        vec![
            ("a1", vec![0.51, 0.52]),
            ("a2", vec![0.49, 0.48]),
            ("a3", vec![0.52, 0.51]),
            ("a4", vec![0.48, 0.49]),
            ("a5", vec![0.50, 0.51]),
        ],
        calib(),
    );
    let mut round = Round::new(
        RoundConfig {
            round_id: "r-esc".into(),
            policy,
            max_asks: 2,
        },
        &["a1", "a2", "a3", "a4", "a5"],
        "q?",
        "state",
        &oracle,
    );

    let RoundOutcome::Escalated(e) = round.run().expect("ok") else {
        panic!("no agent ever cleared the jitter band; this must escalate");
    };
    assert_eq!(e.check_justified(quorum), Ok(()));
    assert!(
        e.observed_votes.iter().all(|v| !v.stable),
        "escalation evidence shows why no quorum formed"
    );
}
