//! Full-scale pharmacy simulation against the live Jev oracle.
//!
//! Runs the model-checked consensus kernel over synthetic pharmacy scenarios
//! under deterministic chaos, and asserts the one invariant that matters in a
//! regulated setting:
//!
//!   **A golden case is never decided wrongly. Escalation is always allowed;
//!   being confidently wrong never is.**
//!
//! Every round is written to `simulation_telemetry.jsonl` with the seed, the
//! fault schedule, every vote with its stability evidence, and the oracle's own
//! request ids — so any finding can be replayed and any claim traced to a call.
//!
//! Usage:
//!     source ../../../env.sh
//!     cargo run --release --example pharmacy_sim -- --seeds 20 --chaos realistic
//!
//! Chaos levels: none | realistic | severe

use std::collections::BTreeMap;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::time::Instant;

use consensus_kernel::{
    chaos::{ChaosConfig, ChaosOracle},
    jev::JevOracle,
    pharmacy::{Expected, Scenario, Tier, SCENARIOS},
    Calibration, Oracle, QuorumPolicy, Round, RoundConfig, RoundOutcome,
};
use serde::Serialize;

const AGENTS: &[&str] = &["rx1", "rx2", "rx3", "rx4", "rx5"];

#[derive(Serialize)]
struct RoundRecord {
    seed: u64,
    chaos: String,
    scenario: String,
    tier: String,
    expected: String,
    outcome: String,
    verdict: Option<String>,
    correct: Option<bool>,
    safety_violation: bool,
    escalated: bool,
    votes: Vec<VoteRecord>,
    oracle_request_ids: Vec<String>,
    /// Agents that dropped out because the oracle was unreachable. An
    /// escalation caused by degraded availability is operationally different
    /// from one caused by a hard question.
    oracle_failures: Vec<(String, String)>,
    elapsed_ms: u128,
    error: Option<String>,
}

#[derive(Serialize)]
struct VoteRecord {
    agent: String,
    verdict: String,
    probability: f64,
    margin: f64,
    noise_floor: f64,
    stable: bool,
    attempt: i32,
}

/// One agent asks one paraphrase. Wrapping the shared oracle per agent is what
/// makes the agents genuinely different: identical prompts across agents
/// produced a measured spread of 0.010, inside the 0.042 noise floor, so
/// identical-prompt agents are one judgment sampled five times.
struct ParaphraseOracle<'a> {
    inner: &'a dyn Oracle,
    by_agent: BTreeMap<String, String>,
}

impl<'a> Oracle for ParaphraseOracle<'a> {
    fn consult(
        &self,
        agent_id: &str,
        _question: &str,
        state: &str,
        attempt: i32,
    ) -> Result<consensus_kernel::Judgment, consensus_kernel::OracleError> {
        let q = self
            .by_agent
            .get(agent_id)
            .map(String::as_str)
            .unwrap_or(_question);
        self.inner.consult(agent_id, q, state, attempt)
    }
}

fn parse_args() -> (usize, String, u64, String) {
    let mut seeds = 12usize;
    let mut chaos = "realistic".to_string();
    let mut base_seed = 1000u64;
    let mut out = "simulation_telemetry.jsonl".to_string();
    let args: Vec<String> = std::env::args().collect();
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--seeds" => {
                seeds = args.get(i + 1).and_then(|s| s.parse().ok()).unwrap_or(seeds);
                i += 2;
            }
            "--chaos" => {
                chaos = args.get(i + 1).cloned().unwrap_or(chaos);
                i += 2;
            }
            "--base-seed" => {
                base_seed = args
                    .get(i + 1)
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(base_seed);
                i += 2;
            }
            "--out" => {
                out = args.get(i + 1).cloned().unwrap_or(out);
                i += 2;
            }
            _ => i += 1,
        }
    }
    (seeds, chaos, base_seed, out)
}

fn chaos_for(name: &str) -> ChaosConfig {
    match name {
        "none" => ChaosConfig::none(),
        "severe" => ChaosConfig::severe(),
        _ => ChaosConfig::realistic(),
    }
}

fn run_one(
    scenario: &Scenario,
    seed: u64,
    chaos_name: &str,
    base: &dyn Oracle,
) -> RoundRecord {
    let chaos = ChaosOracle::new(base, chaos_for(chaos_name), seed);

    let by_agent: BTreeMap<String, String> = AGENTS
        .iter()
        .enumerate()
        .map(|(i, a)| {
            (
                (*a).to_string(),
                scenario.paraphrases[i % scenario.paraphrases.len()].to_string(),
            )
        })
        .collect();
    let oracle = ParaphraseOracle {
        inner: &chaos,
        by_agent,
    };

    // 5 agents, tolerate 1 forger: Q must exceed 2f=2 and 2Q>5, so Q=3.
    let policy = QuorumPolicy::minimum(AGENTS.len(), 1).expect("sound configuration");

    let mut round = Round::new(
        RoundConfig {
            round_id: format!("{}::{seed}", scenario.id),
            policy,
            max_asks: 2,
        },
        AGENTS,
        scenario.question,
        scenario.record,
        &oracle,
    );

    // Crash agents before the round runs, per the deterministic schedule.
    for a in AGENTS {
        if chaos.should_crash_agent() {
            round.mark_crashed(a);
        }
    }

    let start = Instant::now();
    let result = round.run();
    let elapsed_ms = start.elapsed().as_millis();

    let (outcome_kind, verdict, votes, error) = match &result {
        Ok(RoundOutcome::Decided(d)) => (
            "decided",
            Some(format!("{:?}", d.outcome)),
            d.supporting_votes.iter().map(vote_record).collect(),
            None,
        ),
        Ok(RoundOutcome::Escalated(e)) => (
            "escalated",
            None,
            e.observed_votes.iter().map(vote_record).collect(),
            None,
        ),
        Err(e) => ("error", None, Vec::new(), Some(e.to_string())),
    };

    let expected = format!("{:?}", scenario.expected);
    let correct = match (&scenario.expected, &verdict) {
        (Expected::Yes, Some(v)) => Some(v == "Yes"),
        (Expected::No, Some(v)) => Some(v == "No"),
        (Expected::Escalate, Some(_)) => Some(false),
        (_, None) => None,
    };

    // THE SAFETY INVARIANT. A golden case decided wrongly is a hard failure.
    // Escalation is never a violation: declining to answer is always safe.
    let safety_violation = matches!(scenario.tier, Tier::Golden)
        && matches!(correct, Some(false))
        && verdict.is_some();

    RoundRecord {
        seed,
        chaos: chaos_name.to_string(),
        scenario: scenario.id.to_string(),
        tier: format!("{:?}", scenario.tier),
        expected,
        outcome: outcome_kind.to_string(),
        verdict,
        correct,
        safety_violation,
        escalated: outcome_kind == "escalated",
        votes,
        oracle_request_ids: round.request_ids().to_vec(),
        oracle_failures: round.oracle_failures().to_vec(),
        elapsed_ms,
        error,
    }
}

fn vote_record(v: &consensus_types::Vote) -> VoteRecord {
    let ev = v.evidence.as_ref();
    VoteRecord {
        agent: v.agent_id.clone(),
        verdict: format!("{:?}", v.verdict),
        probability: ev.map(|e| e.probability).unwrap_or(f64::NAN),
        margin: ev.map(|e| e.margin).unwrap_or(f64::NAN),
        noise_floor: ev.map(|e| e.noise_floor).unwrap_or(f64::NAN),
        stable: v.stable,
        attempt: v.attempt,
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let (n_seeds, chaos_name, base_seed, out_path) = parse_args();

    // The identity floor: measured oracle variation when the same request is
    // re-sent. 0.042 over 1,406 captured calls.
    let calibration = Calibration::identity("calib-2026-09-18-identity");
    let jev = JevOracle::from_env(calibration)?.with_criteria(
        "The statement is true of this prescription record.",
        "The statement is not true of this prescription record.",
    );

    let mut sink = BufWriter::new(File::create(&out_path)?);

    println!("pharmacy consensus simulation");
    println!("  agents        : {} (quorum 3, tolerating f=1)", AGENTS.len());
    println!("  scenarios     : {}", SCENARIOS.len());
    println!("  seeds         : {n_seeds}");
    println!("  chaos         : {chaos_name}");
    println!("  noise floor   : 0.042 (identity, measured)");
    println!();

    let mut total = 0usize;
    let mut golden_total = 0usize;
    let mut golden_correct = 0usize;
    let mut golden_escalated = 0usize;
    let mut violations: Vec<String> = Vec::new();
    let mut ambiguous_escalated = 0usize;
    let mut ambiguous_total = 0usize;
    let mut errors = 0usize;

    for s in 0..n_seeds {
        let seed = base_seed + s as u64;
        for scenario in SCENARIOS {
            let rec = run_one(scenario, seed, &chaos_name, &jev);
            total += 1;

            match scenario.tier {
                Tier::Golden => {
                    golden_total += 1;
                    if rec.escalated {
                        golden_escalated += 1;
                    }
                    if matches!(rec.correct, Some(true)) {
                        golden_correct += 1;
                    }
                    if rec.safety_violation {
                        violations.push(format!(
                            "{} seed={} expected={} got={}",
                            rec.scenario,
                            rec.seed,
                            rec.expected,
                            rec.verdict.clone().unwrap_or_default()
                        ));
                    }
                }
                Tier::Ambiguous => {
                    ambiguous_total += 1;
                    if rec.escalated {
                        ambiguous_escalated += 1;
                    }
                }
                Tier::Nuanced => {}
            }
            if rec.error.is_some() {
                errors += 1;
            }

            writeln!(sink, "{}", serde_json::to_string(&rec)?)?;
        }
        print!(".");
        std::io::stdout().flush()?;
    }
    sink.flush()?;
    println!("\n");

    println!("================================================================");
    println!("rounds run              : {total}");
    println!("oracle/transport errors : {errors}");
    println!();
    println!("GOLDEN (must never be wrong)");
    println!("  total                 : {golden_total}");
    println!("  decided correctly     : {golden_correct}");
    println!("  escalated (safe)      : {golden_escalated}");
    println!("  SAFETY VIOLATIONS     : {}", violations.len());
    println!();
    println!("AMBIGUOUS (should escalate)");
    println!("  total                 : {ambiguous_total}");
    println!("  escalated correctly   : {ambiguous_escalated}");
    println!("================================================================");

    if violations.is_empty() {
        println!("\nSAFETY INVARIANT HELD across every seed and fault schedule.");
    } else {
        println!("\nSAFETY INVARIANT VIOLATED:");
        for v in violations.iter().take(20) {
            println!("  {v}");
        }
        println!("\nreplay a violation with --base-seed <seed> --seeds 1");
    }
    println!("\ntelemetry: {out_path}");
    Ok(())
}
