// Scenario tests for the trip-trace recorder (ADR-0017, ADR-0018), driven at
// its boundaries: fixes and navigation events in, HTTP requests out, over an
// in-memory storage and a fake commute service that validates like the real
// one (commute/schemas.py). A test that uploads something the real server
// would refuse fails on `server.invalid`, so every scenario also checks the
// ordering constraints.

import { describe, expect, it } from "vitest";
import { REROUTE, ROUTE, truncate } from "./__fixtures__/routes";
import { densifyRoute } from "./routeProgress";
import { TRIM_M, distanceM } from "./tracePrivacy";
import { TraceStorage, createMemoryTraceStorage } from "./traceStorage";
import { createTripRecorder } from "./tripRecorder";
import {
  FollowedArtifact,
  LatLon,
  RouteAlternative,
  TraceChunk,
  TraceFix,
  TripEnd,
} from "./types";

const BASE = "https://commute.test";
const TOKEN = "tester-token-one";
const T0 = Date.UTC(2026, 8, 24, 15, 15);
const EPOCH_MS_MIN = 946_684_800_000;
const EPOCH_MS_MAX = 4_102_444_800_000;

// --- a fake commute service --------------------------------------------------

interface Req {
  method: string;
  path: string;
  auth: string | null;
  body: string | null;
}
type Override = (req: Req) => Response | "network" | undefined;

const json = (status: number, body: unknown, headers: HeadersInit = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

const isEpochMs = (x: unknown) =>
  Number.isInteger(x) &&
  (x as number) >= EPOCH_MS_MIN &&
  (x as number) <= EPOCH_MS_MAX;

/** commute/schemas.py's TraceChunk rules. Returns the first problem. */
function chunkProblem(c: TraceChunk): string | null {
  const fixes = c.fixes ?? [];
  const arts = c.artifacts ?? [];
  if (fixes.length > 600) return "over 600 fixes";
  if (arts.length > 16) return "over 16 artifacts";
  if (!fixes.length && !arts.length) return "empty chunk";
  for (let i = 0; i < fixes.length; i++) {
    const f = fixes[i];
    if (!isEpochMs(f.t)) return `fixes[${i}].t not epoch-ms`;
    if (i && f.t <= fixes[i - 1].t) return `fixes[${i}].t not increasing`;
    if (!(Math.abs(f.lat) <= 90 && Math.abs(f.lon) <= 180)) return "range";
    if (!(f.accuracy_m >= 0)) return "accuracy";
    if (f.speed_mps !== null && !(f.speed_mps >= 0)) return "speed";
    if (f.heading_deg !== null && !(f.heading_deg >= 0 && f.heading_deg <= 360))
      return "heading";
  }
  for (let i = 0; i < arts.length; i++) {
    if (!isEpochMs(arts[i].effective_at)) return "effective_at not epoch-ms";
    if (i && arts[i].effective_at <= arts[i - 1].effective_at)
      return "effective_at not increasing";
  }
  return null;
}

function endProblem(e: TripEnd): string | null {
  if (!isEpochMs(e.ended_at)) return "ended_at not epoch-ms";
  if (typeof e.arrived !== "boolean") return "arrived";
  const p = e.prediction;
  if (p === null) return null;
  if (!isEpochMs(p.effective_at)) return "effective_at not epoch-ms";
  if (!(p.eta_s >= 0 && p.eta_s <= 172_800)) return "eta_s";
  if (p.effective_at > e.ended_at) return "prediction after ended_at";
  return null;
}

function fakeCommute() {
  const tokens = new Set([TOKEN]);
  const chunks = new Map<string, string>(); // "trip/seq" -> body text
  const ends = new Map<string, string>(); // trip -> body text
  const requests: Req[] = [];
  const invalid: string[] = [];
  const state = { override: null as Override | null };

  function store(
    map: Map<string, string>,
    key: string,
    body: string,
    problem: string | null,
    receipt: object,
  ): Response {
    if (new TextEncoder().encode(body).length > 1_048_576) {
      return json(413, { detail: "too large" });
    }
    if (problem) {
      invalid.push(problem);
      return json(422, { detail: problem });
    }
    const held = map.get(key);
    if (held === undefined) {
      map.set(key, body);
      return json(201, { ...receipt, created: true });
    }
    // The server compares canonical JSON; the recorder resends its bytes.
    if (held === body) return json(200, { ...receipt, created: false });
    return json(409, { detail: "different content" });
  }

  async function fetchImpl(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    const url = new URL(String(input));
    const req: Req = {
      method: init?.method ?? "GET",
      path: url.pathname,
      auth: new Headers(init?.headers).get("Authorization"),
      body: typeof init?.body === "string" ? init.body : null,
    };
    requests.push(req);
    expect(url.origin).toBe(BASE);
    const o = state.override?.(req);
    if (o === "network") throw new TypeError("Failed to fetch");
    if (o) return o;
    const token = req.auth?.replace(/^Bearer /, "");
    if (!token || !tokens.has(token)) {
      return json(401, { detail: "unknown or revoked tester token" });
    }
    if (req.method === "GET" && req.path === "/v1/me") {
      return json(200, { label: "derek-test" });
    }
    let m = req.path.match(/^\/v1\/trips\/([0-9a-f-]{36})\/chunks\/(\d+)$/);
    if (m && req.method === "PUT") {
      const body = req.body!;
      return store(
        chunks,
        `${m[1]}/${m[2]}`,
        body,
        chunkProblem(JSON.parse(body)),
        {
          trip_id: m[1],
          seq: Number(m[2]),
        },
      );
    }
    m = req.path.match(/^\/v1\/trips\/([0-9a-f-]{36})\/end$/);
    if (m && req.method === "POST") {
      const body = req.body!;
      return store(ends, m[1], body, endProblem(JSON.parse(body)), {
        trip_id: m[1],
      });
    }
    return json(404, { detail: "not found" });
  }

  const chunkBodies = () =>
    [...chunks.values()].map((b) => JSON.parse(b) as TraceChunk);
  const uploadedFixes = (): TraceFix[] => chunkBodies().flatMap((c) => c.fixes);
  const uploadedArtifacts = (): FollowedArtifact[] =>
    chunkBodies()
      .flatMap((c) => c.artifacts ?? [])
      .sort((a, b) => a.effective_at - b.effective_at);

  return {
    fetch: fetchImpl as typeof fetch,
    tokens,
    chunks,
    ends,
    requests,
    invalid,
    state,
    chunkBodies,
    uploadedFixes,
    uploadedArtifacts,
    endBodies: () => [...ends.values()].map((b) => JSON.parse(b) as TripEnd),
    /** Every place any stored chunk puts on the server. */
    uploadedPlaces(): LatLon[] {
      return [
        ...uploadedFixes(),
        ...uploadedArtifacts().flatMap(({ artifact: r }) => [
          ...r.geometry.coordinates.map(([lon, lat]) => ({ lon, lat })),
          ...r.segments.flatMap((s) =>
            s.geometry.coordinates.map(([lon, lat]) => ({ lon, lat })),
          ),
          ...r.maneuvers,
          ...r.unsafe_points,
        ]),
      ];
    },
  };
}
type FakeCommute = ReturnType<typeof fakeCommute>;

// --- a harness: recorder + clock + timers ------------------------------------

let tripIds = 0;

function harness(
  opts: { storage?: TraceStorage; server?: FakeCommute; baseUrl?: string } = {},
) {
  const storage = opts.storage ?? createMemoryTraceStorage();
  const server = opts.server ?? fakeCommute();
  const clock = { now: T0 };
  const timers: { fn: () => void; ms: number; cancelled: boolean }[] = [];
  const rec = createTripRecorder({
    storage,
    baseUrl: opts.baseUrl ?? BASE,
    fetch: server.fetch,
    now: () => clock.now,
    newTripId: () =>
      `00000000-0000-4000-8000-${String(++tripIds).padStart(12, "0")}`,
    schedule: (fn, ms) => {
      const t = { fn, ms, cancelled: false };
      timers.push(t);
      return () => {
        t.cancelled = true;
      };
    },
    random: () => 1, // no jitter, so backoff delays are exact
  });
  return { rec, storage, server, clock, timers };
}
type Harness = ReturnType<typeof harness>;

async function optedIn(opts: Parameters<typeof harness>[0] = {}) {
  const h = harness(opts);
  await h.rec.init();
  expect(await h.rec.optIn(TOKEN)).toEqual({ ok: true, label: "derek-test" });
  return h;
}

/** One fix a second along `points`: a car at about 10 m/s on a 10 m track. */
async function drive(
  h: Harness,
  points: LatLon[],
  fix: Partial<TraceFix> = {},
): Promise<void> {
  for (const p of points) {
    h.clock.now += 1000;
    await h.rec.addFix({
      t: h.clock.now,
      lat: p.lat,
      lon: p.lon,
      speed_mps: 10,
      accuracy_m: 5,
      heading_deg: 90,
      ...fix,
    });
  }
}

const track = (r: RouteAlternative) => densifyRoute(r.geometry.coordinates, 10);
const destinationOf = (r: RouteAlternative): LatLon => {
  const [lon, lat] = r.geometry.coordinates.at(-1)!;
  return { lon, lat };
};

function expectNothingNear(server: FakeCommute, point: LatLon) {
  for (const p of server.uploadedPlaces()) {
    expect(distanceM(p, point)).toBeGreaterThanOrEqual(TRIM_M);
  }
}

/** The trailing hold-back is about fixes: where the car actually stopped.
 *  A PLANNED route running past a point where a trip was cut short says
 *  nothing about the stop; its own ends are clipped separately. */
function expectNoFixNear(server: FakeCommute, point: LatLon) {
  for (const p of server.uploadedFixes()) {
    expect(distanceM(p, point)).toBeGreaterThanOrEqual(TRIM_M);
  }
}

// --- scenarios ----------------------------------------------------------------

describe("a recorded trip", () => {
  it("uploads nothing within 300 m of either end, fixes or artifact", async () => {
    const h = await optedIn();
    const path = track(ROUTE);
    const origin = path[0];
    const dest = path.at(-1)!;

    await h.rec.startTrip(ROUTE, destinationOf(ROUTE));
    await drive(h, path);
    await h.rec.endTrip(true);
    await h.rec.settled();

    expect(h.server.invalid).toEqual([]);
    expectNothingNear(h.server, origin);
    expectNothingNear(h.server, dest);

    // Not over-trimmed: every fix well clear of both ends went up, bar the
    // few a winding road holds back behind a nearer one.
    const clear = path.filter(
      (p) =>
        distanceM(p, origin) > TRIM_M + 20 && distanceM(p, dest) > TRIM_M + 20,
    );
    const fixes = h.server.uploadedFixes();
    expect(fixes.length).toBeGreaterThan(0.9 * clear.length);

    // Sealed about every 2 minutes, seqs from 0 with no gaps.
    const seqs = [...h.server.chunks.keys()]
      .map((k) => Number(k.split("/")[1]))
      .sort((a, b) => a - b);
    expect(seqs).toEqual(seqs.map((_, i) => i));
    const fixChunks = h.server.chunkBodies().filter((c) => c.fixes.length);
    expect(fixChunks.length).toBeGreaterThanOrEqual(3);
    for (const c of fixChunks) {
      expect(c.fixes.at(-1)!.t - c.fixes[0].t).toBeLessThanOrEqual(125_000);
    }

    // The artifact went up once, clipped the same way.
    const arts = h.server.uploadedArtifacts();
    expect(arts).toHaveLength(1);
    expect(arts[0].effective_at).toBe(T0);
    expect(arts[0].artifact.distance_m).toBeLessThan(
      ROUTE.distance_m - 2 * TRIM_M,
    );

    // Predicted versus actual, from the artifact followed at the end.
    expect(h.server.endBodies()).toEqual([
      {
        ended_at: h.clock.now,
        arrived: true,
        prediction: {
          effective_at: T0,
          eta_s: ROUTE.eta_s,
          level: "fast",
          profile_version: ROUTE.preference.traffic_basis.profile_version,
        },
      },
    ]);
    // Nothing left on the phone: no anchors, no queue.
    expect(await h.storage.load()).toMatchObject({ trips: [], outbox: [] });
  });

  it("a trip shorter than 600 m uploads nothing, but still sends end", async () => {
    const h = await optedIn();
    const short = truncate(ROUTE, 550);
    await h.rec.startTrip(short, destinationOf(short));
    await drive(h, track(short));
    await h.rec.endTrip(true);
    await h.rec.settled();

    expect(h.server.chunks.size).toBe(0);
    expect(h.server.endBodies()).toEqual([
      {
        ended_at: h.clock.now,
        arrived: true,
        prediction: expect.objectContaining({ eta_s: short.eta_s }),
      },
    ]);
    expect(await h.storage.load()).toMatchObject({ trips: [], outbox: [] });
  });

  it("a long route cancelled inside its first 600 m uploads no route either", async () => {
    const h = await optedIn();
    await h.rec.startTrip(ROUTE, destinationOf(ROUTE));
    await drive(h, track(truncate(ROUTE, 550)));
    await h.rec.endTrip(false);
    await h.rec.settled();

    expect(h.server.chunks.size).toBe(0);
    expect(h.server.endBodies()).toEqual([
      expect.objectContaining({ arrived: false }),
    ]);
  });

  it("GPS jitter at the origin, even a wild jump, never uploads the origin", async () => {
    const h = await optedIn();
    const path = track(ROUTE);
    const origin = path[0];
    await h.rec.startTrip(ROUTE, destinationOf(ROUTE));
    // A minute parked: fixes scatter up to ~40 m, poor accuracy...
    const scatter = Array.from({ length: 60 }, (_, i) => ({
      lat: origin.lat + 0.00035 * Math.sin(i * 1.7),
      lon: origin.lon + 0.00045 * Math.cos(i * 2.3),
    }));
    // ...and one multipath jump 350 m north, in a single second.
    scatter[30] = { lat: origin.lat + 0.00315, lon: origin.lon };
    await drive(h, scatter, { accuracy_m: 30, speed_mps: 0 });
    await drive(h, path);
    await h.rec.endTrip(true);
    await h.rec.settled();

    expect(h.server.invalid).toEqual([]);
    expectNothingNear(h.server, origin);
    expect(h.server.uploadedFixes().length).toBeGreaterThan(100);
  });

  it("a reroute uploads the replacement clipped at both ends, and the end predicts from it", async () => {
    const h = await optedIn();
    const mid = ROUTE.geometry.coordinates.length >> 1;
    const before = densifyRoute(
      ROUTE.geometry.coordinates.slice(0, mid + 1),
      10,
    );
    // From the old route's midpoint across to where the reroute starts.
    const after = densifyRoute(
      [ROUTE.geometry.coordinates[mid], ...REROUTE.geometry.coordinates],
      10,
    ).slice(1);

    await h.rec.startTrip(ROUTE, destinationOf(ROUTE));
    await drive(h, before);
    const reroutedAt = h.clock.now + 400;
    h.clock.now = reroutedAt;
    await h.rec.reroute(REROUTE);
    await drive(h, after);
    await h.rec.endTrip(true);
    await h.rec.settled();

    expect(h.server.invalid).toEqual([]);
    const arts = h.server.uploadedArtifacts();
    expect(arts.map((a) => a.effective_at)).toEqual([T0, reroutedAt]);
    const clipped = arts[1].artifact;
    expect(clipped.distance_m).toBeLessThan(REROUTE.distance_m - 2 * TRIM_M);
    const [lon, lat] = REROUTE.geometry.coordinates[0];
    for (const [cLon, cLat] of clipped.geometry.coordinates) {
      expect(
        distanceM({ lon: cLon, lat: cLat }, { lon, lat }),
      ).toBeGreaterThanOrEqual(TRIM_M);
    }
    expectNothingNear(h.server, before[0]);
    expectNothingNear(h.server, after.at(-1)!);
    expect(h.server.endBodies()[0].prediction).toEqual({
      effective_at: reroutedAt,
      eta_s: REROUTE.eta_s,
      level: "fast",
      profile_version: REROUTE.preference.traffic_basis.profile_version,
    });
  });
});

describe("killed mid-trip and relaunched", () => {
  it("replays the sealed chunks byte-identical and ends the orphan at its last fix", async () => {
    const storage = createMemoryTraceStorage();
    const server = fakeCommute();
    const first = await optedIn({ storage, server });
    server.state.override = () => json(503, { detail: "down" });

    const path = track(ROUTE);
    const driven = path.slice(0, Math.floor(path.length * 0.7));
    await first.rec.startTrip(ROUTE, destinationOf(ROUTE));
    await drive(first, driven);
    await first.rec.settled();
    const lastFixT = first.clock.now;
    const sealed = (await storage.load()).outbox;
    expect(sealed.length).toBeGreaterThanOrEqual(2);
    // The app is killed here: no endTrip, no flush. An hour later:
    server.state.override = null;
    const second = harness({ storage, server });
    second.clock.now = lastFixT + 3_600_000;
    await second.rec.init();
    await second.rec.settled();

    expect(server.invalid).toEqual([]);
    // The first was tried before the kill and replayed after it; every one
    // went up exactly as it was sealed.
    const tries = (path: string) =>
      server.requests.filter((r) => r.path === path);
    expect(tries(sealed[0].path).length).toBe(2);
    for (const item of sealed) {
      expect(tries(item.path).length).toBeGreaterThanOrEqual(1);
      for (const r of tries(item.path)) expect(r.body).toBe(item.body);
      const [trip, seq] = item.key.split("/");
      expect(server.chunks.get(`${trip}/${Number(seq)}`)).toBe(item.body);
    }
    expect(server.endBodies()).toEqual([
      {
        ended_at: lastFixT,
        arrived: false,
        prediction: expect.objectContaining({ effective_at: T0 }),
      },
    ]);
    // The tail behind the last fix stayed on the phone, and then went away.
    expectNoFixNear(server, driven.at(-1)!);
    expectNothingNear(server, driven[0]);
    expect(await storage.load()).toMatchObject({ trips: [], outbox: [] });
  });

  it("a trip killed before its first seal still uploads its clear middle", async () => {
    const storage = createMemoryTraceStorage();
    const server = fakeCommute();
    const first = await optedIn({ storage, server });
    const driven = track(ROUTE).slice(0, 100); // 100 s: under one seal period
    await first.rec.startTrip(ROUTE, destinationOf(ROUTE));
    await drive(first, driven);
    await first.rec.settled();
    expect(server.chunks.size).toBe(0);

    const second = harness({ storage, server });
    await second.rec.init();
    await second.rec.settled();

    expect(server.invalid).toEqual([]);
    expect(server.uploadedFixes().length).toBeGreaterThan(0);
    expect(server.uploadedArtifacts()).toHaveLength(1);
    expectNothingNear(server, driven[0]);
    expectNoFixNear(server, driven.at(-1)!);
    expect(server.endBodies()).toEqual([
      expect.objectContaining({ arrived: false }),
    ]);
  });
});

describe("server answers", () => {
  it("401 stops recording and holds the queue until a new token", async () => {
    const h = await optedIn();
    const path = track(ROUTE);
    await h.rec.startTrip(ROUTE, destinationOf(ROUTE));
    h.server.tokens.clear(); // revoked by the owner
    await drive(h, path.slice(0, 200));
    await h.rec.settled();
    expect(h.rec.getStatus()).toMatchObject({
      rejected: true,
      recording: false,
    });

    const asked = h.server.requests.length;
    const tripAt401 = (await h.storage.load()).trips[0];
    await drive(h, path.slice(200, 260));
    // "Stop recording": the fixes after the 401 are not kept.
    expect((await h.storage.load()).trips[0]).toEqual(tripAt401);
    await h.rec.endTrip(true);
    await h.rec.settled();
    expect(h.server.requests.length).toBe(asked); // nothing more sent
    const stored = await h.storage.load();
    expect(stored.trips).toEqual([]);
    expect(stored.outbox.length).toBeGreaterThan(0);

    h.server.tokens.add("tester-token-two");
    expect(await h.rec.optIn("tester-token-two")).toMatchObject({ ok: true });
    await h.rec.settled();
    expect(h.rec.getStatus()).toMatchObject({ rejected: false, queued: 0 });
    expect(h.server.endBodies()).toHaveLength(1);
    expect(h.server.invalid).toEqual([]);
  });

  it("409 and 422 drop that item for good, and the rest still go", async () => {
    const h = await optedIn();
    h.server.state.override = (req) =>
      req.path.endsWith("/chunks/0")
        ? json(409, { detail: "conflict" })
        : req.path.endsWith("/chunks/1")
          ? json(422, { detail: "fixes[3].lat: bad" })
          : undefined;
    await h.rec.startTrip(ROUTE, destinationOf(ROUTE));
    await drive(h, track(ROUTE));
    await h.rec.endTrip(true);
    await h.rec.settled();
    await h.rec.retryNow();
    await h.rec.settled();

    const count = (suffix: string) =>
      h.server.requests.filter((r) => r.path.endsWith(suffix)).length;
    expect(count("/chunks/0")).toBe(1);
    expect(count("/chunks/1")).toBe(1);
    expect(h.server.chunks.size).toBeGreaterThanOrEqual(2);
    expect(h.server.endBodies()).toHaveLength(1);
    expect(h.rec.getStatus()).toMatchObject({ queued: 0, rejected: false });
  });

  it("5xx, 429 and network errors back off, doubling, then deliver", async () => {
    const h = await optedIn();
    const failures: (Response | "network")[] = [
      json(503, { detail: "down" }),
      "network",
      json(429, { detail: "slow down" }, { "Retry-After": "60" }),
    ];
    h.server.state.override = () => failures.shift();
    await h.rec.startTrip(ROUTE, destinationOf(ROUTE));
    await drive(h, track(ROUTE));
    await h.rec.endTrip(true);
    await h.rec.settled();

    const live = () => h.timers.filter((t) => !t.cancelled);
    const fire = async () => {
      const t = live().at(-1)!;
      t.cancelled = true;
      h.clock.now += t.ms;
      t.fn();
      await h.rec.settled();
    };
    // Each failure stops the pass and schedules exactly one retry.
    expect(live().map((t) => t.ms)).toEqual([5_000]);
    // While a retry is scheduled, new seals wait for it rather than resend.
    expect(
      h.server.requests.filter((r) => r.method !== "GET").map((r) => r.path),
    ).toHaveLength(1);
    await fire();
    expect(live().map((t) => t.ms)).toEqual([10_000]);
    await fire();
    expect(live().map((t) => t.ms)).toEqual([60_000]); // Retry-After wins
    await fire();
    expect(live()).toEqual([]);
    expect(h.rec.getStatus().queued).toBe(0);
    expect(h.server.endBodies()).toHaveLength(1);
    expect(h.server.invalid).toEqual([]);
  });
});

describe("ordering the server enforces", () => {
  it("only strictly later, ~1 Hz, integer-ms fixes reach a chunk", async () => {
    const h = await optedIn();
    const path = track(ROUTE);
    await h.rec.startTrip(ROUTE, destinationOf(ROUTE));
    for (const p of path) {
      h.clock.now += 1000;
      const t = h.clock.now + 0.4; // fractional, as some browsers report
      const fix = { lat: p.lat, lon: p.lon, accuracy_m: 5 };
      await h.rec.addFix({ t, ...fix, speed_mps: NaN, heading_deg: NaN });
      await h.rec.addFix({ t, ...fix, speed_mps: 10, heading_deg: 90 }); // repeat
      await h.rec.addFix({
        t: t - 5000,
        ...fix,
        speed_mps: 10,
        heading_deg: 90,
      });
      await h.rec.addFix({
        t: t + 200,
        ...fix,
        speed_mps: 10,
        heading_deg: 90,
      }); // 5 Hz
    }
    await h.rec.endTrip(true);
    await h.rec.settled();

    expect(h.server.invalid).toEqual([]);
    for (const c of h.server.chunkBodies()) {
      for (let i = 0; i < c.fixes.length; i++) {
        const f = c.fixes[i];
        expect(Number.isInteger(f.t)).toBe(true);
        expect(f.speed_mps).toBeNull();
        expect(f.heading_deg).toBeNull();
        if (i) expect(f.t - c.fixes[i - 1].t).toBeGreaterThanOrEqual(900);
      }
    }
    expect(h.server.uploadedFixes().length).toBeGreaterThan(200);
  });

  it("end never predates its prediction, even for a reroute after the last fix", async () => {
    const storage = createMemoryTraceStorage();
    const server = fakeCommute();
    const first = await optedIn({ storage, server });
    await first.rec.startTrip(ROUTE, destinationOf(ROUTE));
    await drive(first, track(ROUTE).slice(0, 150));
    first.clock.now += 5000; // the reroute lands after the last fix...
    const reroutedAt = first.clock.now;
    await first.rec.reroute(REROUTE);
    await first.rec.settled(); // ...and the app dies before another fix

    const second = harness({ storage, server });
    await second.rec.init();
    await second.rec.settled();
    expect(server.invalid).toEqual([]);
    expect(server.endBodies()).toEqual([
      expect.objectContaining({
        ended_at: reroutedAt,
        arrived: false,
        prediction: expect.objectContaining({ effective_at: reroutedAt }),
      }),
    ]);
  });
});

describe("opt-in", () => {
  it("checks a pasted token with GET /v1/me, stores it with its label, and refuses a dead one", async () => {
    const h = harness();
    await h.rec.init();
    expect(h.rec.getStatus()).toMatchObject({
      configured: true,
      optedIn: false,
    });
    expect(await h.rec.optIn("  not-a-token ")).toEqual({
      ok: false,
      reason: "rejected",
    });
    expect(h.rec.getStatus().optedIn).toBe(false);
    h.server.state.override = () => "network";
    expect(await h.rec.optIn(TOKEN)).toEqual({
      ok: false,
      reason: "unreachable",
    });
    h.server.state.override = null;
    expect(await h.rec.optIn(` ${TOKEN}\n`)).toEqual({
      ok: true,
      label: "derek-test",
    });
    expect(h.rec.getStatus()).toMatchObject({
      optedIn: true,
      label: "derek-test",
      enabled: true,
    });
    expect(h.server.requests.at(-1)).toMatchObject({
      method: "GET",
      path: "/v1/me",
      auth: `Bearer ${TOKEN}`,
    });

    // Every /v1 call after it carries the token.
    await h.rec.startTrip(ROUTE, destinationOf(ROUTE));
    await drive(h, track(ROUTE));
    await h.rec.endTrip(true);
    await h.rec.settled();
    expect(h.server.requests.length).toBeGreaterThan(4);
    for (const r of h.server.requests.slice(3))
      expect(r.auth).toBe(`Bearer ${TOKEN}`);

    // It survives a relaunch.
    const again = harness({ storage: h.storage, server: h.server });
    await again.rec.init();
    expect(again.rec.getStatus()).toMatchObject({
      optedIn: true,
      label: "derek-test",
    });
  });

  it("records nothing without a commute URL, without a token, or when off", async () => {
    const inert = [harness({ baseUrl: "" }), harness(), await optedIn()];
    await inert[0].rec.init();
    await inert[1].rec.init();
    await inert[2].rec.setEnabled(false);
    expect(inert[0].rec.getStatus().configured).toBe(false);
    for (const h of inert) {
      const asked = h.server.requests.length;
      await h.rec.startTrip(ROUTE, destinationOf(ROUTE));
      await drive(h, track(ROUTE));
      await h.rec.endTrip(true);
      await h.rec.settled();
      expect(h.server.requests.length).toBe(asked);
      expect(await h.storage.load()).toMatchObject({ trips: [], outbox: [] });
    }
  });

  it("forget clears the token, the queue and any open trip", async () => {
    const h = await optedIn();
    h.server.state.override = () => json(503, { detail: "down" });
    await h.rec.startTrip(ROUTE, destinationOf(ROUTE));
    await drive(h, track(ROUTE).slice(0, 250));
    await h.rec.settled();
    expect((await h.storage.load()).outbox.length).toBeGreaterThan(0);

    await h.rec.forget();
    expect(await h.storage.load()).toEqual({
      settings: null,
      trips: [],
      outbox: [],
    });
    expect(h.rec.getStatus()).toMatchObject({
      optedIn: false,
      queued: 0,
      recording: false,
    });
  });
});
