"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

import { fetchHubWho, postHubWho } from "./hubApi";
import {
  getPeople,
  getWho,
  hydrateWho,
  setPeople,
  setWho as storeSetWho,
  subscribeWho,
} from "./whoStore";

const NOBODY: readonly string[] = [];

// One household fetch per page load, however many components ask.
let household: Promise<void> | null = null;

function loadHousehold(): void {
  if (household) return;
  household = fetchHubWho()
    .then((r) => {
      setPeople(r.people);
    })
    .catch(() => {
      // Offline or signed out: keep what the panel remembers, ask again
      // the next time a screen mounts.
      household = null;
    });
}

/** The household, owner first ([] until known). */
export function usePeople(): readonly string[] {
  return useSyncExternalStore(subscribeWho, getPeople, () => NOBODY);
}

/**
 * Who is currently logging.
 *
 * Backed by a shared store, so switching person updates every component at
 * once — the toggle, the themed shell, and whatever is attributing a log.
 *
 * Deliberately sticky: it persists across reloads and never resets on
 * idle. A stale name corrected with one tap beats an "unknown" state that
 * blocks logging at 6am with a dog underfoot.
 *
 * The people come from the server (GET /api/hub/who) and are remembered
 * on the panel. A tap that changes the person is also posted to the server
 * (2026-09-24), which is what tells Edward who is at the hub. Hydrating
 * from storage does not post: a reload is not someone walking up to the
 * screen.
 */
export function useWho() {
  const who = useSyncExternalStore(
    subscribeWho,
    getWho,
    // Server render has no localStorage; match it on first client paint and
    // hydrate immediately after, or React reports a mismatch.
    () => "",
  );
  const people = usePeople();

  useEffect(() => {
    hydrateWho();
    loadHousehold();
  }, []);

  const setWho = useCallback((person: string) => {
    if (storeSetWho(person)) void postHubWho(person);
  }, []);

  return { who, setWho, people };
}
