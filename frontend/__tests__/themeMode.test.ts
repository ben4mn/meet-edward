// R/G red for lib/themeMode.ts — Light / Dark / System (the You card).
//
// ThemeProvider knows two themes; "System" is a mode on top that follows
// prefers-color-scheme. The mode is stored beside the theme; the theme key
// keeps the last resolved theme so the boot script paints it with no flash.

import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { resolveTheme, readThemeMode, storeThemeMode, THEME_MODE_KEY, type ThemeMode } from "../lib/themeMode.ts";

describe("resolveTheme", () => {
  it("light and dark are themselves", () => {
    assert.equal(resolveTheme("light", true), "light");
    assert.equal(resolveTheme("dark", false), "dark");
  });
  it("system follows the device", () => {
    assert.equal(resolveTheme("system", true), "dark");
    assert.equal(resolveTheme("system", false), "light");
  });
});

describe("readThemeMode / storeThemeMode", () => {
  const mem = new Map<string, string>();
  const storage = {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, v),
    removeItem: (k: string) => void mem.delete(k),
  };
  beforeEach(() => mem.clear());

  it("falls back to the stored theme, then dark", () => {
    assert.equal(readThemeMode(storage), "dark");
    mem.set("edward-theme", "light");
    assert.equal(readThemeMode(storage), "light");
  });
  it("round-trips every mode", () => {
    for (const m of ["light", "dark", "system"] as ThemeMode[]) {
      storeThemeMode(m, storage);
      assert.equal(readThemeMode(storage), m);
    }
    assert.equal(THEME_MODE_KEY, "edward-theme-mode");
  });
  it("ignores junk and missing storage", () => {
    mem.set(THEME_MODE_KEY, "sepia");
    assert.equal(readThemeMode(storage), "dark");
    assert.equal(readThemeMode(null), "dark");
  });
});
