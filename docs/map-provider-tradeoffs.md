# Map provider trade-offs: MapLibre + OpenFreeMap vs Google Maps Platform

**Status:** evaluation only — no change adopted. Current stack stays.
**Date:** 2026-07-26

## TL;DR

Stay on MapLibre + OpenFreeMap for this prototype. Google's basemap is better
cartographically and its address autocomplete is materially better than
Nominatim's, but adopting **any** Google data API forces adopting the Google
basemap too (their "no use with a non-Google map" clause), it introduces an
API key + billing account, and it buys **zero routing improvement** — the
routing engine is our own C++ core over OSM, which is the whole point of the
project.

Critically: **switching providers would not have fixed the blank map.** That
was a container-sizing bug in our own code (canvas stuck at MapLibre's
400×300 fallback inside a 900×720 container, with no container-resize
handling). A Google map in a zero-height container is equally blank. The two
decisions are independent.

## Current stack

| Piece | Choice | Terms / cost |
|---|---|---|
| Renderer | MapLibre GL JS | BSD-3-Clause, no restrictions |
| Basemap tiles | OpenFreeMap (Liberty style) | Free, no API key, no quota, no billing account |
| Geocoding | Nominatim (proxied server-side) | Free; requires identifying User-Agent and ≤1 req/s |
| Routing | **Our own** C++17 engine over OSM | ODbL data; no third-party routing service |

Properties worth preserving: the front-end needs **no map-data credentials**
(an explicit goal in the project brief), there is no vendor account to
manage, and nothing rate-limits the map itself.

## Google Maps Platform

### Cost

Google replaced the long-standing $200/month universal credit on
**2025-03-01** with per-SKU free caps organized into Essentials / Pro /
Enterprise tiers:

- **Essentials: 10,000 free calls per SKU per month** (Dynamic Maps, Static
  Maps, Geocoding). Pro is 5,000/SKU; Enterprise 1,000/SKU.
- Beyond the free cap, **Dynamic Maps (Maps JavaScript API) is $7 per 1,000**
  map loads in the 10,001–100,000 band.
- Alternatively an Essentials **subscription at $275/month** for 100,000
  combined calls.

For a prototype this is effectively free — 10k map loads/month is generous.
The cost concern is not the sticker price but that it requires a **billing
account on file** and per-SKU quota monitoring, and browser API keys are
inherently client-visible (they must be HTTP-referrer restricted).

### The decisive licensing constraint

Google's Service Specific Terms carry a **"No use with a non-Google map"**
restriction covering the Routes API, Directions API, Distance Matrix API,
**Geocoding API**, and Places. Google Maps Content from those services must
not be displayed on a non-Google map.

Two consequences that matter here:

1. We **cannot** swap Nominatim for Google Geocoding/Places while keeping the
   MapLibre basemap. That combination is prohibited.
2. Adoption is therefore **all-or-nothing**, not incremental: taking any
   Google data API drags the basemap along with it.

### What *is* permitted

Overlaying **our own** data — the OSM-derived route polylines, tier-colored
segments, and unsafe-maneuver markers — on a Google basemap is fine. Custom
overlays are core Maps JavaScript API functionality. So "Google basemap +
our own router" is a legitimate configuration; it is the *reverse* direction
(Google content on our map) that is barred.

### Automotive / navigation caveats

The brief frames this as an in-car planner, which brushes against terms that
deserve attention if it ever ships:

- Google restricts applications embedded in **in-dashboard automotive
  infotainment systems** that let end users request driving directions from
  the Directions API.
- Applications providing **real-time navigation or used during real-time
  driving** must comply with Google's Safety Requirements; the **Navigation
  SDK** is the product intended for turn-by-turn experiences, not the
  Directions API.

Turn-by-turn is an explicit non-goal of this prototype, so this is currently
moot — but it constrains the product direction the brief gestures at.

### Interaction with OSM/ODbL

Our graph is OSM-derived and therefore ODbL-licensed. A basemap-only adoption
keeps the two cleanly separated: Google pixels underneath, our own geometry
on top. Pulling Google content *into* the routing pipeline (e.g. using their
geocoding results or traffic data as engine inputs) would both breach
Google's terms and risk contaminating an ODbL-derived dataset. That boundary
must stay bright.

## What switching would and would not buy

**Would improve**
- Basemap cartography and label quality — Google's is better than Liberty.
- **Address entry UX.** This is the strongest argument. Nominatim's 1 req/s
  policy forces the 600 ms debounce + server-side throttle we currently run;
  Places Autocomplete is built for keystroke-rate querying.
- Live **traffic visualization** (display only — using it as a routing input
  is out of bounds, and live traffic is an explicit non-goal).

**Would not improve**
- **Routing quality — at all.** Safety-aware routing is our own engine over
  our own OSM-derived cost model. That is the entire contribution of this
  project and Google offers nothing for it (their routing is a black box we
  could not add turn-level safety penalties to).
- The blank-map bug (a sizing defect in our component).

**Would cost**
- An API key + billing account, key restriction management, per-SKU quota
  monitoring.
- The brief's "front-end never needs its own map-data credentials" property.
- Vendor lock-in on the display layer, and the all-or-nothing coupling above.

## Middle option worth keeping in view

The real weakness of the current stack is not MapLibre — it is
**OpenFreeMap's** hosting: donation-funded, effectively one maintainer, **no
SLA**. That is a genuine production risk, but it is a *tile-hosting* problem
with tile-hosting answers that keep MapLibre and avoid Google's licensing
entanglement entirely:

- **MapTiler** or **Stadia Maps** — commercial vector tiles, SLA, MapLibre-native.
- **Self-hosted Protomaps** — a single `.pmtiles` file on object storage; no
  per-call cost, full control, and it fits the existing offline-pack philosophy
  of this project rather well.

Similarly, if geocoding UX becomes the bottleneck, **Photon**, **Pelias**, or a
**self-hosted Nominatim** remove the 1 req/s ceiling without triggering the
non-Google-map clause.

## Recommendation

Keep MapLibre + OpenFreeMap. Revisit only when a specific constraint binds:

- *Address entry is the top complaint* → try Photon/Pelias/self-hosted
  Nominatim first (cheap, no coupling). Google Places only if that fails, and
  accept that it takes the basemap with it.
- *Production reliability / SLA required* → move tile hosting to
  MapTiler/Stadia/self-hosted Protomaps. This addresses the actual risk
  without adopting Google.
- *Product pivots to true turn-by-turn in-dash navigation* → the Google terms
  above become a first-class licensing question, and the Navigation SDK (not
  the Directions API) is the relevant product.

## Sources

- [Google Maps Platform pricing](https://mapsplatform.google.com/pricing/)
- [Changes to Google Maps Platform credits and volume discounts (billing FAQ)](https://developers.google.com/maps/billing-and-pricing/faq)
- [Up to 10,000 monthly free calls per product](https://mapsplatform.google.com/resources/blog/start-building-today-with-up-to-10-000-monthly-free-calls-per-product/)
- [Google Maps Platform Service Specific Terms](https://cloud.google.com/maps-platform/terms/maps-service-terms)
- [Google Maps Platform Terms of Service](https://cloud.google.com/maps-platform/terms)
- [Maps JavaScript API usage and billing](https://developers.google.com/maps/documentation/javascript/usage-and-billing)
- [Navigation SDK overview](https://mapsplatform.google.com/maps-products/navigation-sdk/)
- [Google Maps API pricing breakdown 2026 (Woosmap)](https://www.woosmap.com/blog/google-maps-api-pricing-breakdown)
