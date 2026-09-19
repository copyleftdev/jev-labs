//! Wire types for the semantic consensus kernel.
//!
//! These types are GENERATED from `api/asyncapi.yaml`, which is itself derived
//! from the model-checked TLA+ specification in `spec/SemanticConsensusV2.tla`.
//! Regenerate with `api/codegen.sh`; do not edit `src/generated` by hand.
//!
//! The chain is: TLA+ action/variable -> AsyncAPI schema -> Rust type.
//! `spec/TRACEABILITY.md` maps each element back to the property it enforces.
//!
//! `invariants` provides receiver-side checks so a decision can be verified
//! from the message alone rather than by trusting the coordinator.

mod generated;
pub use generated::*;

pub mod invariants;
pub use invariants::InvariantError;
