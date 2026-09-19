use serde::{Deserialize, Serialize};
// ConsultRequestPayload represents a ConsultRequestPayload model.
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
pub struct ConsultRequestPayload {
    #[serde(rename="roundId")]
    pub round_id: String,
    #[serde(rename="agentId")]
    pub agent_id: String,
    #[serde(rename="attempt")]
    pub attempt: i32,
    #[serde(rename="question")]
    pub question: String,
    #[serde(rename="state", skip_serializing_if = "Option::is_none")]
    pub state: Option<String>,
}
