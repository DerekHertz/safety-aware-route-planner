import { describe, expect, it, vi } from "vitest";
import { createWakeLockController } from "./useWakeLock";

// A stubbed WakeLockSentinel: exposes `release()` (async, matching the real
// API — release() "runs in parallel" per spec, so its `release` event fires
// on a later microtask, never synchronously within the call that triggered
// it) and lets the test fire that event itself to simulate the browser
// revoking the lock for reasons other than an explicit release() call (e.g.
// going hidden, or low battery).
function fakeSentinel() {
  let releaseListener: (() => void) | null = null;
  const release = vi.fn(() => {
    queueMicrotask(() => releaseListener?.());
    return Promise.resolve();
  });
  return {
    release,
    addEventListener: vi.fn((type: string, cb: () => void) => {
      if (type === "release") releaseListener = cb;
    }),
    removeEventListener: vi.fn(),
    // Test-only hook to simulate the browser dropping the lock without an
    // explicit release() call from our code.
    simulateExternalRelease: () => queueMicrotask(() => releaseListener?.()),
  };
}

// A stubbed `navigator.wakeLock.request` — resolves with a fresh fake
// sentinel each call by default, but a test can swap in its own resolution
// (e.g. a rejection) via `impl`.
function fakeNavigator(
  impl: () => Promise<ReturnType<typeof fakeSentinel>> = () =>
    Promise.resolve(fakeSentinel()),
) {
  return { wakeLock: { request: vi.fn(impl) } };
}

// A stubbed `document`: Node's built-in EventTarget is a real EventTarget,
// so addEventListener/dispatchEvent behave exactly as they would in a
// browser — no jsdom required for this.
function fakeDocument(visibilityState: "visible" | "hidden" = "visible") {
  const doc = Object.assign(new EventTarget(), { visibilityState });
  return doc;
}

/** Flushes queued microtasks (promise resolutions, queueMicrotask) so async
 *  effects inside the controller have run before assertions. */
async function flush() {
  await Promise.resolve();
  await Promise.resolve();
}

describe("createWakeLockController", () => {
  it("acquires when enabled", async () => {
    const nav = fakeNavigator();
    const doc = fakeDocument();
    const onChange = vi.fn();
    const controller = createWakeLockController(nav, doc, onChange);

    controller.setWanted(true);
    await flush();

    expect(nav.wakeLock.request).toHaveBeenCalledTimes(1);
    expect(nav.wakeLock.request).toHaveBeenCalledWith("screen");
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("releases when disabled", async () => {
    const nav = fakeNavigator();
    const doc = fakeDocument();
    const onChange = vi.fn();
    const controller = createWakeLockController(nav, doc, onChange);

    controller.setWanted(true);
    await flush();
    onChange.mockClear();

    controller.setWanted(false);
    await flush();

    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("re-acquires after a visibilitychange back to visible", async () => {
    const nav = fakeNavigator();
    const doc = fakeDocument("visible");
    const onChange = vi.fn();
    const controller = createWakeLockController(nav, doc, onChange);

    controller.setWanted(true);
    await flush();
    expect(nav.wakeLock.request).toHaveBeenCalledTimes(1);

    // The browser silently drops the lock while the tab is hidden — this is
    // the half that's easy to miss: nothing brings it back on its own.
    doc.visibilityState = "hidden";
    const [firstSentinel] = nav.wakeLock.request.mock.results.map(
      (r) => r.value,
    );
    const granted = await firstSentinel;
    granted.simulateExternalRelease();
    await flush();
    expect(onChange).toHaveBeenLastCalledWith(false);

    // Coming back to visible must trigger a fresh request, not assume the
    // old one still holds.
    doc.visibilityState = "visible";
    doc.dispatchEvent(new Event("visibilitychange"));
    await flush();

    expect(nav.wakeLock.request).toHaveBeenCalledTimes(2);
    expect(onChange).toHaveBeenLastCalledWith(true);
  });

  it("does not re-acquire after being deliberately disabled", async () => {
    const nav = fakeNavigator();
    const doc = fakeDocument("hidden");
    const onChange = vi.fn();
    const controller = createWakeLockController(nav, doc, onChange);

    controller.setWanted(true);
    await flush();
    controller.setWanted(false);
    await flush();
    onChange.mockClear();

    doc.visibilityState = "visible";
    doc.dispatchEvent(new Event("visibilitychange"));
    await flush();

    // Still just the one request from the initial enable — the session
    // ended, so a later visibilitychange must be a no-op.
    expect(nav.wakeLock.request).toHaveBeenCalledTimes(1);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("survives an absent navigator.wakeLock", async () => {
    const nav = {}; // no `wakeLock` at all — older Safari, some in-app browsers
    const doc = fakeDocument();
    const onChange = vi.fn();
    const controller = createWakeLockController(nav, doc, onChange);

    expect(controller.supported).toBe(false);
    expect(() => controller.setWanted(true)).not.toThrow();
    await flush();

    expect(onChange).not.toHaveBeenCalled();

    doc.dispatchEvent(new Event("visibilitychange"));
    await flush();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("survives a rejected request", async () => {
    const nav = fakeNavigator(() => Promise.reject(new Error("denied")));
    const doc = fakeDocument();
    const onChange = vi.fn();
    const controller = createWakeLockController(nav, doc, onChange);

    expect(() => controller.setWanted(true)).not.toThrow();
    await flush();

    expect(onChange).toHaveBeenCalledWith(false);
    expect(onChange).not.toHaveBeenCalledWith(true);
  });
});
