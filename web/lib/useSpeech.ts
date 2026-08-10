"use client";

// Thin wrapper around the browser SpeechSynthesis API for spoken turn-by-turn
// instructions and safety alerts. No new npm dependency — this is a Web API
// available in every modern browser (unsupported browsers just get silent
// no-ops from `speak`).

import { useEffect, useState } from "react";

export const VOICE_STORAGE_KEY = "sr.voice";
const DEFAULT_VOICE_ENABLED = false;

export interface Speech {
  enabled: boolean;
  setEnabled: (v: boolean) => void;
  speak: (text: string) => void;
}

function synth(): SpeechSynthesis | null {
  if (typeof window === "undefined" || !window.speechSynthesis) return null;
  return window.speechSynthesis;
}

export function useSpeech(): Speech {
  const [enabled, setEnabledState] = useState(DEFAULT_VOICE_ENABLED);

  // Read the persisted preference after mount so SSR markup and the first
  // client render agree — same reasoning/pattern as the `units` preference
  // in app/page.tsx (see UNITS_STORAGE_KEY there).
  useEffect(() => {
    const stored = window.localStorage.getItem(VOICE_STORAGE_KEY);
    if (stored === "true") setEnabledState(true);
    else if (stored === "false") setEnabledState(false);
  }, []);

  const setEnabled = (v: boolean) => {
    // Turning voice ON must "prime" iOS Safari's user-gesture requirement:
    // an empty utterance is spoken synchronously, inside the same call stack
    // as the click that invoked setEnabled, before anything else happens.
    // Doing this later (e.g. in an effect) means iOS no longer counts it as
    // gesture-triggered and every subsequent speak() silently fails.
    if (v) {
      const s = synth();
      if (s) s.speak(new SpeechSynthesisUtterance(""));
    }
    setEnabledState(v);
    window.localStorage.setItem(VOICE_STORAGE_KEY, String(v));
  };

  const speak = (text: string) => {
    if (!enabled) return;
    const s = synth();
    if (!s) return;
    // Clear any queued/stale utterance first — otherwise instructions back
    // up into a queue and get spoken late.
    s.cancel();
    s.speak(new SpeechSynthesisUtterance(text));
  };

  return { enabled, setEnabled, speak };
}
