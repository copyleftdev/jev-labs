---------------------------- MODULE MCStable ----------------------------
(***************************************************************************)
(* Consensus among agents whose votes come from a NON-DETERMINISTIC        *)
(* semantic oracle.                                                        *)
(*                                                                         *)
(* Classical consensus (Paxos, Raft) assumes each participant has a value  *)
(* it *knows*. Here each agent derives its vote from a judgment oracle     *)
(* (Jev) that is measurably non-deterministic: identical requests returned *)
(* 0.03, 0.03, 0.03, 0.04, 0.04 in measurement. So an agent asked the same *)
(* question twice may legitimately vote differently, with no fault and no  *)
(* malice involved.                                                        *)
(*                                                                         *)
(* That breaks a hidden assumption in classical specs: that a correct      *)
(* process's proposed value is stable. We must model vote instability as   *)
(* normal behaviour rather than as a fault, or we will verify a protocol   *)
(* that cannot exist.                                                      *)
(*                                                                         *)
(* Design decision under test: is the protocol still safe when a correct   *)
(* agent's vote can flip because the oracle jittered near a threshold?     *)
(*                                                                         *)
(* Measured inputs that motivate the model (see ../../ measurements):      *)
(*   - oracle jitter on identical input: ~+/-0.01                          *)
(*   - noise floor across multi-call transforms: 0.042 - 0.073             *)
(*   - 88% of probability mass lands below 0.1 or above 0.9                *)
(*   - mid-range answers (0.2-0.8) were correct 20/20: hedging is          *)
(*     one-directional, NOT genuine uncertainty                            *)
(***************************************************************************)
EXTENDS Integers, FiniteSets, Sequences, TLC

CONSTANTS
    Agents,          \* set of agent identifiers
    Quorum,          \* number of agreeing votes required to decide
    MaxRounds,       \* bound on rounds, for model checking
    Verdicts,        \* the decision space, e.g. {"yes","no"}
    RequireStable    \* TRUE = only count votes outside the jitter band

(***************************************************************************)
(* An agent's vote is ABSTAIN until it has consulted the oracle. The key   *)
(* modelling choice: a vote drawn near the decision threshold is marked    *)
(* UNSTABLE, meaning the oracle could return the other side on a re-ask.   *)
(* This is the TLA+ encoding of the measured jitter.                       *)
(***************************************************************************)
ABSTAIN == "abstain"
Votes == Verdicts \cup {ABSTAIN}

VARIABLES
    vote,        \* [Agents -> Votes]  current vote of each agent
    stable,      \* [Agents -> BOOLEAN] is this agent's vote outside the jitter band
    round,       \* current round number
    decided,     \* the decided value, or ABSTAIN if none
    asked        \* [Agents -> Nat] how many times each agent consulted the oracle

vars == <<vote, stable, round, decided, asked>>

TypeOK ==
    /\ vote    \in [Agents -> Votes]
    /\ stable  \in [Agents -> BOOLEAN]
    /\ round   \in 0..MaxRounds
    /\ decided \in Votes
    /\ asked   \in [Agents -> 0..MaxRounds]

Init ==
    /\ vote    = [a \in Agents |-> ABSTAIN]
    /\ stable  = [a \in Agents |-> FALSE]
    /\ round   = 0
    /\ decided = ABSTAIN
    /\ asked   = [a \in Agents |-> 0]

(***************************************************************************)
(* Consult: an agent asks the oracle and receives a verdict. The verdict   *)
(* is non-deterministic (we quantify over all possible answers) and it may *)
(* be stable or unstable. An unstable answer is one the oracle could flip  *)
(* on a re-ask -- exactly the measured near-threshold behaviour.           *)
(***************************************************************************)
Consult(a) ==
    /\ decided = ABSTAIN
    /\ asked[a] < MaxRounds
    /\ \E v \in Verdicts, s \in BOOLEAN :
         /\ vote'   = [vote   EXCEPT ![a] = v]
         /\ stable' = [stable EXCEPT ![a] = s]
    /\ asked' = [asked EXCEPT ![a] = @ + 1]
    /\ UNCHANGED <<round, decided>>

(***************************************************************************)
(* Reconsult: an agent with an UNSTABLE vote re-asks and may get the other *)
(* answer. This is the heart of the model. In classical consensus a        *)
(* correct process never changes its mind for no reason; here it can, and  *)
(* the protocol must tolerate it.                                          *)
(***************************************************************************)
Reconsult(a) ==
    /\ decided = ABSTAIN
    /\ vote[a] # ABSTAIN
    /\ ~stable[a]                     \* only unstable votes can flip
    /\ asked[a] < MaxRounds
    /\ \E v \in Verdicts, s \in BOOLEAN :
         /\ vote'   = [vote   EXCEPT ![a] = v]
         /\ stable' = [stable EXCEPT ![a] = s]
    /\ asked' = [asked EXCEPT ![a] = @ + 1]
    /\ UNCHANGED <<round, decided>>

VotesFor(v) == {a \in Agents : vote[a] = v}
StableVotesFor(v) == {a \in Agents : vote[a] = v /\ stable[a]}

(***************************************************************************)
(* NAIVE DECISION RULE -- counts any quorum, stable or not.                *)
(* This is what an engineer writes first. The model checker should find    *)
(* that it can decide on a quorum built from votes that could all flip.    *)
(***************************************************************************)
DecideNaive ==
    /\ decided = ABSTAIN
    /\ \E v \in Verdicts :
         /\ Cardinality(VotesFor(v)) >= Quorum
         /\ decided' = v
    /\ UNCHANGED <<vote, stable, round, asked>>

(***************************************************************************)
(* STABLE DECISION RULE -- requires the quorum to consist of votes that    *)
(* sit outside the jitter band. This is the fix we expect to verify.       *)
(***************************************************************************)
DecideStable ==
    /\ decided = ABSTAIN
    /\ \E v \in Verdicts :
         /\ Cardinality(StableVotesFor(v)) >= Quorum
         /\ decided' = v
    /\ UNCHANGED <<vote, stable, round, asked>>

Tick ==
    /\ round < MaxRounds
    /\ round' = round + 1
    /\ UNCHANGED <<vote, stable, decided, asked>>

(***************************************************************************)
(* RequireStable selects which decision rule is in force, so both variants *)
(* are checked from one spec with two configs. Setting it FALSE should     *)
(* produce a counterexample to DecisionIsReproducible; TRUE should not.    *)
(***************************************************************************)
Decide == IF RequireStable THEN DecideStable ELSE DecideNaive

Next ==
    \/ \E a \in Agents : Consult(a)
    \/ \E a \in Agents : Reconsult(a)
    \/ Decide
    \/ Tick

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* SAFETY PROPERTIES                                                       *)
(***************************************************************************)

\* Once decided, the decision never changes.
Irrevocable == [][decided # ABSTAIN => decided' = decided]_vars

\* A decision is only ever taken when a real quorum supported it.
Justified ==
    decided # ABSTAIN => Cardinality(VotesFor(decided)) >= Quorum

(***************************************************************************)
(* THE INTERESTING ONE.                                                    *)
(* A decision must not rest on votes that could flip. If this is violated, *)
(* the protocol can commit to a verdict that the very same agents, asked   *)
(* again one second later, would not support -- an irreproducible decision.*)
(* For a consensus kernel whose output is auditable, that is fatal.        *)
(***************************************************************************)
DecisionIsReproducible ==
    decided # ABSTAIN => Cardinality(StableVotesFor(decided)) >= Quorum

=============================================================================
