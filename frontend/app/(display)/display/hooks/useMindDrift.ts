"use client";

// His mind drifting on /screen (round 2, 2026-09-28): one of his recent
// thoughts at a time, the next chosen at random after a random 25–70 s,
// never one of the last two (lib/thinkingModel.ts pickMindThought /
// nextMindDelayMs). One timer, re-armed after each change — nothing loops.
// `rand` is injected so tests can drive it; the page passes Math.random.

import { useCallback, useEffect, useRef, useState } from "react";

import { mindKey, nextMindDelayMs, pickMindThought, type Rand, type RecentThought } from "../lib/thinkingModel";

interface Drift {
  current: RecentThought | null;
  shown: string[];
}

export function useMindDrift(pool: RecentThought[], { enabled, rand }: { enabled: boolean; rand: Rand }): RecentThought | null {
  const [drift, setDrift] = useState<Drift>({ current: null, shown: [] });
  const driftRef = useRef(drift);
  const poolRef = useRef(pool);
  poolRef.current = pool;
  const randRef = useRef(rand);
  randRef.current = rand;

  const advance = useCallback(() => {
    const s = driftRef.current;
    const next = pickMindThought(poolRef.current, s.shown, randRef.current);
    const d: Drift = next ? { current: next, shown: [...s.shown, mindKey(next)].slice(-4) } : { current: null, shown: s.shown };
    driftRef.current = d;
    setDrift(d);
  }, []);

  // A new pool: keep the thought on screen while it is still in it, else pick one now.
  const poolKey = pool.map(mindKey).join("\n");
  const currentKey = drift.current ? mindKey(drift.current) : null;
  useEffect(() => {
    if (!enabled) return;
    const keys = new Set(poolRef.current.map(mindKey));
    if (!currentKey || !keys.has(currentKey)) advance();
  }, [enabled, poolKey, currentKey, advance]);

  // The next thought after a random while; one timer at a time.
  useEffect(() => {
    if (!enabled || !currentKey || poolRef.current.length < 2) return;
    const id = window.setTimeout(advance, nextMindDelayMs(randRef.current));
    return () => window.clearTimeout(id);
  }, [enabled, currentKey, poolKey, advance]);

  return enabled ? drift.current : null;
}
