//! Quorum sizing, derived from model checking rather than folklore.
//!
//! The bound is not assumed. `spec/sweep_quorum.sh` runs TLC across
//! (agents, byzantine, quorum) and confirms cell by cell that safety holds
//! exactly when:
//!
//! ```text
//!   2Q > N        quorums intersect, so two coordinators cannot diverge
//!   Q  > 2f       any quorum holds an honest majority, so forgers cannot
//!                 be the reason a decision happened
//! ```
//!
//! Both conditions come from a verified invariant:
//!
//! - `Agreement` fails when `2Q <= N` — TLC produces a split-brain trace where
//!   two coordinators see disjoint quorums and commit opposite verdicts.
//! - `SoundDecision` fails when `Q <= 2f` — a quorum can then be half forgers,
//!   and the honest votes are not a majority of what carried the decision.
//!
//! The `f` parameter is a policy choice about the deployment, not a property
//! of the oracle. Agents that merely disagree are not Byzantine; an agent is
//! Byzantine here only if it forges self-consistent stability evidence, which
//! recomputation cannot detect.

/// How a quorum configuration failed validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum QuorumError {
    /// `2Q <= N`: two coordinators could observe disjoint quorums.
    QuorumsMayNotIntersect { agents: usize, quorum: usize },
    /// `Q <= 2f`: a quorum could be carried by forgers.
    NoHonestMajority { quorum: usize, byzantine: usize },
    /// More agents required than exist.
    QuorumExceedsAgents { agents: usize, quorum: usize },
    /// A quorum of zero decides on nothing.
    QuorumTooSmall,
}

impl std::fmt::Display for QuorumError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            QuorumError::QuorumsMayNotIntersect { agents, quorum } => write!(
                f,
                "quorum {quorum} of {agents} agents allows disjoint quorums \
                 (need 2Q > N); two coordinators could commit different verdicts"
            ),
            QuorumError::NoHonestMajority { quorum, byzantine } => write!(
                f,
                "quorum {quorum} with {byzantine} possible forgers has no honest \
                 majority (need Q > 2f)"
            ),
            QuorumError::QuorumExceedsAgents { agents, quorum } => {
                write!(f, "quorum {quorum} exceeds {agents} agents")
            }
            QuorumError::QuorumTooSmall => write!(f, "quorum must be at least 1"),
        }
    }
}

impl std::error::Error for QuorumError {}

/// A quorum configuration that has been checked against the derived bound.
///
/// Construct with [`QuorumPolicy::new`]; the constructor is the only way to
/// build one, so an unsound configuration cannot reach the coordinator.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QuorumPolicy {
    agents: usize,
    byzantine: usize,
    quorum: usize,
}

impl QuorumPolicy {
    /// Validate a configuration against `2Q > N` and `Q > 2f`.
    pub fn new(agents: usize, byzantine: usize, quorum: usize) -> Result<Self, QuorumError> {
        if quorum == 0 {
            return Err(QuorumError::QuorumTooSmall);
        }
        if quorum > agents {
            return Err(QuorumError::QuorumExceedsAgents { agents, quorum });
        }
        if 2 * quorum <= agents {
            return Err(QuorumError::QuorumsMayNotIntersect { agents, quorum });
        }
        if quorum <= 2 * byzantine {
            return Err(QuorumError::NoHonestMajority { quorum, byzantine });
        }
        Ok(Self {
            agents,
            byzantine,
            quorum,
        })
    }

    /// The smallest sound quorum for a deployment, or `None` if `N` is too
    /// small to tolerate `f` forgers at all.
    ///
    /// `Q > 2f` and `2Q > N` give `Q = max(floor(N/2)+1, 2f+1)`, which only
    /// exists when that value is `<= N` — i.e. `N > 2f`... and in fact
    /// `N >= 2f+1` is necessary, matching the classical requirement that a
    /// majority of participants be honest.
    pub fn minimum(agents: usize, byzantine: usize) -> Option<Self> {
        let by_intersection = agents / 2 + 1;
        let by_honesty = 2 * byzantine + 1;
        let q = by_intersection.max(by_honesty);
        Self::new(agents, byzantine, q).ok()
    }

    pub fn quorum(&self) -> usize {
        self.quorum
    }

    pub fn agents(&self) -> usize {
        self.agents
    }

    pub fn byzantine(&self) -> usize {
        self.byzantine
    }

    /// Can the honest agents decide WITHOUT any cooperation from forgers?
    ///
    /// Safety forces `Q >= 2f+1`, but only `N-f` agents are honest. When
    /// `N-f < Q` the deployment is still safe — forgers cannot cause a wrong
    /// decision — but a silent adversary can stall every round, because no
    /// quorum forms without it. That condition reduces to `N >= 3f+1`, the
    /// classical Byzantine threshold, which falls out of our derived bound
    /// rather than being assumed.
    ///
    /// This is reported rather than enforced: a stallable configuration is a
    /// legitimate choice when `f` is a worst case you do not expect to hit,
    /// and refusing to build it would be over-reach. But an operator sizing a
    /// cluster should see it.
    pub fn honest_can_decide(&self) -> bool {
        self.agents - self.byzantine >= self.quorum
    }

    /// Can this deployment still decide after `crashed` agents are lost?
    ///
    /// Crashed agents shrink the live set; the quorum does not shrink with it,
    /// so a deployment can become permanently unable to decide. Callers should
    /// surface this as escalation rather than as a hang.
    pub fn tolerates_crashes(&self, crashed: usize) -> bool {
        self.agents.saturating_sub(crashed) >= self.quorum
    }

    /// Maximum crashes that still leave a decidable round.
    pub fn crash_tolerance(&self) -> usize {
        self.agents.saturating_sub(self.quorum)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every cell here is a row from `spec/sweep_quorum.sh`, which TLC
    /// verified. If the bound changes, these must be regenerated from a new
    /// sweep — not adjusted by hand.
    #[test]
    fn matches_the_model_checked_sweep() {
        // (agents, byzantine, quorum, expected_safe)
        let cells = [
            (3, 0, 1, false), // 2*1 <= 3, split brain
            (3, 0, 2, true),
            (3, 0, 3, true),
            (3, 1, 1, false), // 1 <= 2*1, forgers carry it
            (3, 1, 2, false),
            (3, 1, 3, true),
            (4, 0, 1, false),
            (4, 0, 2, false), // 2*2 == 4, disjoint quorums exist
            (4, 0, 3, true),
            (4, 0, 4, true),
            (4, 1, 1, false),
            (4, 1, 2, false),
            (4, 1, 3, true),
            (4, 1, 4, true),
            (5, 0, 2, false),
            (5, 0, 3, true),
            (5, 1, 2, false),
            (5, 1, 3, true),
        ];
        for (n, f, q, expected) in cells {
            let got = QuorumPolicy::new(n, f, q).is_ok();
            assert_eq!(
                got, expected,
                "N={n} f={f} Q={q}: expected safe={expected}, got {got}"
            );
        }
    }

    #[test]
    fn minimum_quorum_is_the_derived_bound() {
        assert_eq!(QuorumPolicy::minimum(3, 0).unwrap().quorum(), 2);
        assert_eq!(QuorumPolicy::minimum(4, 0).unwrap().quorum(), 3);
        assert_eq!(QuorumPolicy::minimum(5, 0).unwrap().quorum(), 3);
        assert_eq!(QuorumPolicy::minimum(3, 1).unwrap().quorum(), 3);
        assert_eq!(QuorumPolicy::minimum(5, 1).unwrap().quorum(), 3);
        assert_eq!(QuorumPolicy::minimum(7, 2).unwrap().quorum(), 5);
    }

    #[test]
    fn too_few_agents_for_the_forgers_is_impossible() {
        // 2 agents cannot tolerate a forger: Q would have to exceed 2.
        assert!(QuorumPolicy::minimum(2, 1).is_none());
        assert!(QuorumPolicy::minimum(4, 2).is_none());
    }

    #[test]
    fn crash_tolerance_is_reported() {
        let p = QuorumPolicy::new(5, 0, 3).unwrap();
        assert_eq!(p.crash_tolerance(), 2);
        assert!(p.tolerates_crashes(2));
        assert!(!p.tolerates_crashes(3));
    }

    #[test]
    fn error_messages_name_the_violated_bound() {
        let e = QuorumPolicy::new(4, 0, 2).unwrap_err();
        assert!(format!("{e}").contains("2Q > N"), "{e}");
        let e = QuorumPolicy::new(3, 1, 2).unwrap_err();
        assert!(format!("{e}").contains("Q > 2f"), "{e}");
    }
}
