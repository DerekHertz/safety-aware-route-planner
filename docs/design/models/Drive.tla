---- MODULE Drive ----
\* Flow C: the app from choosing a route to the end of a drive.
VARIABLES screen, altAvailable, route, feed, guidance, markersShown, reportSent, tapped
\* tapped: the driver has tapped recompute and the new route has not arrived yet.
vars == <<screen, altAvailable, route, feed, guidance, markersShown, reportSent, tapped>>

Init ==
  /\ screen = "planning"
  /\ altAvailable \in BOOLEAN
  /\ route = "default"
  /\ feed = "none"
  /\ guidance = "none"
  /\ markersShown = TRUE
  /\ reportSent = FALSE
  /\ tapped = FALSE

ChooseAlternative ==
  /\ screen = "planning" /\ altAvailable /\ route = "default"
  /\ route' = "alternative"
  /\ UNCHANGED <<screen, altAvailable, feed, guidance, markersShown, reportSent, tapped>>

ChooseDefault ==
  /\ screen = "planning" /\ route = "alternative"
  /\ route' = "default"
  /\ UNCHANGED <<screen, altAvailable, feed, guidance, markersShown, reportSent, tapped>>

Start ==
  /\ screen = "planning"
  /\ \E f \in {"simulated", "real"} : feed' = f
  /\ screen' = "driving" /\ guidance' = "following"
  /\ UNCHANGED <<altAvailable, route, markersShown, reportSent, tapped>>

DriftOff ==
  /\ screen = "driving" /\ guidance = "following"
  /\ guidance' = "offRoute"
  /\ UNCHANGED <<screen, altAvailable, route, feed, markersShown, reportSent, tapped>>

BackOnRoute ==
  /\ screen = "driving" /\ guidance = "offRoute" /\ ~tapped
  /\ guidance' = "following"
  /\ UNCHANGED <<screen, altAvailable, route, feed, markersShown, reportSent, tapped>>

TapRecompute ==
  /\ screen = "driving" /\ guidance = "offRoute" /\ ~tapped
  /\ tapped' = TRUE
  /\ UNCHANGED <<screen, altAvailable, route, feed, guidance, markersShown, reportSent>>

RerouteArrives ==
  /\ screen = "driving" /\ tapped
  /\ route' = "rerouted" /\ markersShown' = FALSE /\ guidance' = "following" /\ tapped' = FALSE
  /\ UNCHANGED <<screen, altAvailable, feed, reportSent>>

Arrive ==
  /\ screen = "driving" /\ guidance = "following"
  /\ screen' = "arrived" /\ guidance' = "arrived"
  /\ UNCHANGED <<altAvailable, route, feed, markersShown, reportSent, tapped>>

SendReport ==
  /\ screen = "arrived" /\ feed = "real"
  /\ reportSent' = TRUE /\ screen' = "finished"
  /\ UNCHANGED <<altAvailable, route, feed, guidance, markersShown, tapped>>

SkipReport ==
  /\ screen = "arrived"
  /\ screen' = "finished"
  /\ UNCHANGED <<altAvailable, route, feed, guidance, markersShown, reportSent, tapped>>

Finished == screen = "finished" /\ UNCHANGED vars

Next ==
  \/ ChooseAlternative \/ ChooseDefault \/ Start \/ DriftOff \/ BackOnRoute
  \/ TapRecompute \/ RerouteArrives \/ Arrive \/ SendReport \/ SkipReport \/ Finished

Spec == Init /\ [][Next]_vars

\* Rule 11: "A rerouted route never shows crossing markers or claims."
R11_ReroutedNoMarkers == route = "rerouted" => ~markersShown

\* Rule 12: "A simulated drive never sends a drive report."
R12_NoSimulatedReport == reportSent => feed = "real"

\* Rule 13: "The app never changes the route without the driver's tap."
R13_RouteChangeIsTapped ==
  [][(screen = "driving" /\ route' # route) => tapped]_vars
====
