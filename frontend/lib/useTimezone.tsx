"use client";

import { useEffect, useState, type ReactNode } from "react";
import { getSettings, updateSettings } from "./api";
import { TimezoneContext, browserTimezone } from "./timezoneContext";

// The hooks live in ./timezoneContext (2026-09-26 perf pass); re-exported
// here so every existing import keeps working.
export { useSetUserTimezone, useUserTimezone } from "./timezoneContext";

// A fresh install's settings row holds TIMEZONE from the backend's .env
// (setup.sh writes it) or, when that is unset, the server's fallback
// (services/timezone_utils.py FALLBACK_TZ: "America/Chicago"; "UTC" if that
// fallback changes). A row still on the fallback is replaced with the
// browser's own zone on the first load; a zone setup.sh chose is kept.
// Before settings load, and when they hold none, the browser's zone is the
// fallback (useUserTimezone does the same).
const SERVER_DEFAULT_TZS: ReadonlySet<string> = new Set(["America/Chicago", "UTC"]);
const AUTODETECT_FLAG_KEY = "tz_autodetected_v1";

export function TimezoneProvider({ children }: { children: ReactNode }) {
  const [tz, setTz] = useState<string>("");

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const s = await getSettings();
        if (!mounted) return;
        const loaded = s.timezone || browserTimezone();
        setTz(loaded);

        // One-time auto-detect on first load with browser zone, only if the
        // backend still holds the bare default. Guarded by localStorage so a
        // user who deliberately picks the default zone later isn't
        // overridden on next session.
        const alreadyDetected =
          typeof window !== "undefined" &&
          window.localStorage.getItem(AUTODETECT_FLAG_KEY) === "true";
        if (alreadyDetected) return;
        if (!SERVER_DEFAULT_TZS.has(loaded)) {
          window.localStorage.setItem(AUTODETECT_FLAG_KEY, "true");
          return;
        }
        const browserTz = browserTimezone();
        if (!browserTz || browserTz === loaded) {
          window.localStorage.setItem(AUTODETECT_FLAG_KEY, "true");
          return;
        }
        try {
          const updated = await updateSettings({ timezone: browserTz });
          if (mounted) setTz(updated.timezone || browserTz);
          window.localStorage.setItem(AUTODETECT_FLAG_KEY, "true");
        } catch (err) {
          // Non-fatal — leave the existing zone and try again next session.
          console.warn("timezone auto-detect PATCH failed:", err);
        }
      } catch (err) {
        // Settings fetch can fail on logged-out pages; silently fall back.
        console.warn("timezone load failed, using browser fallback:", err);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <TimezoneContext.Provider value={{ tz, setTz }}>
      {children}
    </TimezoneContext.Provider>
  );
}
