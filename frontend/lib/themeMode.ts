// Light / Dark / System (the You card). ThemeProvider (lib/theme.tsx) knows
// two themes; "System" is a mode on top of it that follows the device's
// prefers-color-scheme. The mode is stored beside the theme; the theme key
// keeps the last resolved theme, so the boot script paints it with no flash.
// components/layout/ThemeModeSync.tsx keeps a System choice in step.

import { useCallback, useEffect, useState } from "react";
import { useTheme } from "./theme";
import { THEME_MODE_KEY, THEME_STORAGE_KEY, type Theme } from "./themeColors";

export type ThemeMode = Theme | "system";

export { THEME_MODE_KEY };
/** Window event: the mode changed (so every reader re-reads it). */
export const THEME_MODE_EVENT = "edward:theme-mode";

export interface ModeStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function resolveTheme(mode: ThemeMode, prefersDark: boolean): Theme {
  if (mode === "system") return prefersDark ? "dark" : "light";
  return mode;
}

function browserStorage(): ModeStorage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readThemeMode(storage: ModeStorage | null | undefined = browserStorage()): ThemeMode {
  if (!storage) return "dark";
  try {
    const m = storage.getItem(THEME_MODE_KEY);
    if (m === "light" || m === "dark" || m === "system") return m;
    const t = storage.getItem(THEME_STORAGE_KEY);
    return t === "light" || t === "dark" ? t : "dark";
  } catch {
    return "dark";
  }
}

export function storeThemeMode(mode: ThemeMode, storage: ModeStorage | null | undefined = browserStorage()): void {
  if (!storage) return;
  try {
    storage.setItem(THEME_MODE_KEY, mode);
  } catch {
    /* blocked storage: the choice lasts this page only */
  }
}

// ---------------------------------------------------------------- React

function prefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/**
 * The theme mode for the You card, the avatar menu and the palette:
 * `mode` is what the owner chose (light / dark / system), `theme` what is on
 * screen. `setMode` stores the choice and applies it through ThemeProvider
 * (inside which this must be used).
 */
export function useThemeMode(): { mode: ThemeMode; theme: Theme; setMode: (m: ThemeMode) => void } {
  const { theme, setTheme } = useTheme();
  const [mode, setModeState] = useState<ThemeMode>("dark");

  useEffect(() => {
    setModeState(readThemeMode());
    const on = () => setModeState(readThemeMode());
    window.addEventListener(THEME_MODE_EVENT, on);
    return () => window.removeEventListener(THEME_MODE_EVENT, on);
  }, []);

  const setMode = useCallback(
    (m: ThemeMode) => {
      storeThemeMode(m);
      setModeState(m);
      setTheme(resolveTheme(m, prefersDark()));
      window.dispatchEvent(new window.CustomEvent(THEME_MODE_EVENT, { detail: m }));
    },
    [setTheme],
  );

  return { mode, theme, setMode };
}

/** Keeps a "System" choice in step with the device (mounted once in the shell,
 *  inside ThemeProvider). Runs after ThemeProvider has adopted the stored theme. */
export function useSystemThemeSync(): void {
  const { setTheme, mounted, theme } = useTheme();
  useEffect(() => {
    if (!mounted || typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      if (readThemeMode() !== "system") return;
      const want = resolveTheme("system", mql.matches);
      if (want !== theme) setTheme(want);
    };
    apply();
    mql.addEventListener?.("change", apply);
    window.addEventListener(THEME_MODE_EVENT, apply);
    return () => {
      mql.removeEventListener?.("change", apply);
      window.removeEventListener(THEME_MODE_EVENT, apply);
    };
  }, [mounted, setTheme, theme]);
}
