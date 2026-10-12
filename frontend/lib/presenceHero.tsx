"use client";

// One live Edward per screen (docs/design-system-2026-09-25.md §1, §4, §7).
//
// The shell's presence bar (phone) and header (desktop) carry the live
// Edward on every page. A page that has its own hero Edward — Today's
// presence hero, chat's empty state, the Edward tab hero, the voice view —
// registers it here; while that hero is on screen the bar fades its own
// avatar and status out and the hero is the one who breathes. When the hero
// scrolls out (or unmounts) the bar shows him again, like an iOS large title
// collapsing into the navigation bar.
//
// A hero that can scroll (Today, chat's empty state, the Edward tab):
//
//   const ref = useRef<HTMLDivElement>(null);
//   const live = usePresenceHero(ref);            // boolean: "be the live one"
//   <div ref={ref}><EdwardAvatar size="xl" live={live} … /></div>
//   // usePresenceHero(ref, { enabled: messages.length === 0 }) to stand down
//
// A hero that covers the screen while it is up (the live voice view):
//
//   useRegisterPresenceHero(isOpen);
//
// The shell reads it with useHeroOnScreen() (or usePresenceHero() with no
// arguments). The presence sheet (tap Edward anywhere) has its own live
// Edward, so while it is open neither a hero nor the bar breathes; anything
// can open it:
//
//   const sheet = usePresenceSheet();  // { open, close, isOpen }
//
// State is one module-level store (no provider needed); PresenceHeroProvider
// is a pass-through for callers that wrap with it. Pure decisions
// (heroVisibleFrom, nextHeroes, anyHeroOnScreen) are exported for the tests.

import React, { useEffect, useId, useState, useSyncExternalStore, type ReactNode, type RefObject } from "react";

/** A hero counts as on screen when at least this much of him shows. */
export const HERO_MIN_RATIO = 0.4;

/** The phone presence bar's drawn height: a hero tucked behind it is gone. */
export const HERO_TOP_INSET_PX = 56;

export interface HeroEntry {
  isIntersecting: boolean;
  intersectionRatio: number;
}

export function heroVisibleFrom(entry: HeroEntry): boolean {
  return entry.isIntersecting && entry.intersectionRatio >= HERO_MIN_RATIO;
}

export type HeroMap = ReadonlyMap<string, boolean>;

/** Record hero `id` as visible / not visible, or remove it (`null`, unmounted).
 *  Returns the same map when nothing changed so nothing re-renders. */
export function nextHeroes(map: HeroMap, id: string, visible: boolean | null): HeroMap {
  if (visible === null) {
    if (!map.has(id)) return map;
    const next = new Map(map);
    next.delete(id);
    return next;
  }
  if (map.get(id) === visible) return map;
  const next = new Map(map);
  next.set(id, visible);
  return next;
}

export function anyHeroOnScreen(map: HeroMap): boolean {
  let on = false;
  map.forEach((v) => {
    if (v) on = true;
  });
  return on;
}

// ---------------------------------------------------------------- the store

type Listener = () => void;

let heroes: HeroMap = new Map();
let sheetOpen = false;
const listeners = new Set<Listener>();

function emit() {
  for (const l of Array.from(listeners)) l();
}

export const heroStore = {
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  /** Is any hero on screen? */
  active(): boolean {
    return anyHeroOnScreen(heroes);
  },
  /** A hero is visible (true), scrolled away (false) or gone (null). */
  report(id: string, visible: boolean | null): void {
    const next = nextHeroes(heroes, id, visible);
    if (next === heroes) return;
    heroes = next;
    emit();
  },
  sheetOpen(): boolean {
    return sheetOpen;
  },
  openSheet(): void {
    if (sheetOpen) return;
    sheetOpen = true;
    emit();
  },
  closeSheet(): void {
    if (!sheetOpen) return;
    sheetOpen = false;
    emit();
  },
};

const serverFalse = () => false;

function useHeroActive(): boolean {
  return useSyncExternalStore(heroStore.subscribe, heroStore.active, serverFalse);
}

function useSheetOpen(): boolean {
  return useSyncExternalStore(heroStore.subscribe, heroStore.sheetOpen, serverFalse);
}

// ---------------------------------------------------------------- hooks

/** A pass-through for callers that wrap a subtree with it; the store is global. */
export function PresenceHeroProvider({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

/** A hero that is on screen whenever `on` (the voice view while it is open). */
export function useRegisterPresenceHero(on: boolean = true): void {
  const id = useId();
  useEffect(() => {
    heroStore.report(id, on ? true : null);
    return () => heroStore.report(id, null);
  }, [id, on]);
}

/**
 * With a ref: register the page's hero Edward and return whether he should
 * be the live one (`<EdwardAvatar live={…} />`) — true while he is on screen
 * and the presence sheet is closed. `enabled: false` stands him down (e.g. a
 * chat that has messages) so the bar takes over at once.
 *
 * With no arguments: whether any hero is on screen (the shell's question).
 */
export function usePresenceHero(): boolean;
export function usePresenceHero(ref: RefObject<Element | null>, opts?: { enabled?: boolean }): boolean;
export function usePresenceHero(ref?: RefObject<Element | null>, opts: { enabled?: boolean } = {}): boolean {
  const registering = ref !== undefined;
  const enabled = registering && (opts.enabled ?? true);
  const id = useId();
  const active = useHeroActive();
  const sheet = useSheetOpen();
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    if (!enabled || !ref) return;
    const el = ref.current;
    const IO =
      typeof window !== "undefined"
        ? (globalThis as { IntersectionObserver?: typeof IntersectionObserver }).IntersectionObserver
        : undefined;
    // Mounted and drawn is "on screen" until the observer says otherwise.
    setVisible(true);
    heroStore.report(id, true);
    if (!el || typeof IO !== "function") {
      return () => heroStore.report(id, null);
    }
    const observer = new IO(
      (entries) => {
        const entry = entries[entries.length - 1];
        if (!entry) return;
        const v = heroVisibleFrom(entry);
        setVisible(v);
        heroStore.report(id, v);
      },
      { rootMargin: `-${HERO_TOP_INSET_PX}px 0px 0px 0px`, threshold: [0, HERO_MIN_RATIO, 1] },
    );
    observer.observe(el);
    return () => {
      observer.disconnect();
      heroStore.report(id, null);
    };
  }, [enabled, id, ref]);

  if (!registering) return active;
  if (!enabled) return false;
  return visible && !sheet;
}

/** For the shell: is a page's hero Edward on screen right now? */
export function useHeroOnScreen(): boolean {
  return useHeroActive();
}

/** Open / close the presence sheet from anywhere (a hero tap, the bar). */
export function usePresenceSheet(): { open: () => void; close: () => void; isOpen: boolean } {
  const isOpen = useSheetOpen();
  return { open: heroStore.openSheet, close: heroStore.closeSheet, isOpen };
}
