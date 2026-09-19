use serde::{Deserialize, Serialize};
// CrashNoticePayload represents a CrashNoticePayload model.
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
pub struct CrashNoticePayload {
    #[serde(rename="roundId")]
    pub round_id: String,
    #[serde(rename="agentId")]
    pub agent_id: String,
    #[serde(rename="detectedAt")]
    pub detected_at: String,
    #[serde(rename="reason", skip_serializing_if = "Option::is_none")]
    pub reason: Option<Box<crate::CrashReason>>,
}
