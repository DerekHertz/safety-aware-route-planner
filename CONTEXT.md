# Gap-crossing route comparison

A tool that finds the places on a planned drive where the driver must wait for a gap in a main street's traffic, and looks for a nearby alternative that crosses it somewhere traffic is stopped for them. It makes claims about driver comfort and observable traffic controls, never about route safety.

## Language

**Movement**:
A route's passage through one intersection, from an approach to a departure. Straight-through passages are movements even when they have no turn instruction.
_Avoid_: Maneuver, turn (a turn is one kind of movement)

**Main street**:
A street established, by positive evidence, as carrying through traffic. A two-lane street can qualify. If its role cannot be established, it is neither main nor local: the status is unknown.
_Avoid_: Arterial, major road

**Crossing**:
One passage of a route straight across, or left onto, a main street, however many carriageways or intersections it spans. Right turns onto a main street, and left turns off one, are movements but never crossings.
_Avoid_: Intersection (that is where a crossing happens, not the crossing)

**Gap crossing**:
A crossing where the driver must choose a gap because the main street's relevant traffic is not stopped for them. A stop sign facing only the driver does not remove the gap.
_Avoid_: Unprotected crossing

**Flag**:
The status of a crossing that is a gap crossing. A crossing is flagged if any carriageway of the main street requires choosing a gap, and counts as one flag.
_Avoid_: Warning, risk, unsafe

**Unflagged**:
The status of a crossing whose relevant traffic is controlled for the movement, by an all-way stop or by a signal whose circular green stops the perpendicular traffic, or whose cross street is established as local. A signal does not by itself claim a protected left-turn phase.
_Avoid_: Safe

**Unknown**:
The status of a crossing whose evidence is missing, ambiguous or in conflict. An unknown is never counted as unflagged. Conflicting evidence stays unknown until the conflict is resolved.
_Avoid_: Uncertain, unverified

**Check failed**:
The status of a crossing, or of a whole route, when the assessment itself could not run. It is distinct from unknown, and a route with a failed check shows no flag count.
_Avoid_: Error (in user-facing and domain language)

## Routes

**Default route**:
The route the driver would take without this tool, with its ETA and the flags and unknowns on it.
_Avoid_: Original route, baseline

**Example route**:
A default route supplied by us rather than chosen by the routing provider, always labeled as such.
_Avoid_: Sample route, fake route

**Position feed**:
The source of the driver's position that navigation follows: simulated or real GPS.
_Avoid_: Location provider

**Alternative**:
A complete route to the same destination that crosses the same main street elsewhere. At most one alternative is shown beside the default route.
_Avoid_: Detour (the extra path inside an alternative), reroute

**Improvement**:
An alternative that has fewer flags than the default route even if every unknown on it turned out to be a flag. Without one, the result is "no improvement found", which differs from a search that did not finish.
_Avoid_: Better route

**Avoidance preference**:
The driver's choice of how much added time to accept in order to avoid flags. It sets the added-time budget.
_Avoid_: Safety level, strictness

**Added-time budget**:
The most extra ETA an alternative may have over the default route, as a share of the default ETA with an absolute cap.
_Avoid_: Time penalty, detour limit

## Evidence

**Evidence**:
A dated record or observation bearing on one movement, such as a map tag, a public record or a street image, kept with its source and date.
_Avoid_: Data, signal

**Lead**:
Information that suggests a control but is not yet evidence, such as a map icon or a model's answer before it has passed its measured gate. A lead never changes a crossing's status from unknown.
_Avoid_: Hint, tip

**Drive report**:
A driver's answer, after a real drive, to whether a crossing worked the way the app described it. The only source of ground truth about the street.
_Avoid_: Label, feedback, rating

**Assessment**:
The conclusion for one crossing: flagged, unflagged or unknown, with its evidence and a short explanation. A failed assessment is check failed.
_Avoid_: Classification, score

## Relationships

- A **Crossing** is made of one or more **Movements**, and has exactly one status: flagged, unflagged, unknown or check failed.
- A route's flag count counts flagged crossings only. Its unknown count is reported separately and is never folded into it.
- An **Alternative** is offered only if it is an **Improvement** within the **Added-time budget** set by the **Avoidance preference**.
