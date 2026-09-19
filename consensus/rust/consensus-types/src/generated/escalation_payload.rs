use serde::{Deserialize, Serialize};
// EscalationPayload represents a EscalationPayload model.
#[derive(Clone, Debug, Deserialize, PartialEq, PartialOrd, Serialize)]
pub struct EscalationPayload {
    #[serde(rename="roundId")]
    pub round_id: String,
    #[serde(rename="outcome")]
    pub outcome: Box<crate::Outcome>,
    #[serde(rename="reason")]
    pub reason: Box<crate::EscalationReason>,
    #[serde(rename="observedVotes")]
    pub observed_votes: Vec<crate::Vote>,
}
