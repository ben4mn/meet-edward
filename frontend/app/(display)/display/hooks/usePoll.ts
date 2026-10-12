"use client";

import { useEffect, useRef, useState } from "react";

export interface PollState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  lastSuccessAt: number | null;
  refetch: () => void;
}

/**
 * Poll a fetcher on a fixed interval.
 *
 * - Keeps stale data on screen across fetches (no flicker).
 * - Pauses when the tab is hidden so we don't burn CPU on a background tab.
 * - `refetch` triggers an immediate fetch (useful right after a mutation).
 */
export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  deps: React.DependencyList = [],
): PollState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastSuccessAt, setLastSuccessAt] = useState<number | null>(null);
  const tickRef = useRef(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const run = async () => {
    const myTick = ++tickRef.current;
    try {
      const next = await fetcherRef.current();
      // Drop stale responses if a newer fetch already raced ahead.
      if (myTick !== tickRef.current) return;
      setData(next);
      setError(null);
      setLastSuccessAt(Date.now());
    } catch (e) {
      if (myTick !== tickRef.current) return;
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      if (myTick === tickRef.current) setLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    let id: ReturnType<typeof setInterval> | null = null;

    const start = () => {
      if (id !== null) return;
      run();
      id = setInterval(() => {
        if (!cancelled) run();
      }, intervalMs);
    };

    const stop = () => {
      if (id !== null) clearInterval(id);
      id = null;
    };

    const onVis = () => {
      if (document.visibilityState === "visible") start();
      else stop();
    };

    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVis);

    return () => {
      cancelled = true;
      stop();
      document.removeEventListener("visibilitychange", onVis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps]);

  return { data, error, loading, lastSuccessAt, refetch: run };
}
