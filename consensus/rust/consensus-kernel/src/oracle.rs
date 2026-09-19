//! The judgment oracle abstraction.
//!
//! The coordinator must run identically against a deterministic fake and
//! against live Jev. That is not a testing convenience -- it is what lets us
//! replay a TLA+ counterexample through the real state machine. If the oracle
//! were hardwired to an HTTP client, every test would depend on a
//! non-deterministic remote service and the verification chain would end at
//! the type layer.

use consensus_types::{StabilityEvidence, Verdict};

/// What the oracle returns for one judgment.
#[derive(Debug, Clone, PartialEq)]
pub struct Judgment {
    pub verdict: Verdict,
    pub evidence: StabilityEvidence,
    /// Provider-side request id, retained so a vote can be reconciled against
    /// the oracle's own records.
    pub request_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum OracleError {
    Transport(String),
    Protocol(String),
    RateLimited,
}

impl std::fmt::Display for OracleError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OracleError::Transport(m) => write!(f, "transport error: {m}"),
            OracleError::Protocol(m) => write!(f, "protocol error: {m}"),
            OracleError::RateLimited => write!(f, "rate limited"),
        }
    }
}

impl std::error::Error for OracleError {}

/// The calibration in force for a class of question.
///
/// `noise_floor` is EMPIRICAL. It is the measured variation of the oracle
/// under a transform that should change nothing. Measured values for Jev:
///
/// | transform                   | floor |
/// |-----------------------------|-------|
/// | identity (same request)     | 0.042 |
/// | question reorder            | 0.059 |
/// | semantic restatement        | 0.073 |
///
/// Source: 1,406 captured calls. A hardcoded guess here voids the TLA+ proof,
/// whose `stable` predicate means "would not flip on re-ask".
#[derive(Debug, Clone, PartialEq)]
pub struct Calibration {
    pub threshold: f64,
    pub noise_floor: f64,
    pub source: String,
}

impl Calibration {
    pub fn new(threshold: f64, noise_floor: f64, source: impl Into<String>) -> Self {
        Self {
            threshold,
            noise_floor,
            source: source.into(),
        }
    }

    /// The identity floor: the oracle re-asked with a byte-identical request.
    pub fn identity(source: impl Into<String>) -> Self {
        Self::new(0.5, 0.042, source)
    }

    /// Classify a raw probability into a verdict plus stability evidence.
    ///
    /// This is the single place where a probability becomes a vote. Keeping it
    /// in one function means the rule the spec assumes -- stable iff
    /// `margin > noise_floor` -- has exactly one implementation.
    pub fn classify(&self, probability: f64) -> (Verdict, StabilityEvidence) {
        let margin = (probability - self.threshold).abs();
        let verdict = if probability > self.threshold {
            Verdict::Yes
        } else {
            Verdict::No
        };
        let evidence = StabilityEvidence {
            probability,
            threshold: self.threshold,
            noise_floor: self.noise_floor,
            margin,
            stable: margin > self.noise_floor,
            noise_floor_source: Some(self.source.clone()),
        };
        (verdict, evidence)
    }
}

/// A source of judgments. Implemented by the deterministic fake and by the
/// live Jev client.
pub trait Oracle: Send + Sync {
    /// Ask one yes/no judgment about `state`.
    ///
    /// `attempt` is the caller's consultation count for this agent, mirroring
    /// TLA+ `asked[a]`. Implementations may use it for logging; they must not
    /// use it to change the answer, or re-asking would not be a real re-ask.
    fn consult(
        &self,
        agent_id: &str,
        question: &str,
        state: &str,
        attempt: i32,
    ) -> Result<Judgment, OracleError>;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classification_matches_the_spec_predicate() {
        let c = Calibration::identity("test");
        // Comfortably above the boundary: stable yes.
        let (v, e) = c.classify(0.97);
        assert_eq!(v, Verdict::Yes);
        assert!(e.stable);

        // Inside the jitter band: a vote that could flip on re-ask.
        let (v, e) = c.classify(0.52);
        assert_eq!(v, Verdict::Yes);
        assert!(!e.stable, "margin 0.02 is inside the 0.042 floor");

        // Comfortably below: stable no.
        let (v, e) = c.classify(0.01);
        assert_eq!(v, Verdict::No);
        assert!(e.stable);
    }

    #[test]
    fn a_looser_floor_makes_the_same_probability_unstable() {
        let tight = Calibration::new(0.5, 0.042, "identity");
        let loose = Calibration::new(0.5, 0.073, "cohort");
        assert!(tight.classify(0.56).1.stable);
        assert!(!loose.classify(0.56).1.stable);
    }
}
