"use client";

// React side of lib/sharedPoll.ts: subscribe to the one store for a URL.
//
//   useSignals()  — /api/display/signals, every 2 s, shared by the alert
//                   strip and Edward (they each polled every 2 s before).
//   useMindNow()  — /api/mind/now, every 10 s: a running wake, what waits on
//                   the owner, the quota and his hands, so /screen and the hub can
//                   say "Thinking · morning wake" truthfully.

import { useSyncExternalStore } from "react";

import { fetchMindNow, type MindNow } from "@/lib/mindNowApi";

import { fetchSignals } from "../lib/displayApi";
import {
  SIGNALS_POLL_MS,
  SIGNALS_URL,
  sharedPollStore,
  type PollSnapshot,
} from "../lib/sharedPoll";
import type { SignalsResponse } from "../lib/types";

export interface SharedPoll<T> extends PollSnapshot<T> {
  refetch: () => void;
}

export function useSharedPoll<T>(
  key: string,
  fetcher: () => Promise<T>,
  intervalMs: number,
): SharedPoll<T> {
  const store = sharedPollStore(key, fetcher, intervalMs);
  const snap = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  return { ...snap, refetch: () => void store.refetch() };
}

export function useSignals(): SharedPoll<SignalsResponse> {
  return useSharedPoll(SIGNALS_URL, fetchSignals, SIGNALS_POLL_MS);
}

export const MIND_NOW_URL = "/api/mind/now";
/** The app's presence bar polls every 20 s; a wall screen can afford 10. */
export const SCREEN_MIND_NOW_POLL_MS = 10_000;

/** ok → the body; an older backend without the route → null (nothing to
 *  say, not a failure); anything else throws so the store counts it. */
export async function fetchMindNowForScreen(): Promise<MindNow | null> {
  const result = await fetchMindNow();
  if (result.status === "ok") return result.data;
  if (result.status === "missing") return null;
  throw new Error(result.status === "unreachable" ? result.error : result.status);
}

export function useMindNow(): SharedPoll<MindNow | null> {
  return useSharedPoll(MIND_NOW_URL, fetchMindNowForScreen, SCREEN_MIND_NOW_POLL_MS);
}
