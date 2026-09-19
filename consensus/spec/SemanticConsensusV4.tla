--------------------------- MODULE SemanticConsensusV4 ---------------------------
(***************************************************************************)
(* Liveness UNDER Byzantine faults -- the last gap that could change the   *)
(* protocol rather than merely add plumbing.                               *)
(*                                                                         *)
(* V2 proved liveness without liars. V3 proved safety with them. The       *)
(* intersection was unchecked, and it is not obvious: bounded ask budgets  *)
(* mean the protocol cannot HANG, so naive liveness holds trivially. The   *)
(* real question is sharper.                                               *)
(*                                                                         *)
(*   Can a Byzantine agent force UNCERTAIN when the honest agents, on      *)
(*   their own, held a decisive stable quorum?                             *)
(*                                                                         *)
(* That is an availability attack. Every forced escalation costs a human   *)
(* review, so an adversary who can trigger them at will has a denial-of-   *)
(* service against the review queue even though safety never breaks.       *)
(*                                                                         *)
(* THE ATTACK WE ARE HUNTING: a forger votes for the OPPOSITE verdict and  *)
(* claims stability. It cannot create a false quorum (Q > 2f forbids that) *)
(* but it may be able to SPLIT the stable votes so that neither verdict    *)
(* reaches Q -- turning a clean decision into an escalation.               *)
(*                                                                         *)
(* Scope: liveness quality, not safety. V3 owns safety.                    *)
(***************************************************************************)
EXTENDS Integers, FiniteSets, TLC

CONSTANTS
    Agents,
    Byzantine,
    Verdicts,
    Quorum,
    MaxAsks

ABSTAIN   == "abstain"
UNCERTAIN == "uncertain"

Votes    == Verdicts \cup {ABSTAIN}
Outcomes == Verdicts \cup {ABSTAIN, UNCERTAIN}

Honest == Agents \ Byzantine

VARIABLES
    vote,         \* [Agents -> Votes]
    claimStable,  \* [Agents -> BOOLEAN]  what the agent reports
    trueStable,   \* [Agents -> BOOLEAN]  whether it would survive a re-ask
    asked,        \* [Agents -> 0..MaxAsks]
    decided

vars == <<vote, claimStable, trueStable, asked, decided>>

TypeOK ==
    /\ vote        \in [Agents -> Votes]
    /\ claimStable \in [Agents -> BOOLEAN]
    /\ trueStable  \in [Agents -> BOOLEAN]
    /\ asked       \in [Agents -> 0..MaxAsks]
    /\ decided     \in Outcomes

Init ==
    /\ vote        = [a \in Agents |-> ABSTAIN]
    /\ claimStable = [a \in Agents |-> FALSE]
    /\ trueStable  = [a \in Agents |-> FALSE]
    /\ asked       = [a \in Agents |-> 0]
    /\ decided     = ABSTAIN

(***************************************************************************)
(* An honest agent reports what the oracle gave it. Its vote may be        *)
(* unstable (the oracle jittered), in which case it re-asks.               *)
(***************************************************************************)
ConsultHonest(a) ==
    /\ a \in Honest
    /\ decided = ABSTAIN
    /\ asked[a] < MaxAsks
    /\ \/ vote[a] = ABSTAIN
       \/ ~claimStable[a]              \* only re-ask an unsettled vote
    /\ \E v \in Verdicts, s \in BOOLEAN :
         /\ vote'        = [vote        EXCEPT ![a] = v]
         /\ trueStable'  = [trueStable  EXCEPT ![a] = s]
         /\ claimStable' = [claimStable EXCEPT ![a] = s]
    /\ asked' = [asked EXCEPT ![a] = @ + 1]
    /\ UNCHANGED decided

(***************************************************************************)
(* A Byzantine agent picks any verdict and any stability claim, freely,    *)
(* at any time -- including changing its mind after appearing settled.     *)
(* This is deliberately stronger than the honest agent's freedom: a real   *)
(* adversary is not bound by the re-ask guard.                             *)
(***************************************************************************)
ConsultByzantine(a) ==
    /\ a \in Byzantine
    /\ decided = ABSTAIN
    /\ asked[a] < MaxAsks
    /\ \E v \in Verdicts, truth \in BOOLEAN, claim \in BOOLEAN :
         /\ vote'        = [vote        EXCEPT ![a] = v]
         /\ trueStable'  = [trueStable  EXCEPT ![a] = truth]
         /\ claimStable' = [claimStable EXCEPT ![a] = claim]
    /\ asked' = [asked EXCEPT ![a] = @ + 1]
    /\ UNCHANGED decided

\* What the coordinator can see and count.
ClaimedStableFor(v) == {a \in Agents : vote[a] = v /\ claimStable[a]}

\* What is actually true, used only in properties.
HonestStableFor(v) == {a \in Honest : vote[a] = v /\ trueStable[a]}

Decide ==
    /\ decided = ABSTAIN
    /\ \E v \in Verdicts :
         /\ Cardinality(ClaimedStableFor(v)) >= Quorum
         /\ decided' = v
    /\ UNCHANGED <<vote, claimStable, trueStable, asked>>

Exhausted ==
    \A a \in Agents : asked[a] >= MaxAsks \/ claimStable[a]

NoClaimedQuorum ==
    \A v \in Verdicts : Cardinality(ClaimedStableFor(v)) < Quorum

Escalate ==
    /\ decided = ABSTAIN
    /\ Exhausted
    /\ NoClaimedQuorum
    /\ decided' = UNCERTAIN
    /\ UNCHANGED <<vote, claimStable, trueStable, asked>>

Next ==
    \/ \E a \in Agents : ConsultHonest(a)
    \/ \E a \in Agents : ConsultByzantine(a)
    \/ Decide
    \/ Escalate

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

(***************************************************************************)
(* LIVENESS                                                                *)
(***************************************************************************)

\* Baseline: the protocol always terminates, even with forgers. Expected to
\* hold because ask budgets are bounded and Escalate is always available.
EventuallyDecided == <>(decided # ABSTAIN)

(***************************************************************************)
(* THE PROPERTY THAT MATTERS.                                              *)
(*                                                                         *)
(* If the honest agents alone ever held a genuine stable quorum for some   *)
(* verdict, the protocol must not end in UNCERTAIN. Escalation in that     *)
(* situation is a forced review the adversary caused, not a real           *)
(* uncertainty about the question.                                         *)
(*                                                                         *)
(* This is stated as a state predicate over the FINAL state so it can be   *)
(* checked as an invariant: if we ever reach a terminal UNCERTAIN while    *)
(* an honest quorum stood, the adversary won.                              *)
(***************************************************************************)
HonestQuorumExists ==
    \E v \in Verdicts : Cardinality(HonestStableFor(v)) >= Quorum

NoForcedEscalation ==
    (decided = UNCERTAIN) => ~HonestQuorumExists

(***************************************************************************)
(* The scarier variant: can a forger make the protocol decide AGAINST a    *)
(* verdict the honest agents genuinely held a stable quorum for?           *)
(*                                                                         *)
(* This is safety-critical rather than merely availability. If it can      *)
(* happen, the quorum bound from V3 is insufficient and the design must    *)
(* change.                                                                 *)
(***************************************************************************)
NoContradictionOfHonestQuorum ==
    \A v \in Verdicts :
        (decided \in Verdicts /\ Cardinality(HonestStableFor(v)) >= Quorum)
            => decided = v

(***************************************************************************)
(* WHY NoForcedEscalation HOLDS -- the structural reason, stated as a      *)
(* checkable invariant rather than left as an argument in a comment.       *)
(*                                                                         *)
(* An honest agent's claim equals its truth, so the claimed-stable set is  *)
(* a SUPERSET of the honest-stable set. A forger can only ADD to a claimed *)
(* set, never remove from one. Therefore an honest quorum always presents  *)
(* as a claimed quorum, which disables Escalate and enables Decide.        *)
(*                                                                         *)
(* Monotonicity is the whole argument. If this invariant ever fails, the   *)
(* reasoning behind NoForcedEscalation has broken and the liveness result  *)
(* should not be trusted.                                                  *)
(***************************************************************************)
ClaimedCoversHonest ==
    \A v \in Verdicts :
        HonestStableFor(v) \subseteq ClaimedStableFor(v)

EscalateDisabledUnderHonestQuorum ==
    HonestQuorumExists => ~NoClaimedQuorum

(***************************************************************************)
(* MEASURED RESULT (see check.sh, MCByzLive and MCByzLiveUnsound)          *)
(*                                                                         *)
(* All of NoForcedEscalation, NoContradictionOfHonestQuorum,               *)
(* ClaimedCoversHonest and EscalateDisabledUnderHonestQuorum hold at       *)
(* N=5,f=1,Q=3 (138,129 states) and at the tight N=4,f=1,Q=3.              *)
(*                                                                         *)
(* They ALSO hold at the unsound N=3,f=1,Q=2, which was not expected. The  *)
(* reason is monotonicity: an honest agent's claim equals its truth, so a  *)
(* forger can only ever ADD to a claimed-stable set, never subtract. It    *)
(* cannot un-stabilise an honest vote or make an honest agent disappear.   *)
(* Consequently:                                                           *)
(*                                                                         *)
(*   - an honest quorum always surfaces as a claimed quorum, so Escalate   *)
(*     is disabled and the round decides (availability is safe);           *)
(*   - the forger's only power is to manufacture a COMPETING claimed       *)
(*     quorum, which Q > 2f forbids (integrity is safe).                   *)
(*                                                                         *)
(* So the Q > 2f bound is needed for SAFETY (V3 MCByzantine / the sweep),  *)
(* not for liveness quality. Liveness quality is protected by the weaker   *)
(* structural fact that forgers cannot remove honest evidence. Stating     *)
(* this explicitly matters: it tells an operator that lowering the quorum  *)
(* costs correctness, never availability, so there is no tempting          *)
(* availability argument for weakening it.                                 *)
(*                                                                         *)
(* NON-VACUITY: NeverHonestQuorum is violated in both configurations, so   *)
(* the antecedent of these properties is genuinely reachable and the       *)
(* results are not vacuous.                                                *)
(***************************************************************************)

=============================================================================
