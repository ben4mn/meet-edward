"use client";

// Edward in the hub's greeting card (2026-09-26).
//
// The hub's one live Edward. His mood comes from the same presence /screen
// uses (display/hooks/useEdwardState.ts — real signals only) and his status
// is his accessible name. He waves (a) the first time the hub shows him on
// a given day and (b) whenever someone new taps in at the who-toggle —
// `waveKey` changes on that tap, never on a reload or a hydration.

import React, { useEffect, useState } from "react";

import { EdwardAvatar } from "@/components/EdwardAvatar";
import { shouldWave, type Mood } from "@/lib/edwardMood";

export const HUB_WAVE_KEY = "edward:hubLastWaveAt";

function readStored(): string | null {
  try {
    return window.localStorage.getItem(HUB_WAVE_KEY);
  } catch {
    return null;
  }
}

function store(iso: string): void {
  try {
    window.localStorage.setItem(HUB_WAVE_KEY, iso);
  } catch {
    // A kiosk with storage blocked still greets; it just may wave again.
  }
}

export function GreetingEdward({
  mood,
  label,
  waveKey,
  size = 200,
}: {
  mood: Mood;
  label: string;
  /** Bump to wave (someone new tapped in). */
  waveKey: number;
  size?: number;
}) {
  const [wave, setWave] = useState<number | null>(null);

  // Once a day, the first time he is shown.
  useEffect(() => {
    const now = new Date();
    if (shouldWave(readStored(), now)) {
      store(now.toISOString());
      setWave((w) => (w ?? 0) + 1);
    }
  }, []);

  // Someone new at the hub.
  useEffect(() => {
    if (waveKey > 0) setWave((w) => (w ?? 0) + 1);
  }, [waveKey]);

  return (
    <EdwardAvatar
      size={size}
      mood={mood}
      live
      aria-label={label}
      title={label}
      beat={wave === null ? null : "wave"}
      beatKey={wave ?? undefined}
    />
  );
}
