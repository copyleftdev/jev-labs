use serde::{Deserialize, Serialize};
// CrashReason represents a CrashReason model.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
pub enum CrashReason {
    #[serde(rename="heartbeat_timeout")]
    HeartbeatTimeout,
    #[serde(rename="transport_error")]
    TransportError,
    #[serde(rename="explicit_shutdown")]
    ExplicitShutdown,
}
