//! The consensus coordinator: a direct implementation of the model-checked
//! state machine in `spec/SemanticConsensusV2.tla`.
//!
//! Correspondence with the spec, which is the whole point of this module:
//!
//! | TLA+                     | here                                  |
//! |--------------------------|---------------------------------------|
//! | `Consult(a)`             | `Round::consult` with `attempt = 1`   |
//! | `Reconsult(a)`           | `Round::consult` with `attempt > 1`   |
//! | `Crash(a)`               | `Round::mark_crashed`                 |
//! | `Decide`                 | `Round::try_decide` -> `Decision`     |
//! | `Escalate`               | `Round::try_decide` -> `Escalation`   |
//! | `asked[a] < MaxAsks`     | `max_asks` budget                     |
//! | `StableVotesFor(v)`      | stable votes from live agents         |
//!
//! Two properties are load-bearing and are enforced here rather than assumed:
//!
//! - `DecisionIsReproducible` — only stable votes from live agents count
//!   toward quorum. TLC finds a 4-state counterexample without this.
//! - `EventuallyDecided` — the ask budget is bounded and escalation is always
//!   available, so the round cannot hang. TLC reaches `State 9: Stuttering`
//!   without it.

use std::collections::HashMap;

use consensus_types::{
    DecisionPayload, EscalationPayload, EscalationReason, Outcome, Verdict, Vote,
};

use crate::oracle::{Oracle, OracleError};
use crate::quorum::QuorumPolicy;

/// Terminal result of a round. Mirrors TLA+ `Outcomes`.
#[derive(Debug, Clone, PartialEq)]
pub enum RoundOutcome {
    Decided(DecisionPayload),
    Escalated(EscalationPayload),
}

impl RoundOutcome {
    pub fn is_decided(&self) -> bool {
        matches!(self, RoundOutcome::Decided(_))
    }
}

#[derive(Debug, Clone)]
pub struct RoundConfig {
    pub round_id: String,
    /// Quorum sizing validated against the model-checked bound.
    ///
    /// Constructed via [`crate::quorum::QuorumPolicy::new`], which enforces
    /// `2Q > N` (quorums intersect, so concurrent coordinators cannot diverge)
    /// and `Q > 2f` (any quorum holds an honest majority). Both bounds were
    /// derived by sweeping TLC, not assumed — see `spec/sweep_quorum.sh`.
    pub policy: QuorumPolicy,
    /// TLA+ `MaxAsks`. Bounding this is what makes `Exhausted` eventually
    /// true, which is what makes the protocol live.
    pub max_asks: i32,
}

impl RoundConfig {
    pub fn quorum(&self) -> i32 {
        self.policy.quorum() as i32
    }
}

/// Per-agent state. Mirrors the TLA+ variables indexed by agent.
#[derive(Debug, Clone)]
struct AgentState {
    vote: Option<Vote>,
    asked: i32,
    crashed: bool,
}

impl AgentState {
    fn new() -> Self {
        Self {
            vote: None,
            asked: 0,
            crashed: false,
        }
    }
}

/// One consensus round over one question.
pub struct Round<'a> {
    cfg: RoundConfig,
    question: String,
    state_text: String,
    agents: HashMap<String, AgentState>,
    oracle: &'a dyn Oracle,
    outcome: Option<RoundOutcome>,
    /// Every oracle request id seen, so a round is auditable end to end.
    request_ids: Vec<String>,
    /// Oracle failures, as (agent, reason). Degraded availability is recorded
    /// rather than thrown, so an escalation caused by an unreachable oracle is
    /// distinguishable from one caused by a genuinely hard question.
    oracle_failures: Vec<(String, String)>,
}

impl<'a> Round<'a> {
    pub fn new(
        cfg: RoundConfig,
        agent_ids: &[&str],
        question: impl Into<String>,
        state_text: impl Into<String>,
        oracle: &'a dyn Oracle,
    ) -> Self {
        let agents = agent_ids
            .iter()
            .map(|a| ((*a).to_string(), AgentState::new()))
            .collect();
        Self {
            cfg,
            question: question.into(),
            state_text: state_text.into(),
            agents,
            oracle,
            outcome: None,
            request_ids: Vec::new(),
            oracle_failures: Vec::new(),
        }
    }

    /// TLA+ `Crash(a)`. A crashed agent leaves `Live` and stops counting.
    pub fn mark_crashed(&mut self, agent_id: &str) {
        if let Some(a) = self.agents.get_mut(agent_id) {
            a.crashed = true;
        }
    }

    fn live(&self) -> impl Iterator<Item = (&String, &AgentState)> {
        self.agents.iter().filter(|(_, a)| !a.crashed)
    }

    /// TLA+ `Consult(a)` / `Reconsult(a)`.
    ///
    /// Guarded by `asked[a] < MaxAsks`. Reconsulting a vote that is already
    /// stable is refused: in the spec, `Reconsult` requires `~stable[a]`, and
    /// allowing it here would let an implementation churn a settled vote.
    pub fn consult(&mut self, agent_id: &str) -> Result<bool, OracleError> {
        if self.outcome.is_some() {
            return Ok(false); // TLA+ actions are guarded by decided = ABSTAIN
        }
        let Some(agent) = self.agents.get(agent_id) else {
            return Ok(false);
        };
        if agent.crashed || agent.asked >= self.cfg.max_asks {
            return Ok(false);
        }
        if let Some(v) = &agent.vote {
            if v.stable {
                return Ok(false); // already settled; nothing to re-ask
            }
        }

        let attempt = agent.asked + 1;
        let judgment =
            self.oracle
                .consult(agent_id, &self.question, &self.state_text, attempt)?;

        if let Some(rid) = &judgment.request_id {
            self.request_ids.push(rid.clone());
        }

        let vote = Vote {
            round_id: self.cfg.round_id.clone(),
            agent_id: agent_id.to_string(),
            verdict: Box::new(judgment.verdict),
            stable: judgment.evidence.stable,
            attempt,
            evidence: Some(Box::new(judgment.evidence)),
        };

        let agent = self.agents.get_mut(agent_id).expect("checked above");
        agent.asked = attempt;
        agent.vote = Some(vote);
        Ok(true)
    }

    /// TLA+ `StableVotesFor(v)` restricted to live agents.
    fn stable_votes_for(&self, verdict: &Verdict) -> Vec<Vote> {
        self.live()
            .filter_map(|(_, a)| a.vote.as_ref())
            .filter(|v| v.stable && *v.verdict == *verdict)
            .cloned()
            .collect()
    }

    fn all_live_votes(&self) -> Vec<Vote> {
        self.live()
            .filter_map(|(_, a)| a.vote.clone())
            .collect()
    }

    /// TLA+ `Exhausted`: no live agent can make further progress.
    ///
    /// An agent is "done" when its budget is spent OR its vote is already
    /// stable — a stable vote is never re-asked (the spec guards `Reconsult`
    /// on `~stable[a]`). Treating only the budget as exhaustion leaves a round
    /// where every agent has settled but no quorum formed spinning with no
    /// enabled action, which is the livelock the spec forbids.
    fn exhausted(&self) -> bool {
        self.live().all(|(_, a)| {
            a.asked >= self.cfg.max_asks
                || a.vote.as_ref().map(|v| v.stable).unwrap_or(false)
        })
    }

    /// TLA+ `Decide` and `Escalate`, evaluated in that order.
    ///
    /// Returns `None` while the round is still in progress.
    pub fn try_decide(&mut self) -> Option<RoundOutcome> {
        if let Some(o) = &self.outcome {
            return Some(o.clone()); // TLA+ Irrevocable
        }

        // Decide: a quorum of stable votes from live agents.
        for verdict in [Verdict::Yes, Verdict::No] {
            let supporting = self.stable_votes_for(&verdict);
            if supporting.len() as i32 >= self.cfg.quorum() {
                let payload = DecisionPayload {
                    round_id: self.cfg.round_id.clone(),
                    outcome: Box::new(verdict),
                    quorum: self.cfg.quorum(),
                    supporting_votes: supporting,
                };
                let out = RoundOutcome::Decided(payload);
                self.outcome = Some(out.clone());
                return Some(out);
            }
        }

        // Escalate: budget spent, no stable quorum available.
        let live_count = self.live().count() as i32;
        if live_count < self.cfg.quorum() {
            let payload = EscalationPayload {
                round_id: self.cfg.round_id.clone(),
                outcome: Box::new(Outcome::Uncertain),
                reason: Box::new(EscalationReason::InsufficientLiveAgents),
                observed_votes: self.all_live_votes(),
            };
            let out = RoundOutcome::Escalated(payload);
            self.outcome = Some(out.clone());
            return Some(out);
        }

        if self.exhausted() {
            let payload = EscalationPayload {
                round_id: self.cfg.round_id.clone(),
                outcome: Box::new(Outcome::Uncertain),
                reason: Box::new(EscalationReason::BudgetExhausted),
                observed_votes: self.all_live_votes(),
            };
            let out = RoundOutcome::Escalated(payload);
            self.outcome = Some(out.clone());
            return Some(out);
        }

        None
    }

    /// Drive the round to completion.
    ///
    /// Oracle failures are treated as agent unavailability, not as round
    /// failure. A transient transport error or rate limit must not abort a
    /// decision that the remaining agents can still make — in a regulated
    /// setting a network blip that voids an otherwise sound consensus is
    /// itself a hazard, because it pushes load onto human review for no
    /// clinical reason.
    ///
    /// An agent whose consultations keep failing simply stops contributing.
    /// If too few agents remain to reach quorum, the round escalates through
    /// the normal path with its evidence intact, rather than surfacing a raw
    /// transport error.
    ///
    /// Terminates because every iteration either consults (strictly increasing
    /// some `asked[a]`, bounded by `max_asks`) or reaches an outcome. This is
    /// the operational counterpart of `EventuallyDecided`.
    pub fn run(&mut self) -> Result<RoundOutcome, OracleError> {
        loop {
            if let Some(o) = self.try_decide() {
                return Ok(o);
            }
            let ids: Vec<String> = self
                .live()
                .filter(|(_, a)| a.asked < self.cfg.max_asks)
                .map(|(id, _)| id.clone())
                .collect();

            let mut progressed = false;
            for id in ids {
                match self.consult(&id) {
                    Ok(true) => progressed = true,
                    Ok(false) => {}
                    Err(e) => {
                        // Charge the attempt so a persistently failing agent
                        // exhausts its budget rather than looping forever, and
                        // record why it dropped out.
                        if let Some(agent) = self.agents.get_mut(&id) {
                            agent.asked += 1;
                        }
                        self.oracle_failures.push((id.clone(), e.to_string()));
                        progressed = true;
                    }
                }
                if let Some(o) = self.try_decide() {
                    return Ok(o);
                }
            }

            if !progressed {
                // No action was enabled and no outcome reached. The spec
                // forbids this state; escalate rather than spin.
                let payload = EscalationPayload {
                    round_id: self.cfg.round_id.clone(),
                    outcome: Box::new(Outcome::Uncertain),
                    reason: Box::new(EscalationReason::NoStableQuorum),
                    observed_votes: self.all_live_votes(),
                };
                let out = RoundOutcome::Escalated(payload);
                self.outcome = Some(out.clone());
                return Ok(out);
            }
        }
    }

    /// Oracle failures encountered during the round, as (agent, reason).
    ///
    /// Surfaced rather than swallowed: a round that escalated because half the
    /// agents could not reach the oracle is operationally different from one
    /// that escalated because the question was genuinely hard, and an operator
    /// must be able to tell them apart.
    pub fn oracle_failures(&self) -> &[(String, String)] {
        &self.oracle_failures
    }

    pub fn request_ids(&self) -> &[String] {
        &self.request_ids
    }

    pub fn asked(&self, agent_id: &str) -> i32 {
        self.agents.get(agent_id).map(|a| a.asked).unwrap_or(0)
    }
}
