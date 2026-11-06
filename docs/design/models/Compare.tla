---- MODULE Compare ----
\* Flow A: one comparison. One crossing on the default route, two candidate alternatives,
\* each introducing one crossing. Every candidate avoids the default's crossing.
EXTENDS FiniteSets, Naturals

D == {"d1"}
Cands == {"k1", "k2"}
Intro == [k \in Cands |-> IF k = "k1" THEN {"i1"} ELSE {"i2"}]
Crossings == D \cup {"i1", "i2"}
Evidence == {"ruleFlag", "ruleUnflag", "jevFlag", "jevUnflag", "jevLow"}

VARIABLES phase, avoid, gate, evid, status, place, budget, searchComplete, outcome, shown, countShown, deadlineHit
\* deadlineHit records that the 10 seconds ran out; only the rules read it.
vars == <<phase, avoid, gate, evid, status, place, budget, searchComplete, outcome, shown, countShown, deadlineHit>>

Known(S) == Cardinality({c \in S : status[c] = "flagged"})
Unk(S) == Cardinality({c \in S : status[c] = "unknown"})
Worst(k) == Known(Intro[k]) + Unk(Intro[k])
Assessed(c) == status[c] \in {"flagged", "unflagged", "unknown"}

StatusFrom(e) ==
  CASE e = "ruleFlag"   -> "flagged"
    [] e = "ruleUnflag" -> "unflagged"
    [] e = "jevFlag"    -> IF gate THEN "flagged" ELSE "unknown"
    [] e = "jevUnflag"  -> IF gate THEN "unflagged" ELSE "unknown"
    [] e = "jevLow"     -> "unknown"

Init ==
  /\ phase = "routing"
  /\ avoid \in BOOLEAN
  /\ gate \in BOOLEAN
  /\ evid = [c \in Crossings |-> "none"]
  /\ status = [c \in Crossings |-> "pending"]
  /\ place = [k \in Cands |-> "none"]
  /\ budget = [k \in Cands |-> "none"]
  /\ searchComplete = FALSE
  /\ outcome = "none"
  /\ shown = "none"
  /\ countShown = FALSE
  /\ deadlineHit = FALSE

RouteFails ==
  /\ phase = "routing"
  /\ phase' = "routeFailed" /\ outcome' = "routeFailed"
  /\ UNCHANGED <<avoid, gate, evid, status, place, budget, searchComplete, shown, countShown, deadlineHit>>

RouteOk ==
  /\ phase = "routing" /\ phase' = "checking"
  /\ UNCHANGED <<avoid, gate, evid, status, place, budget, searchComplete, outcome, shown, countShown, deadlineHit>>

Relevant(c) ==
  \/ (phase = "checking" /\ c \in D)
  \/ (phase = "searching" /\ \E k \in Cands : c \in Intro[k] /\ place[k] = "ok")

Assess(c) ==
  /\ Relevant(c) /\ status[c] = "pending"
  /\ \E e \in Evidence :
       /\ evid' = [evid EXCEPT ![c] = e]
       /\ status' = [status EXCEPT ![c] = StatusFrom(e)]
  /\ UNCHANGED <<phase, avoid, gate, place, budget, searchComplete, outcome, shown, countShown, deadlineHit>>

CheckFails(c) ==
  /\ Relevant(c) /\ status[c] = "pending"
  /\ status' = [status EXCEPT ![c] = "failed"]
  /\ UNCHANGED <<phase, avoid, gate, evid, place, budget, searchComplete, outcome, shown, countShown, deadlineHit>>

DefaultChecked ==
  /\ phase = "checking" /\ \A c \in D : status[c] # "pending"
  /\ IF \E c \in D : status[c] = "failed"
       THEN phase' = "done" /\ outcome' = "checkFailed" /\ countShown' = FALSE /\ UNCHANGED searchComplete
     ELSE IF ~avoid
       THEN phase' = "done" /\ outcome' = "defaultOnly" /\ countShown' = TRUE /\ UNCHANGED searchComplete
     ELSE IF Known(D) = 0
       THEN phase' = "done" /\ outcome' = "noImprovement" /\ countShown' = TRUE /\ searchComplete' = TRUE
     ELSE phase' = "searching" /\ countShown' = TRUE /\ UNCHANGED <<outcome, searchComplete, deadlineHit>>
  /\ UNCHANGED <<avoid, gate, evid, status, place, budget, shown, deadlineHit>>

RouteCandidate(k) ==
  /\ phase = "searching" /\ place[k] = "none"
  /\ \E p \in {"ok", "wrong"}, b \in {"in", "over"} :
       place' = [place EXCEPT ![k] = p] /\ budget' = [budget EXCEPT ![k] = b]
  /\ UNCHANGED <<phase, avoid, gate, evid, status, searchComplete, outcome, shown, countShown, deadlineHit>>

CandDone(k) == place[k] = "wrong" \/ (place[k] = "ok" /\ \A c \in Intro[k] : status[c] # "pending")

DeadlineHits ==
  /\ phase = "searching" /\ ~(\A k \in Cands : CandDone(k))
  /\ phase' = "ranking" /\ searchComplete' = FALSE /\ deadlineHit' = TRUE
  /\ UNCHANGED <<avoid, gate, evid, status, place, budget, outcome, shown, countShown>>

SearchFinishes ==
  /\ phase = "searching" /\ \A k \in Cands : CandDone(k)
  /\ phase' = "ranking" /\ searchComplete' = TRUE
  /\ UNCHANGED <<avoid, gate, evid, status, place, budget, outcome, shown, countShown, deadlineHit>>

Improves(k) == place[k] = "ok" /\ (\A c \in Intro[k] : Assessed(c)) /\ Worst(k) < Known(D)

Rank ==
  /\ phase = "ranking" /\ phase' = "done"
  /\ LET E == {k \in Cands : Improves(k) /\ budget[k] = "in"} IN
       IF E # {}
         THEN /\ shown' = CHOOSE k \in E : \A j \in E : Worst(k) <= Worst(j)
              /\ outcome' = "improvement"
       ELSE IF \E k \in Cands : Improves(k)
         THEN outcome' = "overBudget" /\ UNCHANGED shown
       ELSE IF ~searchComplete
         THEN outcome' = "incomplete" /\ UNCHANGED shown
       ELSE outcome' = "noImprovement" /\ UNCHANGED shown
  /\ UNCHANGED <<avoid, gate, evid, status, place, budget, searchComplete, countShown, deadlineHit>>

Finished == phase \in {"done", "routeFailed"} /\ UNCHANGED vars

Next ==
  \/ RouteFails \/ RouteOk \/ DefaultChecked \/ DeadlineHits \/ SearchFinishes \/ Rank \/ Finished
  \/ \E c \in Crossings : Assess(c) \/ CheckFails(c)
  \/ \E k \in Cands : RouteCandidate(k)

Spec == Init /\ [][Next]_vars

\* Rule 1: "An unknown is never counted as unflagged."
R1_UnknownNeverUnflagged ==
  \A c \in Crossings : status[c] = "unflagged" =>
    (evid[c] = "ruleUnflag" \/ (evid[c] = "jevUnflag" /\ gate))

\* Rule 2: "A failed check never shows a flag count."
R2_FailedCheckNoCount == (\E c \in D : status[c] = "failed") => ~countShown

\* Rule 3: "An alternative is shown only if it has fewer flags than the default even when all its unknowns are flags."
R3_RealImprovement ==
  shown # "none" =>
    Cardinality({c \in Intro[shown] : status[c] \in {"flagged", "unknown"}})
      < Cardinality({c \in D : status[c] = "flagged"})

\* Rule 4: "A shown alternative never exceeds its time budget."
R4_WithinBudget == shown # "none" => budget[shown] = "in"

\* Rule 5: "Every crossing on a shown alternative has been assessed, including the ones it introduces."
R5_AllAssessed == shown # "none" => \A c \in Intro[shown] : Assessed(c)

\* Rule 6: "A shown alternative actually crosses where it was meant to, in the intended direction."
R6_CrossesWhereMeant == shown # "none" => place[shown] = "ok"

\* Rule 7: "An incomplete search is never reported as "no improvement"."
R7_IncompleteNotNoImprovement == outcome = "noImprovement" => ~deadlineHit

\* Rule 8: "A lead never changes a crossing's status."
R8_LeadNoStatus ==
  \A c \in Crossings :
    (evid[c] = "jevLow" \/ (evid[c] \in {"jevFlag", "jevUnflag"} /\ ~gate)) => status[c] = "unknown"

\* Rule 9: "Jev's answers change a status only after Jev has passed its gate."
R9_JevAfterGate ==
  \A c \in Crossings :
    (evid[c] \in {"jevFlag", "jevUnflag"} /\ status[c] \in {"flagged", "unflagged"}) => gate
====
