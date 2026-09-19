--------------------------- MODULE MCScale ---------------------------
(***************************************************************************)
(* Semantic consensus with crash faults and LIVENESS.                      *)
(*                                                                         *)
(* V1 established safety: gating the quorum on stable votes prevents an    *)
(* irreproducible decision. But safety is cheap -- a protocol that never   *)
(* decides is trivially safe. The real question is whether the stability   *)
(* gate costs us liveness.                                                 *)
(*                                                                         *)
(* THE SUSPICION: the oracle is free to return an unstable answer every    *)
(* time. If it does, no stable quorum ever forms and the kernel hangs      *)
(* forever on a question that simply sits near the threshold. That is not  *)
(* a fault anyone injected; it is the measured behaviour of the oracle.    *)
(*                                                                         *)
(* This spec checks that suspicion directly, and checks whether bounded    *)
(* escalation repairs it.                                                  *)
(*                                                                         *)
(* Grounding measurements (see repository telemetry, n=1406 calls):        *)
(*   - identical requests returned 0.03,0.03,0.03,0.04,0.04 -> jitter real *)
(*   - noise floor across transforms: 0.042 - 0.073                        *)
(*   - mid-range Noul answers were correct 20/20, so persistent            *)
(*     instability is NOT evidence of a 50/50 question. Escalating it to a *)
(*     human or a reasoning model is the correct response, and UNCERTAIN   *)
(*     must therefore be a first-class outcome rather than a failure.      *)
(***************************************************************************)
EXTENDS Integers, FiniteSets, TLC

CONSTANTS
    Agents,           \* agent identifiers
    Verdicts,         \* real decision space, e.g. {yes, no}
    Quorum,           \* agreeing stable votes needed to decide
    MaxAsks,          \* per-agent budget of oracle consultations
    MaxCrashes,       \* how many agents may crash
    EnableEscalation  \* TRUE = allow the UNCERTAIN outcome

ABSTAIN   == "abstain"     \* no vote yet / no decision yet
UNCERTAIN == "uncertain"   \* escalate: oracle never stabilised

Votes    == Verdicts \cup {ABSTAIN}
Outcomes == Verdicts \cup {ABSTAIN, UNCERTAIN}

VARIABLES
    vote,      \* [Agents -> Votes]
    stable,    \* [Agents -> BOOLEAN]  vote sits outside the jitter band
    asked,     \* [Agents -> 0..MaxAsks]  consultation budget spent
    crashed,   \* [Agents -> BOOLEAN]
    decided    \* Outcomes

vars == <<vote, stable, asked, crashed, decided>>

Live == {a \in Agents : ~crashed[a]}

TypeOK ==
    /\ vote    \in [Agents -> Votes]
    /\ stable  \in [Agents -> BOOLEAN]
    /\ asked   \in [Agents -> 0..MaxAsks]
    /\ crashed \in [Agents -> BOOLEAN]
    /\ decided \in Outcomes

Init ==
    /\ vote    = [a \in Agents |-> ABSTAIN]
    /\ stable  = [a \in Agents |-> FALSE]
    /\ asked   = [a \in Agents |-> 0]
    /\ crashed = [a \in Agents |-> FALSE]
    /\ decided = ABSTAIN

(***************************************************************************)
(* Consult / Reconsult                                                     *)
(*                                                                         *)
(* The oracle answer is fully non-deterministic in BOTH the verdict and    *)
(* its stability. Quantifying over stability is what makes the livelock    *)
(* reachable: nothing in the model forces the oracle to ever settle. That  *)
(* faithfully represents a question whose true probability sits on the     *)
(* decision boundary.                                                      *)
(***************************************************************************)
Consult(a) ==
    /\ decided = ABSTAIN
    /\ ~crashed[a]
    /\ asked[a] < MaxAsks
    /\ \E v \in Verdicts, s \in BOOLEAN :
         /\ vote'   = [vote   EXCEPT ![a] = v]
         /\ stable' = [stable EXCEPT ![a] = s]
    /\ asked'   = [asked EXCEPT ![a] = @ + 1]
    /\ UNCHANGED <<crashed, decided>>

\* An unstable vote may flip on re-ask. A correct agent changing its mind.
Reconsult(a) ==
    /\ decided = ABSTAIN
    /\ ~crashed[a]
    /\ vote[a] # ABSTAIN
    /\ ~stable[a]
    /\ asked[a] < MaxAsks
    /\ \E v \in Verdicts, s \in BOOLEAN :
         /\ vote'   = [vote   EXCEPT ![a] = v]
         /\ stable' = [stable EXCEPT ![a] = s]
    /\ asked'   = [asked EXCEPT ![a] = @ + 1]
    /\ UNCHANGED <<crashed, decided>>

Crash(a) ==
    /\ decided = ABSTAIN
    /\ ~crashed[a]
    /\ Cardinality({x \in Agents : crashed[x]}) < MaxCrashes
    /\ crashed' = [crashed EXCEPT ![a] = TRUE]
    /\ UNCHANGED <<vote, stable, asked, decided>>

\* Only live agents with stable votes count toward a quorum.
StableVotesFor(v) == {a \in Live : vote[a] = v /\ stable[a]}

Decide ==
    /\ decided = ABSTAIN
    /\ \E v \in Verdicts :
         /\ Cardinality(StableVotesFor(v)) >= Quorum
         /\ decided' = v
    /\ UNCHANGED <<vote, stable, asked, crashed>>

(***************************************************************************)
(* Escalate: every live agent has spent its consultation budget and no     *)
(* stable quorum exists. Rather than hang, emit UNCERTAIN so the caller    *)
(* can route to a human or a reasoning model.                              *)
(*                                                                         *)
(* This is the measured behaviour turned into protocol: persistent         *)
(* instability is a signal, not a failure.                                 *)
(***************************************************************************)
Exhausted == \A a \in Live : asked[a] >= MaxAsks
NoStableQuorum == \A v \in Verdicts : Cardinality(StableVotesFor(v)) < Quorum

Escalate ==
    /\ EnableEscalation
    /\ decided = ABSTAIN
    /\ Exhausted
    /\ NoStableQuorum
    /\ decided' = UNCERTAIN
    /\ UNCHANGED <<vote, stable, asked, crashed>>

Next ==
    \/ \E a \in Agents : Consult(a)
    \/ \E a \in Agents : Reconsult(a)
    \/ \E a \in Agents : Crash(a)
    \/ Decide
    \/ Escalate

(***************************************************************************)
(* Weak fairness on Next: the system may not stutter while some action is  *)
(* enabled. It does NOT force the oracle to return any particular answer,  *)
(* which is precisely the freedom that makes the livelock reachable.       *)
(***************************************************************************)
Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

(***************************************************************************)
(* SAFETY                                                                  *)
(***************************************************************************)

\* A real verdict must rest on a quorum of stable votes. UNCERTAIN is
\* exempt by construction: it is the admission that no such quorum existed.
DecisionIsReproducible ==
    decided \in Verdicts => Cardinality(StableVotesFor(decided)) >= Quorum

\* Never escalate when a real decision was actually available.
EscalationIsJustified ==
    decided = UNCERTAIN => NoStableQuorum

Irrevocable == [][decided # ABSTAIN => decided' = decided]_vars

(***************************************************************************)
(* LIVENESS                                                                *)
(* The kernel must always reach some outcome -- a verdict or an explicit   *)
(* escalation. Hanging forever is not an acceptable third option.          *)
(***************************************************************************)
EventuallyDecided == <>(decided # ABSTAIN)

=============================================================================
