//! Semantic consensus kernel.
//!
//! Implements the state machine model-checked in
//! `spec/SemanticConsensusV2.tla`, over wire types generated from
//! `api/asyncapi.yaml`.
//!
//! The oracle is a trait so the same coordinator runs against a deterministic
//! script and against live Jev. That is what lets a TLA+ counterexample be
//! replayed through the real implementation.

pub mod coordinator;
pub mod jev;
pub mod chaos;
pub mod oracle;
pub mod pharmacy;
pub mod quorum;
pub mod scripted;

pub use coordinator::{Round, RoundConfig, RoundOutcome};
pub use oracle::{Calibration, Judgment, Oracle, OracleError};
pub use quorum::{QuorumError, QuorumPolicy};
pub use scripted::{ConstantOracle, ScriptedOracle};
