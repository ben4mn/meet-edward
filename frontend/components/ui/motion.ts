"use client";

// Motion tokens for framer-motion, mirroring the CSS ones in globals.css
// (docs/design-system-2026-09-25.md §2 "Radius · elevation · motion").
// Durations are seconds here (framer's unit); exits run at 0.7x.

import { useSyncExternalStore } from "react";

export const DURATION = { press: 0.09, fast: 0.16, base: 0.24, slow: 0.36 } as const;
export const EXIT_FACTOR = 0.7;

export const EASE_OUT = [0.22, 1, 0.36, 1] as const;
export const EASE_IN_OUT = [0.65, 0, 0.35, 1] as const;
export const EASE_IN = [0.4, 0, 1, 1] as const;

/** Sheets and drawers. */
export const SPRING_SHEET = { type: "spring", stiffness: 420, damping: 36 } as const;
/** The dock's active pill and other small, snappy things (a switch knob). */
export const SPRING_PILL = { type: "spring", stiffness: 600, damping: 40 } as const;

/** Reduced motion: an opacity fade of 120ms or less, nothing else. */
export const REDUCED_FADE = { duration: 0.12, ease: "linear" } as const;

const noopSubscribe = () => () => {};

/** True once rendering on the client (false during SSR and hydration). */
export function useIsClient(): boolean {
  return useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );
}

function canMatch(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function";
}

/** A media query as state. `false` on the server and where matchMedia is
 *  missing (jsdom), so callers must treat `false` as the safe default. */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (!canMatch()) return () => {};
      const mql = window.matchMedia(query);
      mql.addEventListener?.("change", onChange);
      return () => mql.removeEventListener?.("change", onChange);
    },
    () => (canMatch() ? window.matchMedia(query).matches : false),
    () => false,
  );
}

export function usePrefersReducedMotion(): boolean {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}

/** md+ in Tailwind terms: the centred-dialog, hover-capable layout. */
export function useIsDesktop(): boolean {
  return useMediaQuery("(min-width: 768px)");
}
