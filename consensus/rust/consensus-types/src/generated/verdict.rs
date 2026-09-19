use serde::{Deserialize, Serialize};
// Verdict represents a Verdict model.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
pub enum Verdict {
    #[serde(rename="yes")]
    Yes,
    #[serde(rename="no")]
    No,
}
