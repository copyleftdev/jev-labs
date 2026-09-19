#!/usr/bin/env bash
# Model-check every variant of the semantic consensus spec.
#
#   ./check.sh          # core variants
#   ./check.sh --scale  # plus larger models (slower, ~2 min)
#
# The two FAILING configs are deliberate and must keep failing: they are the
# evidence that our invariants have teeth. A spec where nothing can break is
# decoration, not verification.
#
# -deadlock is passed throughout: a terminating protocol has no enabled action
# after it decides, which TLC would otherwise report as deadlock. We check
# safety and liveness explicitly instead.
set -uo pipefail
cd "$(dirname "$0")"
TLA="${TLA_TOOLS:-$HOME/tla/tla2tools.jar}"

if [ ! -f "$TLA" ]; then
    echo "tla2tools.jar not found at $TLA" >&2
    echo "  mkdir -p ~/tla && curl -sL -o ~/tla/tla2tools.jar \\" >&2
    echo "    https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar" >&2
    exit 1
fi

fail=0

run() {
    local name="$1" expect="$2" desc="$3"
    printf '%-16s %s\n' "$name" "$desc"
    out=$(timeout 600 java -cp "$TLA" tlc2.TLC -deadlock -config "$name.cfg" "$name.tla" 2>&1)
    states=$(echo "$out" | grep -oE '[0-9,]+ distinct states found' | tail -1)
    if echo "$out" | grep -q "No error has been found"; then
        got="pass"
    elif echo "$out" | grep -qE "Error: (Invariant|Temporal|Deadlock)"; then
        got="fail"
    else
        got="unknown"
    fi
    if [ "$got" = "$expect" ]; then
        printf '   OK   expected=%-4s got=%-4s  %s\n\n' "$expect" "$got" "$states"
    else
        printf '   XX   expected=%-4s got=%-4s  <-- REGRESSION\n\n' "$expect" "$got"
        echo "$out" | grep -E "Error:" | head -3
        fail=1
    fi
}

echo "=== V1: safety of the stability gate ==="
run MCNaive      fail "naive quorum -> irreproducible decision"
run MCStable     pass "stable-only quorum -> safe"

echo "=== V2: crash faults + liveness ==="
run MCNoEscalate fail "no escalation -> jitter livelock"
run MCEscalate   pass "escalation -> safe AND live"

echo "=== V3: byzantine agents, message loss, concurrent coordinators ==="
run MCSplitBrain fail "Q=2 of N=4 -> disjoint quorums, split brain"
run MCIntersect  pass "Q=3 of N=4 -> quorums intersect"
run MCByzantine  pass "Q=3 with f=1 -> honest majority holds"

echo "=== V4: liveness UNDER byzantine faults ==="
run MCVacuity         fail "non-vacuity: an honest quorum IS reachable"
run MCByzLive         pass "f=1 cannot force escalation or flip a decision"
run MCByzLiveUnsound  pass "liveness quality survives even an unsound quorum"
run MCtight_N4        pass "tight: honest agents exactly at quorum"

if [ "${1:-}" = "--scale" ]; then
    echo "=== V2 at scale ==="
    AG5="{a1,a2,a3,a4,a5}"
    sed "s/Agents = {a1, a2, a3}/Agents = $AG5/; s/Quorum = 2/Quorum = 3/; s/MaxCrashes = 1/MaxCrashes = 2/" \
        MCEscalate.cfg > MCScale.cfg
    cp MCEscalate.tla MCScale.tla
    sed -i 's/MODULE MCEscalate/MODULE MCScale/' MCScale.tla
    run MCScale  pass "5 agents, quorum 3, 2 crashes"
fi

if [ "$fail" -eq 0 ]; then
    echo "all model checks behaved as expected"
else
    echo "REGRESSION: at least one model check changed behaviour" >&2
fi
exit "$fail"
