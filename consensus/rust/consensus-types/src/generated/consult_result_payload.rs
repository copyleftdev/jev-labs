use serde::{Deserialize, Serialize};
// ConsultResultPayload represents a ConsultResultPayload model.
#[derive(Clone, Debug, Deserialize, PartialEq, PartialOrd, Serialize)]
pub struct ConsultResultPayload {
    #[serde(rename="roundId")]
    pub round_id: String,
    #[serde(rename="agentId")]
    pub agent_id: String,
    #[serde(rename="attempt")]
    pub attempt: i32,
    #[serde(rename="verdict")]
    pub verdict: Box<crate::Verdict>,
    #[serde(rename="evidence")]
    pub evidence: Box<crate::StabilityEvidence>,
    #[serde(rename="oracleRequestId", skip_serializing_if = "Option::is_none")]
    pub oracle_request_id: Option<String>,
}
