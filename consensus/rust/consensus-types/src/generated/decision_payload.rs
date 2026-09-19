use serde::{Deserialize, Serialize};
// DecisionPayload represents a DecisionPayload model.
#[derive(Clone, Debug, Deserialize, PartialEq, PartialOrd, Serialize)]
pub struct DecisionPayload {
    #[serde(rename="roundId")]
    pub round_id: String,
    #[serde(rename="outcome")]
    pub outcome: Box<crate::Verdict>,
    #[serde(rename="quorum")]
    pub quorum: i32,
    #[serde(rename="supportingVotes")]
    pub supporting_votes: Vec<crate::Vote>,
}
