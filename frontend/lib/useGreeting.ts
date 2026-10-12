"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchGreeting, fetchGreetingStream } from "@/lib/api";

/**
 * Edward's opening line, streamed. Shared by the empty chat and Today so
 * the first thing on screen is him speaking, not a template.
 *
 * UX pass 2026-09-26:
 *   * loading from the first render, so a page never flashes "Hey." (or an
 *     empty line) before the fetch has even started;
 *   * fetched again when the owner comes back to the app and the line is over 30
 *     minutes old or the part of the day changed ("Morning" at 7 pm
 *     is a lie). The old line stays until the new one's first words land.
 *   * `refresh()` (review 2026-09-26): fetched again at once, asking the
 *     server for a fresh line (`?fresh=1`) — Today calls it after the owner
 *     resolves something the greeting may name.
 *
 * No cleanup-based cancellation on purpose (see ChatWindow): StrictMode
 * double-invokes effects and an abort in cleanup would cancel the only
 * fetch. The ref guards the double-run; a stray setState after unmount is
 * a no-op.
 */

export type PartOfDay = "morning" | "afternoon" | "evening" | "night";

export const GREETING_MAX_AGE_MS = 30 * 60_000;

export function partOfDay(hour: number): PartOfDay {
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 17) return "afternoon";
  if (hour >= 17 && hour < 22) return "evening";
  return "night";
}

function localHour(ms: number, tz?: string): number {
  try {
    const h = new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "numeric", hourCycle: "h23" }).format(new Date(ms));
    return Number(h) % 24;
  } catch {
    return new Date(ms).getHours();
  }
}

/** Fetch again? Never fetched, older than half an hour, or another part of the day. */
export function shouldRefetchGreeting(fetchedAt: number | null, now: number, tz?: string): boolean {
  if (fetchedAt == null) return true;
  if (now - fetchedAt > GREETING_MAX_AGE_MS) return true;
  return partOfDay(localHour(fetchedAt, tz)) !== partOfDay(localHour(now, tz));
}

export interface GreetingOptions {
  /** The streamed line (tests). Default: GET /api/greeting/stream
   *  (`fresh`: ask the server not to answer from its cache). */
  stream?: (o?: { fresh?: boolean }) => AsyncIterable<string>;
  /** The one-shot line (tests). Default: POST /api/greeting. */
  once?: () => Promise<string>;
  /** The clock (tests). */
  now?: () => number;
  /** The zone the part of the day is read in; the browser's by default. */
  tz?: string;
}

export function useGreeting(enabled = true, opts: GreetingOptions = {}) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(enabled);
  const started = useRef(false);
  const inFlight = useRef(false);
  const fetchedAt = useRef<number | null>(null);
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const load = useCallback(async (fresh = false) => {
    if (inFlight.current) return;
    inFlight.current = true;
    const o = optsRef.current;
    const stream = o.stream ?? ((so?: { fresh?: boolean }) => fetchGreetingStream(undefined, so));
    const once = o.once ?? fetchGreeting;
    const now = o.now ?? Date.now;
    setLoading(true);
    try {
      let acc = "";
      for await (const chunk of stream(fresh ? { fresh: true } : undefined)) {
        acc += chunk;
        // The old line stays until the new one has words.
        if (acc) setText(acc);
      }
      if (!acc) {
        const line = await once();
        if (line) setText(line);
      }
    } catch {
      try {
        const line = await once();
        setText((prev) => line || prev || "Hey.");
      } catch {
        setText((prev) => prev || "Hey.");
      }
    } finally {
      fetchedAt.current = now();
      inFlight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    if (started.current) return;
    started.current = true;
    void load();
  }, [enabled, load]);

  useEffect(() => {
    if (!enabled) return;
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      const o = optsRef.current;
      const now = (o.now ?? Date.now)();
      if (fetchedAt.current == null) return; // the first load is still running
      if (shouldRefetchGreeting(fetchedAt.current, now, o.tz)) void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [enabled, load]);

  /** Fetch a fresh line now (after the owner resolved something it may name). */
  const refresh = useCallback(() => load(true), [load]);

  return { text, loading, refresh };
}
