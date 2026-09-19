#!/usr/bin/env bash
# Derive the quorum bound empirically instead of assuming the textbook one.
#
#   ./sweep_quorum.sh
#
# For each (agents N, byzantine f, quorum Q) we ask TLC whether safety holds.
# The predicted bound, from the two invariants:
#
#   Agreement     needs quorums to intersect:        2Q > N
#   SoundDecision needs an honest majority in any
#                 quorum, worst case Q-f honest:     2(Q-f) > Q  =>  Q > 2f
#
#   therefore     Q > max(N/2, 2f)
#
# The sweep prints PREDICT vs ACTUAL for every cell. Any disagreement means the
# reasoning above is wrong, which is exactly what we want to find out.
set -uo pipefail
cd "$(dirname "$0")"
TLA="${TLA_TOOLS:-$HOME/tla/tla2tools.jar}"

agents_set() {
    local n=$1 out="{"
    for i in $(seq 1 "$n"); do
        [ "$i" -gt 1 ] && out="$out, "
        out="$out a$i"
    done
    echo "$out }"
}

byz_set() {
    local n=$1 f=$2 out="{"
    for i in $(seq $((n - f + 1)) "$n"); do
        [ "$i" -gt $((n - f + 1)) ] && out="$out, "
        out="$out a$i"
    done
    [ "$f" -eq 0 ] && out="{"
    echo "$out }"
}

mismatch=0
printf '%3s %3s %3s  %-8s %-8s %s\n' N f Q PREDICT ACTUAL ""
for n in 3 4 5; do
    for f in 0 1; do
        [ "$f" -ge "$n" ] && continue
        for q in $(seq 1 "$n"); do
            # Predicted: safe iff 2Q > N and Q > 2f
            if [ $((2 * q)) -gt "$n" ] && [ "$q" -gt $((2 * f)) ]; then
                predict="safe"
            else
                predict="unsafe"
            fi

            cat > MCSweep.cfg <<EOF
SPECIFICATION Spec
CONSTANTS
    Agents = $(agents_set "$n")
    Byzantine = $(byz_set "$n" "$f")
    Coordinators = {c1, c2}
    Verdicts = {yes, no}
    Quorum = $q
    MaxCrashes = 0
INVARIANTS
    TypeOK
    Agreement
    SoundDecision
EOF
            cp SemanticConsensusV3.tla MCSweep.tla
            sed -i 's/MODULE SemanticConsensusV3/MODULE MCSweep/' MCSweep.tla
            out=$(timeout 600 java -cp "$TLA" tlc2.TLC -deadlock -config MCSweep.cfg MCSweep.tla 2>&1)
            if echo "$out" | grep -q "No error has been found"; then
                actual="safe"
            elif echo "$out" | grep -q "Error: Invariant"; then
                actual="unsafe"
                broke=$(echo "$out" | grep -oE "Invariant [A-Za-z]+" | head -1)
            else
                actual="?"
            fi

            note=""
            if [ "$actual" = "unsafe" ]; then note="${broke:-}"; fi
            flag=" "
            if [ "$predict" != "$actual" ]; then flag="<-- MISMATCH"; mismatch=1; fi
            printf '%3d %3d %3d  %-8s %-8s %s %s\n' "$n" "$f" "$q" "$predict" "$actual" "$note" "$flag"
        done
    done
done

rm -f MCSweep.cfg MCSweep.tla
echo
if [ "$mismatch" -eq 0 ]; then
    echo "bound CONFIRMED: safe iff 2Q > N and Q > 2f"
else
    echo "bound WRONG: at least one cell disagrees with the prediction" >&2
fi
exit "$mismatch"
