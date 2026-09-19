//! End-to-end demo: the consensus kernel against the live Jev oracle.
//!
//! Three agents independently judge the same question through TypeSafe's
//! System One API. The coordinator applies the model-checked rules: only
//! stable votes count, the ask budget is bounded, and an unsettled question
//! escalates rather than hanging.
//!
//! Run:
//!     source ../../../env.sh
//!     cargo run --example live_consensus
//!
//! Requires TYPESAFE_API_KEY. Costs a fraction of a cent.

use consensus_kernel::{
    jev::JevOracle, Calibration, Round, RoundConfig, RoundOutcome,
};

/// Questions chosen to span the difficulty range: one clear-cut, one
/// genuinely borderline. The borderline case is the interesting one — it is
/// where the stability gate earns its keep.
const CASES: &[(&str, &str)] = &[
    (
        "clear",
        "I was charged twice for order A-104 and I want the duplicate refunded.",
    ),
    (
        "borderline",
        "Hi Sarah, following up on our chat at the conference. We just launched \
         our new analytics tier and I thought of your team. Happy to set up a \
         walkthrough if useful, or ignore this if the timing is bad.",
    ),
];

const QUESTION: &str = "Is this message about a billing or payment matter?";

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // The identity floor: measured variation when the same request is re-sent.
    // 0.042 over 1,406 captured calls. This number is the bridge between the
    // telemetry and the formal proof.
    let calibration = Calibration::identity("calib-2026-09-18-identity");

    let oracle = JevOracle::from_env(calibration.clone())?.with_criteria(
        "The message concerns payments, invoices, refunds or charges.",
        "The message does not concern payments, invoices, refunds or charges.",
    );

    println!("noise floor : {:.3} ({})", calibration.noise_floor, calibration.source);
    println!("threshold   : {:.3}", calibration.threshold);
    println!();

    for (label, state) in CASES {
        println!("=== {label} ===");
        println!("{}", truncate(state, 96));

        let cfg = RoundConfig {
            round_id: format!("live-{label}"),
            policy: consensus_kernel::QuorumPolicy::new(3, 0, 2).expect("sound"),
            max_asks: 2,
        };
        let mut round = Round::new(cfg, &["a1", "a2", "a3"], QUESTION, *state, &oracle);

        let outcome = round.run()?;
        match &outcome {
            RoundOutcome::Decided(d) => {
                println!("  OUTCOME  decided {:?}", d.outcome);
                println!("  quorum   {} of stable votes", d.quorum);
                for v in &d.supporting_votes {
                    if let Some(e) = &v.evidence {
                        println!(
                            "    {} p={:.3} margin={:.3} stable={} attempt={}",
                            v.agent_id, e.probability, e.margin, v.stable, v.attempt
                        );
                    }
                }
                // The decision must satisfy the invariant it claims.
                match d.check_reproducible() {
                    Ok(()) => println!("  INVARIANT DecisionIsReproducible: OK"),
                    Err(e) => println!("  INVARIANT VIOLATED: {e:?}"),
                }
            }
            RoundOutcome::Escalated(e) => {
                println!("  OUTCOME  escalated ({:?})", e.reason);
                for v in &e.observed_votes {
                    if let Some(ev) = &v.evidence {
                        println!(
                            "    {} p={:.3} margin={:.3} stable={} attempt={}",
                            v.agent_id, ev.probability, ev.margin, v.stable, v.attempt
                        );
                    }
                }
                match e.check_justified(2) {
                    Ok(()) => println!("  INVARIANT EscalationIsJustified: OK"),
                    Err(err) => println!("  INVARIANT VIOLATED: {err:?}"),
                }
            }
        }

        println!("  oracle request ids: {}", round.request_ids().len());
        for id in round.request_ids() {
            println!("    {id}");
        }
        println!();
    }

    Ok(())
}

fn truncate(s: &str, n: usize) -> String {
    if s.len() <= n {
        s.to_string()
    } else {
        format!("{}...", &s[..n])
    }
}
