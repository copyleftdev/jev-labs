use serde::{Deserialize, Serialize};
// Vote represents a Vote model.
#[derive(Clone, Debug, Deserialize, PartialEq, PartialOrd, Serialize)]
pub struct Vote {
    #[serde(rename="roundId")]
    pub round_id: String,
    #[serde(rename="agentId")]
    pub agent_id: String,
    #[serde(rename="verdict")]
    pub verdict: Box<crate::Verdict>,
    #[serde(rename="stable")]
    pub stable: bool,
    #[serde(rename="attempt")]
    pub attempt: i32,
    #[serde(rename="evidence", skip_serializing_if = "Option::is_none")]
    pub evidence: Option<Box<crate::StabilityEvidence>>,
}
