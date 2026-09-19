#!/usr/bin/env bash
# Full verification chain: spec -> contract -> code.
#
#   ./verify.sh          # everything
#   ./verify.sh --scale  # include the large TLA+ models (~2 min)
#
# Each stage gates the next. A failure anywhere means the chain is broken and
# the generated code can no longer claim to implement a verified protocol.
set -uo pipefail
cd "$(dirname "$0")"
fail=0

step() {
    echo
    echo "################################################################"
    echo "# $1"
    echo "################################################################"
}

step "1/3  TLA+ model checking (spec/)"
if ! ./spec/check.sh "${1:-}"; then fail=1; fi

step "2/3  AsyncAPI contract validation + Rust codegen (api/)"
if ! ./api/codegen.sh; then fail=1; fi

step "3/3  Rust invariant tests + kernel (rust/)"
if ! (cd rust/consensus-types && cargo test --quiet); then fail=1; fi
if ! (cd rust/consensus-kernel && cargo test --quiet); then fail=1; fi

echo
if [ "$fail" -eq 0 ]; then
    cat <<'EOF'
================================================================
VERIFICATION CHAIN INTACT

  TLA+ spec            model-checked, incl. the deliberate
                       counterexamples that prove the invariants bite
  AsyncAPI contract    validates clean, derived from the spec
  Rust types           generated from the contract, compiling
  Invariant tests      TLC's counterexample rejected in real code
  Kernel               Decide/Escalate state machine, TLA+ traces replayed

  Traceability:        spec/TRACEABILITY.md
  Live demo:           cd rust/consensus-kernel &&
                       cargo run --example live_consensus
================================================================
EOF
else
    echo "VERIFICATION FAILED -- see output above" >&2
fi
exit "$fail"
