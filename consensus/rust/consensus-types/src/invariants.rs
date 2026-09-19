//! Invariant checks over the generated wire types.
//!
//! The TLA+ spec proves the *protocol* correct. These functions let a receiver
//! verify that an individual message on the wire actually honours those
//! properties, without trusting the sender.
//!
//! This is the point of carrying `supportingVotes` and `observedVotes` in the
//! outcome messages: a decision is auditable from the message alone.

use crate::{DecisionPayload, EscalationPayload, StabilityEvidence, Vote};

/// Why a message failed its invariant check.
#[derive(Debug, Clone, PartialEq)]
pub enum InvariantError {
    /// `DecisionIsReproducible`: a decision rested on fewer stable votes than
    /// the quorum it claims.
    InsufficientStableVotes { required: i32, found: usize },
    /// A vote counted toward a decision was not stable.
    UnstableVoteInQuorum { agent_id: String },
    /// A supporting vote disagreed with the decided outcome.
    VoteVerdictMismatch { agent_id: String },
    /// `EscalationIsJustified`: a stable quorum existed, so escalating was wrong.
    EscalationWithStableQuorum { found: usize },
    /// The stability flag contradicts the evidence that supposedly justifies it.
    StabilityContradictsEvidence { agent_id: String },
    /// Votes came from different rounds.
    RoundIdMismatch { expected: String, found: String },
}

impl StabilityEvidence {
    /// Recompute stability from the evidence rather than trusting the flag.
    ///
    /// A vote is stable only when its margin from the decision boundary exceeds
    /// the oracle's measured jitter for that transform class. The noise floor
    /// is empirical (0.042-0.073 in our measurements); a hardcoded guess here
    /// voids the TLA+ proof, whose `stable` predicate means "would not flip on
    /// re-ask".
    pub fn recomputed_stable(&self) -> bool {
        self.margin > self.noise_floor
    }

    /// Does the declared flag match what the numbers say?
    pub fn is_self_consistent(&self) -> bool {
        self.stable == self.recomputed_stable()
    }
}

impl DecisionPayload {
    /// Verify `DecisionIsReproducible` from the message alone.
    ///
    /// ```text
    /// decided \in Verdicts => Cardinality(StableVotesFor(decided)) >= Quorum
    /// ```
    pub fn check_reproducible(&self) -> Result<(), InvariantError> {
        for v in &self.supporting_votes {
            if v.round_id != self.round_id {
                return Err(InvariantError::RoundIdMismatch {
                    expected: self.round_id.clone(),
                    found: v.round_id.clone(),
                });
            }
            if !v.stable {
                return Err(InvariantError::UnstableVoteInQuorum {
                    agent_id: v.agent_id.clone(),
                });
            }
            if *v.verdict != *self.outcome {
                return Err(InvariantError::VoteVerdictMismatch {
                    agent_id: v.agent_id.clone(),
                });
            }
            if let Some(ev) = &v.evidence {
                if !ev.is_self_consistent() {
                    return Err(InvariantError::StabilityContradictsEvidence {
                        agent_id: v.agent_id.clone(),
                    });
                }
            }
        }
        let found = self.supporting_votes.len();
        if (found as i32) < self.quorum {
            return Err(InvariantError::InsufficientStableVotes {
                required: self.quorum,
                found,
            });
        }
        Ok(())
    }
}

impl EscalationPayload {
    /// Verify `EscalationIsJustified`: no stable quorum was available.
    ///
    /// ```text
    /// decided = UNCERTAIN => NoStableQuorum
    /// ```
    pub fn check_justified(&self, quorum: i32) -> Result<(), InvariantError> {
        use std::collections::HashMap;
        let mut stable_per_verdict: HashMap<String, usize> = HashMap::new();
        for v in &self.observed_votes {
            if v.stable {
                *stable_per_verdict
                    .entry(format!("{:?}", v.verdict))
                    .or_insert(0) += 1;
            }
        }
        if let Some((_, &count)) = stable_per_verdict
            .iter()
            .max_by_key(|(_, &c)| c)
            .filter(|(_, &c)| (c as i32) >= quorum)
        {
            return Err(InvariantError::EscalationWithStableQuorum { found: count });
        }
        Ok(())
    }
}

/// Count stable votes for a given verdict among live agents.
pub fn stable_votes_for(votes: &[Vote], verdict: &crate::Verdict) -> usize {
    votes
        .iter()
        .filter(|v| v.stable && *v.verdict == *verdict)
        .count()
}
