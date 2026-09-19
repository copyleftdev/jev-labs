--------------------------- MODULE MCByzantine ---------------------------
(***************************************************************************)
(* Closing the adversarial and distribution gaps left open by V2.          *)
(*                                                                         *)
(* V2 proved safety and liveness under crash faults with a single          *)
(* coordinator and perfect observation. Three assumptions were doing quiet  *)
(* work there, and each is a real gap:                                     *)
(*                                                                         *)
(*   G-A  every agent is correct-but-noisy. An agent that LIES about its   *)
(*        stability defeats DecisionIsReproducible directly.               *)
(*   G-B  the coordinator sees every vote. With message loss it sees a     *)
(*        subset, and two coordinators may see DIFFERENT subsets.          *)
(*   G-C  there is one coordinator. A crash mid-round means a successor    *)
(*        decides from its own partial view.                               *)
(*                                                                         *)
(* This spec models all three at once, because they interact: split        *)
(* observation is only dangerous when quorums can fail to intersect, and   *)
(* forged stability is only dangerous when a quorum can be mostly liars.   *)
(*                                                                         *)
(* The point is to DERIVE the bounds rather than assume the textbook ones. *)
(* We let TLC tell us how large a quorum must be, relative to the number   *)
(* of agents and the number of liars, for the protocol to stay sound.      *)
(*                                                                         *)
(* Scope: SAFETY only. V2 owns liveness; re-checking it here would double  *)
(* the state space for no new information.                                 *)
(***************************************************************************)
EXTENDS Integers, FiniteSets, TLC

CONSTANTS
    Agents,        \* all agent identifiers
    Byzantine,     \* subset of Agents that may forge their stability claim
    Coordinators,  \* concurrent or successive deciders
    Verdicts,      \* the real decision space
    Quorum,        \* claimed-stable votes required to decide
    MaxCrashes     \* how many agents may crash

ABSTAIN   == "abstain"
UNCERTAIN == "uncertain"

Votes    == Verdicts \cup {ABSTAIN}
Outcomes == Verdicts \cup {ABSTAIN, UNCERTAIN}

Honest == Agents \ Byzantine

VARIABLES
    vote,         \* [Agents -> Votes]      the verdict an agent reports
    claimStable,  \* [Agents -> BOOLEAN]    stability the agent CLAIMS
    trueStable,   \* [Agents -> BOOLEAN]    whether it would really survive a re-ask
    crashed,      \* [Agents -> BOOLEAN]
    observed,     \* [Coordinators -> SUBSET Agents]  votes actually delivered
    decided       \* [Coordinators -> Outcomes]

vars == <<vote, claimStable, trueStable, crashed, observed, decided>>

Live == {a \in Agents : ~crashed[a]}

TypeOK ==
    /\ vote        \in [Agents -> Votes]
    /\ claimStable \in [Agents -> BOOLEAN]
    /\ trueStable  \in [Agents -> BOOLEAN]
    /\ crashed     \in [Agents -> BOOLEAN]
    /\ observed    \in [Coordinators -> SUBSET Agents]
    /\ decided     \in [Coordinators -> Outcomes]

Init ==
    /\ vote        = [a \in Agents |-> ABSTAIN]
    /\ claimStable = [a \in Agents |-> FALSE]
    /\ trueStable  = [a \in Agents |-> FALSE]
    /\ crashed     = [a \in Agents |-> FALSE]
    /\ observed    = [c \in Coordinators |-> {}]
    /\ decided     = [c \in Coordinators |-> ABSTAIN]

(***************************************************************************)
(* Consult                                                                 *)
(*                                                                         *)
(* An HONEST agent reports what the oracle actually gave it: its claim     *)
(* matches the truth. The coordinator recomputes `margin > noiseFloor`     *)
(* from the evidence carried in the vote, so an honest agent cannot even   *)
(* accidentally misreport.                                                 *)
(*                                                                         *)
(* A BYZANTINE agent forges *self-consistent* evidence: it reports a       *)
(* probability and noise floor that justify `stable = TRUE` while the      *)
(* judgment would not actually survive a re-ask. Recomputation does not    *)
(* catch this -- the arithmetic checks out. Only an independent re-ask of  *)
(* the oracle would, and no such channel exists (there is no usage or      *)
(* attestation API on the provider side).                                  *)
(***************************************************************************)
ConsultHonest(a) ==
    /\ a \in Honest
    /\ ~crashed[a]
    /\ vote[a] = ABSTAIN
    /\ \E v \in Verdicts, s \in BOOLEAN :
         /\ vote'        = [vote        EXCEPT ![a] = v]
         /\ trueStable'  = [trueStable  EXCEPT ![a] = s]
         /\ claimStable' = [claimStable EXCEPT ![a] = s]   \* cannot lie
    /\ UNCHANGED <<crashed, observed, decided>>

ConsultByzantine(a) ==
    /\ a \in Byzantine
    /\ ~crashed[a]
    /\ vote[a] = ABSTAIN
    /\ \E v \in Verdicts, truth \in BOOLEAN, claim \in BOOLEAN :
         /\ vote'        = [vote        EXCEPT ![a] = v]
         /\ trueStable'  = [trueStable  EXCEPT ![a] = truth]
         /\ claimStable' = [claimStable EXCEPT ![a] = claim]  \* may forge
    /\ UNCHANGED <<crashed, observed, decided>>

Crash(a) ==
    /\ ~crashed[a]
    /\ Cardinality({x \in Agents : crashed[x]}) < MaxCrashes
    /\ crashed' = [crashed EXCEPT ![a] = TRUE]
    /\ UNCHANGED <<vote, claimStable, trueStable, observed, decided>>

(***************************************************************************)
(* Deliver                                                                 *)
(*                                                                         *)
(* A vote reaches ONE coordinator. Message loss is modelled by never       *)
(* delivering; reordering is irrelevant because votes are idempotent       *)
(* facts rather than sequenced commands. Because delivery is per           *)
(* coordinator, two coordinators can hold different views of the same      *)
(* round -- which is exactly the split-brain condition we want to probe.   *)
(***************************************************************************)
Deliver(c, a) ==
    /\ vote[a] # ABSTAIN
    /\ a \notin observed[c]
    /\ observed' = [observed EXCEPT ![c] = @ \cup {a}]
    /\ UNCHANGED <<vote, claimStable, trueStable, crashed, decided>>

(***************************************************************************)
(* What a coordinator can see: live agents whose votes it received and     *)
(* whose CLAIMED stability passes verification. It cannot consult          *)
(* trueStable -- that is the adversary's private state.                    *)
(***************************************************************************)
SeenStableFor(c, v) ==
    {a \in observed[c] : ~crashed[a] /\ vote[a] = v /\ claimStable[a]}

Decide(c) ==
    /\ decided[c] = ABSTAIN
    /\ \E v \in Verdicts :
         /\ Cardinality(SeenStableFor(c, v)) >= Quorum
         /\ decided' = [decided EXCEPT ![c] = v]
    /\ UNCHANGED <<vote, claimStable, trueStable, crashed, observed>>

Next ==
    \/ \E a \in Agents : ConsultHonest(a)
    \/ \E a \in Agents : ConsultByzantine(a)
    \/ \E a \in Agents : Crash(a)
    \/ \E c \in Coordinators, a \in Agents : Deliver(c, a)
    \/ \E c \in Coordinators : Decide(c)

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* SAFETY PROPERTIES                                                       *)
(***************************************************************************)

\* G-B / G-C. Two coordinators must never commit different verdicts. This is
\* the quorum-intersection requirement: with N live agents, disjoint quorums
\* are possible unless 2*Quorum > N.
Agreement ==
    \A c1, c2 \in Coordinators :
        (decided[c1] \in Verdicts /\ decided[c2] \in Verdicts)
            => decided[c1] = decided[c2]

\* G-A. A committed verdict must rest on a STRICT MAJORITY of votes that are
\* both honest and genuinely stable. Bare survival (>= 1 honest vote) is too
\* weak: it would let a quorum of liars carry a decision that one honest agent
\* happened to agree with. Requiring a majority means the forgers cannot be
\* the reason the decision happened.
SoundDecision ==
    \A c \in Coordinators :
        decided[c] \in Verdicts =>
            Cardinality({a \in Honest :
                            /\ a \in observed[c]
                            /\ ~crashed[a]
                            /\ vote[a] = decided[c]
                            /\ trueStable[a]}) * 2 > Quorum

\* The V2 property, restated against what a coordinator can actually check.
\* This holds by construction; it is kept so a regression in Decide is caught.
DecisionIsVerifiable ==
    \A c \in Coordinators :
        decided[c] \in Verdicts =>
            Cardinality(SeenStableFor(c, decided[c])) >= Quorum

=============================================================================
