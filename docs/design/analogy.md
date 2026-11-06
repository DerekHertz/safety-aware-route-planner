# Analogy: a hiking guide planning river crossings

The one analogy this design uses, in every stage's picture and in future grilling sessions. It
stays close to the subject: a route with crossings, some you wait at and some that are
controlled. Pictures are generated from the tables below, so edit the tables, not the pictures.

## Page

| Field | Value |
|---|---|
| Page title | River Crossings Field Guide |
| Subject | Gap-crossing route comparison |
| Headline | The guide, the river and the bridge upstream |
| Scene caption | Each component is a station on the trail. The usual path fords the river; the guide looks a few bends upstream for a bridge. |
| Chart note | This is the reduced model drawn for the chart; the full one is what TLC checked. |
| Footer | Trail data in the real system: © OpenStreetMap contributors (ODbL). Generated from docs/design. |

## Components

| Design | Analogy | Where it breaks |
|---|---|---|
| Route source | The trail map's suggested path | A trail map doesn't give a time estimate that reacts to the hour. |
| Movement interpretation | Marking every place the path meets a river | A river with a midstream island is one crossing in two channels, like a divided street. |
| Assessment | Scouting each crossing: ford or bridge | — |
| Local search | Looking a few bends up and down the river for another crossing | Streets have one-way rules and turn bans; riverbanks don't. |
| Comparison | Deciding if the walk to the bridge is worth it | — |
| iOS app: planning | The marked map you hand the hiker | — |
| iOS app: follower | Walking the trail with the guide calling out turns | — |

## Core types

| Design | Analogy | Where it breaks |
|---|---|---|
| Route | A path on the map | — |
| Step | A turn the guide calls out | — |
| Crossing | A place the path crosses a river | — |
| Assessment | The guide's verdict on a crossing | — |
| Evidence | A note in the trail log, dated | — |
| Comparison | The guide's recommendation | — |
| DriveReport | The hiker's note after the trip | — |

## Statuses

| Design | Analogy | Where it breaks |
|---|---|---|
| flagged | A ford you wade when the current allows | A river doesn't change on a schedule; traffic does. |
| unflagged | A bridge, or a stream too small to matter | — |
| unknown | A crossing nobody has scouted | — |
| check_failed | The trail log couldn't be read | — |
| lead | A scout's guess, not yet in the log | — |

## Seams

| Seam | Live | Fixture | Analogy |
|---|---|---|---|
| RouteSource | OSRM | Recorded OSRM | Asking for the map vs. reading last year's printed copy |
| RecordSource | Overpass | Recorded Overpass | Reading the trail log at the ranger station vs. a photocopy |
| Jev | TypeSafe API | Recorded answers | Sending a scout vs. reading the scout's old report |

## Chart states

| Design | Analogy |
|---|---|
| Getting the route | Unfolding the trail map |
| Route couldn't be obtained | No map for this trail |
| Checking the default's crossings | Scouting the crossings on the usual path |
| Crossings couldn't be checked | The trail log was unreadable; the path is shown without verdicts |
| Default route only | The hiker only wanted the usual path |
| No improvement found | Every other crossing nearby is no better |
| Searching nearby crossings | Walking the bank to look for a bridge |
| Comparing (deadline hit) | Turning back before dark, with what was seen |
| Search incomplete | Out of daylight before finding anything |
| Comparing (search finished) | Every nearby crossing has been seen |
| Alternative shown | A bridge upstream, worth the walk |
| No improvement within budget | A bridge exists, but it's too far to walk |
