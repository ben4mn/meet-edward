"use client";

// Tiny module-level stores for the shell's global overlays, so the presence
// bar, the desktop header, the keyboard layer, the avatar menu and the You
// card can open the same palette / sign-out confirm without prop plumbing.

import { useSyncExternalStore } from "react";

function flagStore() {
  let value = false;
  const listeners = new Set<() => void>();
  const set = (v: boolean) => {
    if (value === v) return;
    value = v;
    for (const l of Array.from(listeners)) l();
  };
  return {
    get: () => value,
    open: () => set(true),
    close: () => set(false),
    toggle: () => set(!value),
    subscribe(l: () => void) {
      listeners.add(l);
      return () => {
        listeners.delete(l);
      };
    },
  };
}

/** The ⌘K command palette. */
export const paletteStore = flagStore();
/** "Sign out of Edward?" — one confirm sheet for every way to sign out. */
export const signOutStore = flagStore();

const serverFalse = () => false;

export function usePaletteOpen(): boolean {
  return useSyncExternalStore(paletteStore.subscribe, paletteStore.get, serverFalse);
}

export function useSignOutOpen(): boolean {
  return useSyncExternalStore(signOutStore.subscribe, signOutStore.get, serverFalse);
}

/** Open Edward's screen in its own named tab, reusing it when already open. */
export function openEdwardScreen(): void {
  if (typeof window === "undefined") return;
  const w = window.open("/screen", "edward-screen");
  w?.focus?.();
}
