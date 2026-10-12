"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchSettings,
  patchSettings,
} from "../lib/displayApi";
import {
  PANEL_KEYS,
  type DisplaySettings,
  type PanelKey,
} from "../lib/types";

const STORAGE_KEY = "edward.display.settings";

export const DEFAULT_SETTINGS: DisplaySettings = {
  accent: "green",
  time_of_day: "auto",
  density: "normal",
  avatar_size: "lg",
  show_panels: PANEL_KEYS.reduce(
    (acc, k) => {
      acc[k] = true;
      return acc;
    },
    {} as Record<PanelKey, boolean>,
  ),
  show_thinking: true,
  speak_aloud: true,
};

function readLocal(): DisplaySettings | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<DisplaySettings>;
    return { ...DEFAULT_SETTINGS, ...parsed,
      show_panels: { ...DEFAULT_SETTINGS.show_panels, ...(parsed.show_panels || {}) },
    };
  } catch {
    return null;
  }
}

function writeLocal(s: DisplaySettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    /* localStorage full or unavailable — non-fatal */
  }
}

/**
 * Local-first settings hook.
 *
 * - Hydrates from localStorage immediately (zero spinner).
 * - Reconciles with backend on mount and re-polls every 10s so a
 *   voice-driven mutation (`update_display_settings` tool call) shows up
 *   on the running display within a poll cycle.
 * - `update(patch)` writes optimistically to local + backend; the next poll
 *   reconciles. Backend is source of truth on conflict.
 */
export function useDisplaySettings() {
  const [settings, setSettings] = useState<DisplaySettings>(
    () => readLocal() ?? DEFAULT_SETTINGS,
  );
  const lastSyncedRef = useRef<string>(JSON.stringify(settings));

  const reconcile = useCallback(async () => {
    try {
      const remote = await fetchSettings();
      const merged: DisplaySettings = {
        ...DEFAULT_SETTINGS,
        ...remote,
        show_panels: {
          ...DEFAULT_SETTINGS.show_panels,
          ...(remote.show_panels || {}),
        },
      };
      const serialized = JSON.stringify(merged);
      if (serialized !== lastSyncedRef.current) {
        lastSyncedRef.current = serialized;
        setSettings(merged);
        writeLocal(merged);
      }
    } catch {
      // Stay on local copy if backend is unreachable.
    }
  }, []);

  useEffect(() => {
    reconcile();
    const id = setInterval(reconcile, 10_000);
    return () => clearInterval(id);
  }, [reconcile]);

  const update = useCallback(
    async (patch: Partial<DisplaySettings>) => {
      // Optimistic local update — show_panels is dict-merged like the
      // backend does. Everything else replaces.
      setSettings((prev) => {
        const next: DisplaySettings = {
          ...prev,
          ...patch,
          show_panels: patch.show_panels
            ? { ...prev.show_panels, ...patch.show_panels }
            : prev.show_panels,
        };
        writeLocal(next);
        return next;
      });
      try {
        const result = await patchSettings(patch);
        const merged: DisplaySettings = {
          ...DEFAULT_SETTINGS,
          ...result,
          show_panels: {
            ...DEFAULT_SETTINGS.show_panels,
            ...(result.show_panels || {}),
          },
        };
        lastSyncedRef.current = JSON.stringify(merged);
        setSettings(merged);
        writeLocal(merged);
      } catch {
        // Backend down — local state still reflects user intent. Next reconcile()
        // will diverge; we'll let the user see what they did.
      }
    },
    [],
  );

  return { settings, update };
}
