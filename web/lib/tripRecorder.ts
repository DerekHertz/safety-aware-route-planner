// The trip-trace recorder (ADR-0017), against the ingest contract in
// ADR-0018. Framework-free: navigation events and GPS fixes go in, sealed
// chunks go to durable storage, and an outbox drains them to the commute
// service. `useTripRecorder` wires it to live navigation; the scenario tests
// in tripRecorder.test.ts drive it over in-memory storage and a fake server.
//
// A trip's life:
//
//   startTrip(route)   navigation started: new trip UUID; the route is HELD,
//                      not uploaded (see sealing below)
//   addFix(fix)        ~1 Hz; kept only if clear of every private point, and
//                      persisted at once, so a killed app loses nothing
//   reroute(route)     the replacement becomes the followed artifact
//   endTrip(arrived)   final seal; the held-back tail is discarded; `end`
//                      is queued with the followed artifact's prediction
//
// Sealing, about every 2 minutes and at the end: the fixes that are TRIM_M
// clear of the latest one become a chunk with the next `seq`, serialized
// once and stored in the outbox in the same transaction that advances the
// trip, so a retry (even after a relaunch) sends the identical bytes. The
// followed artifacts go up, clipped (tracePrivacy.ts), only once the trip
// seals its first fixes: a trip that never gets clear of its ends uploads
// nothing at all but its `end`.
//
// A trip still open at launch has no navigation behind it, so `init` ends it
// with `arrived: false` at its last fix, and resends anything unsent.
//
// Responses (ADR-0018): 2xx done; 401 stops recording until a new token;
// 408, 425, 429, 5xx and network errors back off; any other status is
// permanent, and the item is dropped. Nothing here logs, least of all a
// coordinate.

import {
  Anchor,
  RawFix,
  clearOf,
  clipArtifact,
  followsHead,
  sealablePrefix,
  toTraceFix,
} from "./tracePrivacy";
import type {
  DeviceSettings,
  OutboxItem,
  TraceStorage,
  TripRecord,
} from "./traceStorage";
import { outboxOrder } from "./traceStorage";
import type {
  CarriedPreference,
  EtaPrediction,
  FollowedArtifact,
  LatLon,
  RouteAlternative,
  TesterInfo,
  TraceChunk,
  TripEnd,
} from "./types";

/** Seal a chunk of fixes about this often (ADR-0017). */
export const SEAL_INTERVAL_MS = 120_000;
// The server's limits (commute/schemas.py).
const MAX_FIXES_PER_CHUNK = 600;
const MAX_SEQ = 99_999;
const MAX_BODY_BYTES = 1_048_576;
/** First retry delay; doubles per consecutive failure, up to the max. */
export const BACKOFF_BASE_MS = 5_000;
export const BACKOFF_MAX_MS = 600_000;

export type OptInResult =
  | { ok: true; label: string }
  | {
      ok: false;
      reason: "rejected" | "unreachable" | "error" | "unconfigured";
    };

export interface RecorderStatus {
  /** A commute service URL is configured at all. */
  configured: boolean;
  /** This device holds a tester token. */
  optedIn: boolean;
  label: string | null;
  enabled: boolean;
  /** The server refused the token (401); recording is stopped. */
  rejected: boolean;
  /** A trip is being recorded right now. */
  recording: boolean;
  /** Requests waiting in the outbox. */
  queued: number;
  /** When the next retry is due, while backing off. */
  retryAt: number | null;
  /** The name of the last internal failure (never a message with data). */
  error: string | null;
}

export interface RecorderOptions {
  storage: TraceStorage;
  /** The commute service; "" leaves the recorder inert. */
  baseUrl: string;
  fetch?: typeof fetch;
  now?: () => number;
  newTripId?: () => string;
  /** Run `fn` after `ms`; returns a cancel function. */
  schedule?: (fn: () => void, ms: number) => () => void;
  /** Backoff jitter source, in [0, 1). */
  random?: () => number;
}

export interface TripRecorder {
  /** Load state, end any trip orphaned by a killed app, resend the outbox. */
  init(): Promise<void>;
  /** Check a pasted tester token with `GET /v1/me`; stored only if good. */
  optIn(token: string): Promise<OptInResult>;
  setEnabled(enabled: boolean): Promise<void>;
  /** Delete the token, the queue and any open trip from this device. */
  forget(): Promise<void>;
  startTrip(route: RouteAlternative, destination: LatLon | null): Promise<void>;
  addFix(fix: RawFix): Promise<void>;
  reroute(route: RouteAlternative): Promise<void>;
  endTrip(arrived: boolean): Promise<void>;
  /** Cancel any backoff and send now (e.g. the device came back online). */
  retryNow(): Promise<void>;
  /** Resolves once every queued operation and upload pass has finished. */
  settled(): Promise<void>;
  getStatus(): RecorderStatus;
  subscribe(listener: () => void): () => void;
}

type SendOutcome =
  | { kind: "done" }
  | { kind: "permanent" }
  | { kind: "unauthorized" }
  | { kind: "transient"; retryAfterMs: number | null };

/** How ADR-0018 says to treat a status code. */
function classify(status: number): SendOutcome["kind"] {
  if (status >= 200 && status < 300) return "done";
  if (status === 401) return "unauthorized";
  if (status === 408 || status === 425 || status === 429 || status >= 500) {
    return "transient";
  }
  return "permanent"; // 404, 409, 413, 422 and anything else unexpected
}

function retryAfterMs(header: string | null, now: number): number | null {
  if (!header) return null;
  const secs = Number(header);
  if (Number.isFinite(secs) && secs >= 0) return secs * 1000;
  const at = Date.parse(header);
  return Number.isFinite(at) ? Math.max(0, at - now) : null;
}

function randomUuid(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

const byteLength = (s: string) => new TextEncoder().encode(s).length;

function predictionOf(route: RouteAlternative, at: number): EtaPrediction {
  // A v1 artifact has no traffic_basis (ADR-0004), hence the carried type.
  const pref: CarriedPreference = route.preference;
  return {
    effective_at: at,
    eta_s: route.eta_s,
    level: pref.level,
    profile_version: pref.traffic_basis?.profile_version ?? null,
  };
}

function endOf(route: RouteAlternative): Anchor | null {
  const c = route.geometry.coordinates;
  if (!c.length) return null;
  const [lon, lat] = c[c.length - 1];
  return { lon, lat };
}

export function createTripRecorder(opts: RecorderOptions): TripRecorder {
  const storage = opts.storage;
  const baseUrl = opts.baseUrl.replace(/\/+$/, "");
  const doFetch: typeof fetch =
    opts.fetch ?? ((input, init) => fetch(input, init));
  const clock = opts.now ?? Date.now;
  const now = () => Math.round(clock());
  const newTripId = opts.newTripId ?? randomUuid;
  const schedule =
    opts.schedule ??
    ((fn: () => void, ms: number) => {
      const id = setTimeout(fn, ms);
      return () => clearTimeout(id);
    });
  const random = opts.random ?? Math.random;

  let settings: DeviceSettings | null = null;
  let trip: TripRecord | null = null;
  let outbox: OutboxItem[] = [];
  let failures = 0;
  let retryAt: number | null = null;
  let cancelRetry: (() => void) | null = null;
  let flushing: Promise<void> | null = null;
  let queue: Promise<unknown> = Promise.resolve();
  let error: string | null = null;
  const listeners = new Set<() => void>();

  const canUpload = () =>
    !!baseUrl && !!settings && settings.enabled && !settings.rejected;
  // Recording needs the same things uploading does: a trip nobody can send
  // is not worth holding location history for.
  const canRecord = canUpload;

  function computeStatus(): RecorderStatus {
    return {
      configured: !!baseUrl,
      optedIn: !!settings,
      label: settings?.label ?? null,
      enabled: settings?.enabled ?? false,
      rejected: settings?.rejected ?? false,
      recording: !!trip && canRecord(),
      queued: outbox.length,
      retryAt,
      error,
    };
  }
  let status = computeStatus();
  function changed() {
    const next = computeStatus();
    const keys = Object.keys(next) as (keyof RecorderStatus)[];
    if (keys.every((k) => next[k] === status[k])) return;
    status = next;
    for (const l of listeners) l();
  }

  /** Operations run one at a time, in call order; fixes arrive faster than
   *  storage always answers. A failure is recorded, never thrown: the hook
   *  fires these and forgets them. */
  function run<T>(op: () => Promise<T>, fallback: T): Promise<T> {
    const p = queue
      .then(op)
      .catch((e: unknown) => {
        error = e instanceof Error ? e.name : "Error";
        return fallback;
      })
      .finally(changed);
    queue = p;
    return p;
  }

  // --- sealing ---------------------------------------------------------

  function sealChunk(t: TripRecord, chunk: TraceChunk, into: OutboxItem[]) {
    if (t.nextSeq > MAX_SEQ) return;
    const body = JSON.stringify(chunk);
    // The server would answer 413 forever; never the case at these limits.
    if (byteLength(body) > MAX_BODY_BYTES) return;
    const seq = t.nextSeq++;
    into.push({
      key: `${t.tripId}/${String(seq).padStart(5, "0")}`,
      method: "PUT",
      path: `/v1/trips/${t.tripId}/chunks/${seq}`,
      body,
      createdAt: now(),
    });
  }

  function sealArtifact(
    t: TripRecord,
    fa: FollowedArtifact,
    into: OutboxItem[],
  ) {
    const clipped = clipArtifact(fa.artifact, t.anchors);
    if (!clipped) return;
    sealChunk(
      t,
      {
        fixes: [],
        artifacts: [{ effective_at: fa.effective_at, artifact: clipped }],
      },
      into,
    );
  }

  /** Seal every fix that is clear of the head, as chunks of at most 600;
   *  with the first of them, the artifacts held so far. */
  function seal(t: TripRecord, into: OutboxItem[]) {
    const n = sealablePrefix(t.pending, t.head);
    // Re-checked against the anchors: a reroute can add one after a fix
    // was accepted.
    const ready = t.pending.slice(0, n).filter((f) => clearOf(f, t.anchors));
    t.pending = t.pending.slice(n);
    if (t.head) t.lastSealAt = t.head.t;
    if (!ready.length) return;
    if (!t.sealedFixes) {
      t.sealedFixes = true;
      for (const fa of t.heldArtifacts) sealArtifact(t, fa, into);
      t.heldArtifacts = [];
    }
    for (let i = 0; i < ready.length; i += MAX_FIXES_PER_CHUNK) {
      sealChunk(t, { fixes: ready.slice(i, i + MAX_FIXES_PER_CHUNK) }, into);
    }
  }

  function enqueue(items: OutboxItem[]) {
    if (!items.length) return;
    outbox = [...outbox, ...items].sort(outboxOrder);
  }

  /** The final seal, the tail discarded, `end` queued, the trip (and its
   *  anchors) deleted: one transaction. */
  async function finalize(t: TripRecord, arrived: boolean, endedAt: number) {
    const items: OutboxItem[] = [];
    seal(t, items);
    const end: TripEnd = {
      ended_at: endedAt,
      arrived,
      prediction: t.prediction,
    };
    items.push({
      key: `${t.tripId}/end`,
      method: "POST",
      path: `/v1/trips/${t.tripId}/end`,
      body: JSON.stringify(end),
      createdAt: now(),
    });
    await storage.apply({ putOutbox: items, deleteTrips: [t.tripId] });
    if (trip?.tripId === t.tripId) trip = null;
    enqueue(items);
  }

  /** Never before the last fix or the followed artifact's effective_at: the
   *  server refuses a prediction that took effect after the trip ended. */
  function endTime(t: TripRecord, at: number): number {
    return Math.max(at, t.head?.t ?? 0, t.prediction?.effective_at ?? 0);
  }

  // --- uploading -------------------------------------------------------

  async function send(item: OutboxItem, token: string): Promise<SendOutcome> {
    let resp: Response;
    try {
      resp = await doFetch(`${baseUrl}${item.path}`, {
        method: item.method,
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: item.body,
      });
    } catch {
      return { kind: "transient", retryAfterMs: null };
    }
    const kind = classify(resp.status);
    if (kind === "transient") {
      return {
        kind,
        retryAfterMs: retryAfterMs(resp.headers.get("Retry-After"), now()),
      };
    }
    return { kind };
  }

  function stopRetry() {
    cancelRetry?.();
    cancelRetry = null;
    retryAt = null;
  }

  function scheduleRetry(serverAskedMs: number | null) {
    failures++;
    const base = Math.min(
      BACKOFF_MAX_MS,
      BACKOFF_BASE_MS * 2 ** Math.min(failures - 1, 20),
    );
    let delay = base * (0.5 + random() / 2);
    if (serverAskedMs !== null) {
      delay = Math.min(BACKOFF_MAX_MS, Math.max(delay, serverAskedMs));
    }
    stopRetry();
    retryAt = now() + delay;
    cancelRetry = schedule(() => {
      cancelRetry = null;
      retryAt = null;
      void flush();
    }, delay);
  }

  /** Send the outbox in order until it is empty, or a failure says stop.
   *  While a retry is scheduled, only that timer (or retryNow, or a
   *  relaunch) sends again. */
  async function drain() {
    try {
      for (;;) {
        if (!canUpload() || cancelRetry) return;
        const item = outbox[0];
        if (!item) return;
        const outcome = await send(item, settings!.token);
        if (outcome.kind === "done" || outcome.kind === "permanent") {
          failures = 0;
          await storage.apply({ deleteOutbox: [item.key] });
          outbox = outbox.filter((i) => i.key !== item.key);
        } else if (outcome.kind === "unauthorized") {
          if (settings) {
            settings = { ...settings, rejected: true };
            await storage.apply({ settings });
          }
          return;
        } else {
          scheduleRetry(outcome.retryAfterMs);
          return;
        }
        changed();
      }
    } catch (e) {
      error = e instanceof Error ? e.name : "Error";
    } finally {
      changed();
    }
  }

  function flush(): Promise<void> {
    if (!flushing) {
      flushing = drain().finally(() => {
        flushing = null;
      });
    }
    return flushing;
  }

  // --- the interface ---------------------------------------------------

  return {
    init: () =>
      run(async () => {
        const state = await storage.load();
        settings = state.settings;
        outbox = state.outbox;
        // Navigation does not survive a relaunch, so an open trip is over.
        for (const t of state.trips) {
          await finalize(t, false, endTime(t, t.startedAt));
        }
        void flush();
      }, undefined),

    optIn: (raw) =>
      run(
        async (): Promise<OptInResult> => {
          const token = raw.trim();
          if (!baseUrl) return { ok: false, reason: "unconfigured" };
          if (!token) return { ok: false, reason: "rejected" };
          let resp: Response;
          try {
            resp = await doFetch(`${baseUrl}/v1/me`, {
              headers: { Authorization: `Bearer ${token}` },
            });
          } catch {
            return { ok: false, reason: "unreachable" };
          }
          if (resp.status === 401) return { ok: false, reason: "rejected" };
          if (!resp.ok) return { ok: false, reason: "error" };
          const info = (await resp.json()) as TesterInfo;
          settings = {
            token,
            label: String(info.label),
            enabled: true,
            rejected: false,
          };
          await storage.apply({ settings });
          // A good token deserves an immediate try at anything queued.
          stopRetry();
          failures = 0;
          void flush();
          return { ok: true, label: settings.label };
        },
        { ok: false, reason: "error" },
      ),

    setEnabled: (enabled) =>
      run(async () => {
        if (!settings) return;
        settings = { ...settings, enabled };
        await storage.apply({ settings });
        if (!enabled && trip) await finalize(trip, false, endTime(trip, now()));
        void flush();
      }, undefined),

    forget: () =>
      run(async () => {
        stopRetry();
        const state = await storage.load();
        await storage.apply({
          settings: null,
          deleteTrips: state.trips.map((t) => t.tripId),
          deleteOutbox: state.outbox.map((i) => i.key),
        });
        settings = null;
        trip = null;
        outbox = [];
        failures = 0;
      }, undefined),

    startTrip: (route, destination) =>
      run(async () => {
        if (trip) await finalize(trip, false, endTime(trip, now()));
        if (!canRecord()) return;
        const at = now();
        const coords = route.geometry.coordinates;
        const anchors: Anchor[] = [];
        if (coords.length) {
          const [lon, lat] = coords[0];
          anchors.push({ lon, lat });
        }
        const end = endOf(route);
        if (end) anchors.push(end);
        if (destination)
          anchors.push({ lat: destination.lat, lon: destination.lon });
        trip = {
          tripId: newTripId(),
          startedAt: at,
          anchors,
          head: null,
          pending: [],
          lastSealAt: at,
          nextSeq: 0,
          sealedFixes: false,
          heldArtifacts: [{ effective_at: at, artifact: route }],
          prediction: predictionOf(route, at),
        };
        await storage.apply({ putTrips: [trip] });
      }, undefined),

    addFix: (raw) =>
      run(async () => {
        const t = trip;
        if (!t || !canRecord()) return;
        const fix = toTraceFix(raw);
        if (!fix || !followsHead(t.head, fix)) return;
        // The first fix is the trip's origin: a private point of its own.
        if (!t.head) {
          t.anchors.push({
            lat: fix.lat,
            lon: fix.lon,
            accuracy_m: fix.accuracy_m,
          });
        }
        t.head = fix;
        // Inside a private point's circle: never stored, never uploaded.
        if (clearOf(fix, t.anchors)) t.pending.push(fix);
        const items: OutboxItem[] = [];
        if (fix.t - t.lastSealAt >= SEAL_INTERVAL_MS) seal(t, items);
        await storage.apply({ putTrips: [t], putOutbox: items });
        enqueue(items);
        if (items.length) void flush();
      }, undefined),

    reroute: (route) =>
      run(async () => {
        const t = trip;
        if (!t || !canRecord()) return;
        // Strictly after the artifact it replaces.
        const at = Math.max(now(), (t.prediction?.effective_at ?? 0) + 1);
        const end = endOf(route);
        if (end) t.anchors.push(end);
        t.prediction = predictionOf(route, at);
        const fa: FollowedArtifact = { effective_at: at, artifact: route };
        const items: OutboxItem[] = [];
        if (t.sealedFixes) sealArtifact(t, fa, items);
        else t.heldArtifacts.push(fa);
        await storage.apply({ putTrips: [t], putOutbox: items });
        enqueue(items);
        if (items.length) void flush();
      }, undefined),

    endTrip: (arrived) =>
      run(async () => {
        if (!trip) return;
        await finalize(trip, arrived, endTime(trip, now()));
        void flush();
      }, undefined),

    retryNow: () =>
      run(async () => {
        stopRetry();
        void flush();
      }, undefined),

    async settled() {
      for (;;) {
        const q = queue;
        await q;
        if (flushing) {
          await flushing;
          continue;
        }
        if (q === queue) return;
      }
    },

    getStatus: () => status,

    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
