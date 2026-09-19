use serde::{Deserialize, Serialize};
// Outcome represents a Outcome model.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
pub enum Outcome {
    #[serde(rename="uncertain")]
    Uncertain,
}
