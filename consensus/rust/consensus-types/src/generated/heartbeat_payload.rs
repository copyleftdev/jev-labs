use serde::{Deserialize, Serialize};
// HeartbeatPayload represents a HeartbeatPayload model.
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
pub struct HeartbeatPayload {
    #[serde(rename="roundId")]
    pub round_id: String,
    #[serde(rename="agentId")]
    pub agent_id: String,
    #[serde(rename="sentAt")]
    pub sent_at: String,
}
