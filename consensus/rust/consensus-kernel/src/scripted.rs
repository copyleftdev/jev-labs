//! A scripted oracle for replaying exact traces, including TLC counterexamples.
//!
//! This is what makes the verification chain executable. The TLA+ model says
//! "the oracle may return an unstable answer every time"; this lets us build
//! precisely that oracle and watch the real coordinator handle it.

use std::sync::Mutex;

use consensus_types::Verdict;

use crate::oracle::{Calibration, Judgment, Oracle, OracleError};

/// Returns a scripted sequence of raw probabilities per agent.
///
/// When an agent's script is exhausted the last value repeats, which models an
/// oracle that has genuinely settled on an answer (stable or not).
pub struct ScriptedOracle {
    scripts: Vec<(String, Vec<f64>)>,
    calibration: Calibration,
    calls: Mutex<Vec<(String, i32, f64)>>,
    /// Optional failure injected on the Nth call, for transport-error tests.
    fail_on_call: Option<usize>,
}

impl ScriptedOracle {
    pub fn new(scripts: Vec<(&str, Vec<f64>)>, calibration: Calibration) -> Self {
        Self {
            scripts: scripts
                .into_iter()
                .map(|(a, p)| (a.to_string(), p))
                .collect(),
            calibration,
            calls: Mutex::new(Vec::new()),
            fail_on_call: None,
        }
    }

    pub fn failing_on_call(mut self, n: usize) -> Self {
        self.fail_on_call = Some(n);
        self
    }

    /// Every (agent, attempt, probability) actually served.
    pub fn calls(&self) -> Vec<(String, i32, f64)> {
        self.calls.lock().expect("oracle mutex").clone()
    }

    pub fn call_count(&self) -> usize {
        self.calls.lock().expect("oracle mutex").len()
    }
}

impl Oracle for ScriptedOracle {
    fn consult(
        &self,
        agent_id: &str,
        _question: &str,
        _state: &str,
        attempt: i32,
    ) -> Result<Judgment, OracleError> {
        {
            let calls = self.calls.lock().expect("oracle mutex");
            if let Some(n) = self.fail_on_call {
                if calls.len() + 1 == n {
                    return Err(OracleError::Transport("injected failure".into()));
                }
            }
        }

        let script = self
            .scripts
            .iter()
            .find(|(a, _)| a == agent_id)
            .map(|(_, p)| p)
            .ok_or_else(|| OracleError::Protocol(format!("no script for {agent_id}")))?;

        if script.is_empty() {
            return Err(OracleError::Protocol(format!("empty script for {agent_id}")));
        }
        let idx = ((attempt - 1).max(0) as usize).min(script.len() - 1);
        let probability = script[idx];

        self.calls
            .lock()
            .expect("oracle mutex")
            .push((agent_id.to_string(), attempt, probability));

        let (verdict, evidence) = self.calibration.classify(probability);
        Ok(Judgment {
            verdict,
            evidence,
            request_id: Some(format!("scripted_{agent_id}_{attempt}")),
        })
    }
}

/// An oracle that always returns the same probability, for every agent.
pub struct ConstantOracle {
    pub probability: f64,
    pub calibration: Calibration,
}

impl Oracle for ConstantOracle {
    fn consult(
        &self,
        agent_id: &str,
        _question: &str,
        _state: &str,
        attempt: i32,
    ) -> Result<Judgment, OracleError> {
        let (verdict, evidence) = self.calibration.classify(self.probability);
        let _ = (agent_id, attempt);
        Ok(Judgment {
            verdict,
            evidence,
            request_id: None,
        })
    }
}

/// Convenience: the verdict an agent would cast for a probability.
pub fn verdict_for(calibration: &Calibration, probability: f64) -> Verdict {
    calibration.classify(probability).0
}
