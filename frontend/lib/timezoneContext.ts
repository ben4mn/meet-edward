"use client";

// Reading the user's timezone, apart from the provider (2026-09-26 perf
// pass): TimezoneProvider (./useTimezone.tsx, mounted by the layouts) loads
// it through lib/api and re-exports these hooks. A page that only reads the
// zone (Today) imports them from here and does not carry a second copy of
// the provider.

import { createContext, useCallback, useContext } from "react";

type Ctx = {
  tz: string;
  setTz: (tz: string) => void;
};

export const TimezoneContext = createContext<Ctx>({ tz: "", setTz: () => {} });

export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Read the user's configured IANA timezone. Falls back to the browser's
 * resolved zone before settings have loaded, and "UTC" as last resort. */
export function useUserTimezone(): string {
  const { tz } = useContext(TimezoneContext);
  if (tz) return tz;
  if (typeof window !== "undefined") return browserTimezone();
  return "UTC";
}

/** Imperative setter so the settings picker can broadcast a new tz to the
 * rest of the tree without re-fetching. */
export function useSetUserTimezone(): (tz: string) => void {
  const { setTz } = useContext(TimezoneContext);
  return useCallback((next: string) => setTz(next), [setTz]);
}
