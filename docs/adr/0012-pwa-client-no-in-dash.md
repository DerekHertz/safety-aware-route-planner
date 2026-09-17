---
Status: accepted
Date: 2026-09-17
---

# The client is an installable PWA with Google Sign-In; in-dash integration is out of scope

The reference client stays a web app, distributed as an **installable PWA** on both
platforms, with **Google Sign-In** for the accounts the commute planner needs (ADR-0011).
There is no native app and no Apple Developer Program membership.

## In-dash is not reachable, and planning should not assume it

The brief frames this as an in-car planner, so this needs saying plainly: **CarPlay and
Android Auto are both out of reach.** CarPlay navigation requires a separate Apple
entitlement requested on top of the $99/year Developer Program. Android Auto's navigation
category requires a native implementation of the car app library plus Google approval.
Neither is reachable from a PWA at any price this project has agreed to pay.

"In the car" therefore means **a phone in a mount, running fullscreen**. That is a
legitimate and common configuration, and it is what the UI should be designed for. It is
recorded here so a future reader does not build a roadmap around infotainment.

## What the platform does and does not give us

**Voice works.** `web/lib/useSpeech.ts` uses the Web Speech API and already handles the
part that silently breaks on iOS: it primes the user-gesture requirement by speaking an
empty utterance synchronously inside the click handler that enables voice. Doing that
later (in an effect) means iOS stops counting it as gesture-triggered and every subsequent
`speak()` fails with no error.

**Screen Wake Lock is required, not optional.** For mounted navigation the screen must not
sleep: on iOS a backgrounded PWA suspends, which kills `watchPosition` and speech
together. `navigator.wakeLock.request("screen")` plus reacquisition on `visibilitychange`
(the commonly missed half) is a hard prerequisite for promoting live nav off its flag
(ADR-0008).

**iOS PWAs cannot do background geolocation.** A PWA cannot track a commute while
backgrounded, and no amount of effort changes that. The commute planner sidesteps it by
construction: detection and replanning happen **server-side**, and the user is told
*before they leave*, which needs no background GPS at all (ADR-0011). This is a real point
in favour of that design, not merely a constraint it survives.

## Why Google Sign-In

It is free, it works in a browser and a PWA with no platform dependency, and it carries no
Apple relationship. The initial scope is **identity only**; calendar reading is a separate,
later, larger consent ask (ADR-0011).

## Consequences

- If live turn-by-turn ever becomes the primary surface rather than a feature, background
  location makes a native wrapper unavoidable on at least one platform, and this decision
  should be reopened. Google Play registration is a one-time $25; Apple is $99/year plus
  an entitlement request.
- The front-end keeps its "no map-data credentials in the client" property. Google
  Sign-In is an auth credential, not a map-data one, and the basemap stays keyless.
