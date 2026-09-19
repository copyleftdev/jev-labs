use serde::{Deserialize, Serialize};
// EscalationReason represents a EscalationReason model.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
pub enum EscalationReason {
    #[serde(rename="no_stable_quorum")]
    NoStableQuorum,
    #[serde(rename="budget_exhausted")]
    BudgetExhausted,
    #[serde(rename="insufficient_live_agents")]
    InsufficientLiveAgents,
}
