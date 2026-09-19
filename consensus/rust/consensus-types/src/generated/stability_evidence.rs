use serde::{Deserialize, Serialize};
// StabilityEvidence represents a StabilityEvidence model.
#[derive(Clone, Debug, Deserialize, PartialEq, PartialOrd, Serialize)]
pub struct StabilityEvidence {
    #[serde(rename="probability")]
    pub probability: f64,
    #[serde(rename="threshold")]
    pub threshold: f64,
    #[serde(rename="noiseFloor")]
    pub noise_floor: f64,
    #[serde(rename="margin")]
    pub margin: f64,
    #[serde(rename="stable")]
    pub stable: bool,
    #[serde(rename="noiseFloorSource", skip_serializing_if = "Option::is_none")]
    pub noise_floor_source: Option<String>,
}
