// Durable storage for the trip-trace recorder (ADR-0017): one IndexedDB
// database, three object stores, behind a two-method interface so the
// recorder can be driven over an in-memory copy in tests.
//
//   settings  "device" -> DeviceSettings   the tester token (one per device)
//   trips     tripId   -> TripRecord       the open trip: its anchors, the
//                                           fixes not yet sealed, its next seq
//   outbox    key      -> OutboxItem       sealed requests, byte-exact, until
//                                           the server answers for good
//
// Every change is one `apply` call, and `apply` is atomic: sealing a chunk
// writes the outbox item and advances the trip's `nextSeq` in the same
// transaction, so a kill between the two cannot reuse a seq for different
// content. Everything here stays on the phone; the anchors are the private
// points themselves.

import type { Anchor } from "./tracePrivacy";
import type { EtaPrediction, FollowedArtifact, TraceFix } from "./types";

export interface DeviceSettings {
  token: string;
  /** What `GET /v1/me` called this token, shown back to the tester. */
  label: string;
  /** The tester's own on/off switch. Off keeps the token and the queue. */
  enabled: boolean;
  /** The server answered 401: the token is unknown or revoked. */
  rejected: boolean;
}

export interface TripRecord {
  tripId: string;
  startedAt: number;
  /** Private points: nothing within TRIM_M of these is ever uploaded. */
  anchors: Anchor[];
  /** The latest accepted fix; the trailing hold-back is measured from it. */
  head: TraceFix | null;
  /** Accepted fixes clear of every anchor, in time order, not yet sealed. */
  pending: TraceFix[];
  /** When the last chunk of fixes was sealed (or the trip started). */
  lastSealAt: number;
  nextSeq: number;
  /** Whether any chunk of fixes has been sealed. Until one is, followed
   *  artifacts are held here unclipped: a trip that never gets 300 m clear
   *  of its ends uploads no route either. */
  sealedFixes: boolean;
  heldArtifacts: FollowedArtifact[];
  /** From the artifact being followed now; sent with `end`. */
  prediction: EtaPrediction | null;
}

export interface OutboxItem {
  /** `${tripId}/${seq, 5 digits}` or `${tripId}/end`: chunks sort first. */
  key: string;
  method: "PUT" | "POST";
  /** Path under the commute service's base URL. */
  path: string;
  /** The sealed JSON. Resent exactly, so a retry is always a replay. */
  body: string;
  createdAt: number;
}

export interface StorageOps {
  /** undefined leaves it; null deletes it. */
  settings?: DeviceSettings | null;
  putTrips?: TripRecord[];
  deleteTrips?: string[];
  putOutbox?: OutboxItem[];
  deleteOutbox?: string[];
}

export interface StoredState {
  settings: DeviceSettings | null;
  trips: TripRecord[];
  outbox: OutboxItem[];
}

export interface TraceStorage {
  load(): Promise<StoredState>;
  /** All of `ops`, or none of it. */
  apply(ops: StorageOps): Promise<void>;
}

/** Outbox items in the order to send them: oldest first, a trip's chunks
 *  before its end. */
export function outboxOrder(a: OutboxItem, b: OutboxItem): number {
  return (
    a.createdAt - b.createdAt || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)
  );
}

/** For tests: the same contract in memory. Values are structured-cloned in
 *  and out, as IndexedDB does, so a caller can never alias stored state. */
export function createMemoryTraceStorage(): TraceStorage {
  let settings: DeviceSettings | null = null;
  const trips = new Map<string, TripRecord>();
  const outbox = new Map<string, OutboxItem>();
  return {
    async load() {
      return structuredClone({
        settings,
        trips: [...trips.values()],
        outbox: [...outbox.values()].sort(outboxOrder),
      });
    },
    async apply(ops) {
      const o = structuredClone(ops);
      if (o.settings !== undefined) settings = o.settings;
      for (const t of o.putTrips ?? []) trips.set(t.tripId, t);
      for (const id of o.deleteTrips ?? []) trips.delete(id);
      for (const i of o.putOutbox ?? []) outbox.set(i.key, i);
      for (const k of o.deleteOutbox ?? []) outbox.delete(k);
    },
  };
}

const DB_NAME = "sr-trip-traces";
const DB_VERSION = 1;
const SETTINGS_KEY = "device";

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function openDb(factory: IDBFactory): Promise<IDBDatabase> {
  const req = factory.open(DB_NAME, DB_VERSION);
  req.onupgradeneeded = () => {
    const db = req.result;
    if (!db.objectStoreNames.contains("settings")) {
      db.createObjectStore("settings");
    }
    if (!db.objectStoreNames.contains("trips")) {
      db.createObjectStore("trips", { keyPath: "tripId" });
    }
    if (!db.objectStoreNames.contains("outbox")) {
      db.createObjectStore("outbox", { keyPath: "key" });
    }
  };
  return request(req);
}

/** The browser implementation. Thin on purpose: all behavior is in the
 *  recorder and is tested over the in-memory storage above. */
export function createIdbTraceStorage(
  factory: IDBFactory = indexedDB,
): TraceStorage {
  const db = openDb(factory);
  // Handled where it is awaited; this only stops an early failure (private
  // mode, storage disabled) being reported as unhandled in the meantime.
  db.catch(() => {});
  return {
    async load() {
      const tx = (await db).transaction(
        ["settings", "trips", "outbox"],
        "readonly",
      );
      const [settings, trips, outbox] = await Promise.all([
        request(tx.objectStore("settings").get(SETTINGS_KEY)),
        request(tx.objectStore("trips").getAll()),
        request(tx.objectStore("outbox").getAll()),
      ]);
      return {
        settings: (settings as DeviceSettings | undefined) ?? null,
        trips: trips as TripRecord[],
        outbox: (outbox as OutboxItem[]).sort(outboxOrder),
      };
    },
    async apply(ops) {
      const tx = (await db).transaction(
        ["settings", "trips", "outbox"],
        "readwrite",
      );
      const done = new Promise<void>((resolve, reject) => {
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
      });
      const settings = tx.objectStore("settings");
      const trips = tx.objectStore("trips");
      const outbox = tx.objectStore("outbox");
      if (ops.settings === null) settings.delete(SETTINGS_KEY);
      else if (ops.settings) settings.put(ops.settings, SETTINGS_KEY);
      for (const t of ops.putTrips ?? []) trips.put(t);
      for (const id of ops.deleteTrips ?? []) trips.delete(id);
      for (const i of ops.putOutbox ?? []) outbox.put(i);
      for (const k of ops.deleteOutbox ?? []) outbox.delete(k);
      await done;
    },
  };
}
