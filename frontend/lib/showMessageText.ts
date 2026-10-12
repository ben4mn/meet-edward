"use client";

// "Show message text" on the heartbeat panel: off by default, because that
// page is other people's words. One store on one localStorage key, so the
// switch governs the digest, the full log and the event detail alike —
// flipping it in one place re-renders every reader on the page
// (useSyncExternalStore). Pinned in __tests__/showMessageText.test.tsx.

import { useCallback, useSyncExternalStore } from "react";

export const SHOW_TEXT_KEY = "edward:heartbeatShowText";

const listeners = new Set<() => void>();

export function readShowText(): boolean {
  try {
    return localStorage.getItem(SHOW_TEXT_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeShowText(next: boolean): void {
  try {
    localStorage.setItem(SHOW_TEXT_KEY, next ? "1" : "0");
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
  memory = next;
  for (const l of Array.from(listeners)) l();
}

// The last value written, so a browser without storage still flips.
let memory: boolean | null = null;

function snapshot(): boolean {
  try {
    const raw = localStorage.getItem(SHOW_TEXT_KEY);
    if (raw === "1") return true;
    if (raw === "0") return false;
  } catch {
    /* fall through */
  }
  return memory ?? false;
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  const onStorage = (e: StorageEvent) => {
    if (e.key === SHOW_TEXT_KEY) cb();
  };
  if (typeof window !== "undefined") window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(cb);
    if (typeof window !== "undefined") window.removeEventListener("storage", onStorage);
  };
}

export function useShowMessageText(): [boolean, (next: boolean) => void] {
  const show = useSyncExternalStore(subscribe, snapshot, () => false);
  const set = useCallback((next: boolean) => writeShowText(next), []);
  return [show, set];
}
