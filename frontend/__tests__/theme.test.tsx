// Theme switching, the PWA status-bar colour and the manifest
// (docs/design-system-2026-09-25.md §5 "iOS polish").
//
// The theme-color was a fixed emerald (#10b981) whatever the theme, so the
// iPhone status bar was a green stripe over a near-black app. It is now the
// canvas colour of the theme on screen, updated when Ellis toggles.
// Switching themes used to rely on a global `[class*="bg-"]` transition rule
// that overrode every component's own transitions; a brief
// `html.theme-transition` class does the cross-fade instead.

import { describe, it, beforeEach, afterEach, mock } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import React from "react";
import { render, fireEvent, act } from "@testing-library/react";

import {
  THEME_CANVAS,
  THEME_TRANSITION_MS,
  applyTheme,
  themeBootScript,
  themeColorViewport,
} from "../lib/themeColors.ts";
import { ThemeProvider, ThemeToggle, useTheme } from "../lib/theme.tsx";

const ROOT = path.resolve(import.meta.dirname, "..");

// Node 25 has its own global `localStorage` that is inert without
// --localstorage-file; give the page a real in-memory one.
class MemoryStorage {
  private m = new Map<string, string>();
  get length() {
    return this.m.size;
  }
  key(i: number) {
    return Array.from(this.m.keys())[i] ?? null;
  }
  getItem(k: string) {
    return this.m.has(k) ? this.m.get(k)! : null;
  }
  setItem(k: string, v: string) {
    this.m.set(k, String(v));
  }
  removeItem(k: string) {
    this.m.delete(k);
  }
  clear() {
    this.m.clear();
  }
}
const storage = new MemoryStorage();
Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true, writable: true });
Object.defineProperty(window, "localStorage", { value: storage, configurable: true, writable: true });

function resetDocument() {
  document.documentElement.className = "";
  document.head.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.remove());
  storage.clear();
}

describe("theme colours", () => {
  it("are the spec's canvas colours", () => {
    assert.deepEqual(THEME_CANVAS, { dark: "#0a0e14", light: "#f5f6f8" });
  });

  it("give Next one theme-color per system scheme", () => {
    assert.deepEqual(themeColorViewport(), [
      { media: "(prefers-color-scheme: dark)", color: "#0a0e14" },
      { media: "(prefers-color-scheme: light)", color: "#f5f6f8" },
    ]);
  });
});

describe("applyTheme", () => {
  beforeEach(resetDocument);
  afterEach(() => mock.timers.reset());

  it("sets the class and every theme-color meta to the canvas", () => {
    for (const media of ["(prefers-color-scheme: dark)", "(prefers-color-scheme: light)"]) {
      const m = document.createElement("meta");
      m.name = "theme-color";
      m.media = media;
      m.content = "#10b981";
      document.head.appendChild(m);
    }
    applyTheme(document, "light");
    assert.equal(document.documentElement.classList.contains("dark"), false);
    const metas = Array.from(document.head.querySelectorAll('meta[name="theme-color"]'));
    assert.deepEqual(metas.map((m) => m.getAttribute("content")), ["#f5f6f8", "#f5f6f8"]);

    applyTheme(document, "dark");
    assert.equal(document.documentElement.classList.contains("dark"), true);
    assert.deepEqual(metas.map((m) => m.getAttribute("content")), ["#0a0e14", "#0a0e14"]);
  });

  it("creates the theme-color meta when there is none", () => {
    applyTheme(document, "dark");
    assert.equal(document.head.querySelector('meta[name="theme-color"]')?.getAttribute("content"), "#0a0e14");
  });

  it("cross-fades with html.theme-transition only when asked, then removes it", () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    applyTheme(document, "dark");
    assert.equal(document.documentElement.classList.contains("theme-transition"), false);
    applyTheme(document, "light", { animate: true });
    assert.equal(document.documentElement.classList.contains("theme-transition"), true);
    mock.timers.tick(THEME_TRANSITION_MS + 1);
    assert.equal(document.documentElement.classList.contains("theme-transition"), false);
  });
});

describe("themeBootScript (runs in <head> before paint)", () => {
  beforeEach(resetDocument);

  it("applies the stored theme, defaulting to dark", () => {
    new Function(themeBootScript())();
    assert.equal(document.documentElement.classList.contains("dark"), true);
    assert.equal(document.head.querySelector('meta[name="theme-color"]')?.getAttribute("content"), "#0a0e14");

    resetDocument();
    localStorage.setItem("edward-theme", "light");
    new Function(themeBootScript())();
    assert.equal(document.documentElement.classList.contains("dark"), false);
    assert.equal(document.head.querySelector('meta[name="theme-color"]')?.getAttribute("content"), "#f5f6f8");
  });
});

describe("ThemeProvider", () => {
  beforeEach(resetDocument);
  afterEach(() => mock.timers.reset());

  function Probe() {
    const { theme, setTheme } = useTheme();
    return (
      <button type="button" data-testid="probe" onClick={() => setTheme("light")}>
        {theme}
      </button>
    );
  }

  it("toggles class, storage and the status-bar colour, with a brief cross-fade", () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    const { getByRole, getByTestId } = render(
      <ThemeProvider>
        <ThemeToggle />
        <Probe />
      </ThemeProvider>,
    );
    assert.equal(getByTestId("probe").textContent, "dark");
    assert.equal(document.documentElement.classList.contains("theme-transition"), false, "no fade on first paint");

    fireEvent.click(getByRole("button", { name: /switch to light/i }));
    assert.equal(document.documentElement.classList.contains("dark"), false);
    assert.equal(localStorage.getItem("edward-theme"), "light");
    assert.equal(document.head.querySelector('meta[name="theme-color"]')?.getAttribute("content"), "#f5f6f8");
    assert.equal(document.documentElement.classList.contains("theme-transition"), true);
    act(() => mock.timers.tick(THEME_TRANSITION_MS + 1));
    assert.equal(document.documentElement.classList.contains("theme-transition"), false);

    fireEvent.click(getByRole("button", { name: /switch to dark/i }));
    assert.equal(document.documentElement.classList.contains("dark"), true);
    assert.equal(getByTestId("probe").textContent, "dark");
  });
});

describe("after a navigation (2026-09-26 review)", () => {
  beforeEach(resetDocument);

  it("a theme-color meta Next re-inserts takes Ellis's theme, not the device's", async () => {
    localStorage.setItem("edward-theme", "light");
    render(
      <ThemeProvider>
        <p>app</p>
      </ThemeProvider>,
    );
    // Next's app router remounts <head> on a segment change and puts its
    // per-scheme defaults back.
    const m = document.createElement("meta");
    m.name = "theme-color";
    m.media = "(prefers-color-scheme: dark)";
    m.content = "#0a0e14";
    document.head.appendChild(m);
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(m.getAttribute("content"), "#f5f6f8");
  });
});

describe("System mode on a cold open (2026-09-26 review)", () => {
  const realMatchMedia = window.matchMedia;
  const prefersDark = (dark: boolean) => {
    window.matchMedia = ((q: string) => ({
      matches: /prefers-color-scheme:\s*dark/.test(q) ? dark : false,
      media: q,
      onchange: null,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  };
  beforeEach(resetDocument);
  afterEach(() => {
    window.matchMedia = realMatchMedia;
  });

  it("the boot script paints the device's appearance, not the last resolved theme", () => {
    localStorage.setItem("edward-theme", "dark");
    localStorage.setItem("edward-theme-mode", "system");
    prefersDark(false);
    new Function(themeBootScript())();
    assert.equal(document.documentElement.classList.contains("dark"), false);
    assert.equal(document.head.querySelector('meta[name="theme-color"]')?.getAttribute("content"), "#f5f6f8");
  });

  it("an explicit choice still wins over the device", () => {
    localStorage.setItem("edward-theme", "dark");
    localStorage.setItem("edward-theme-mode", "dark");
    prefersDark(false);
    new Function(themeBootScript())();
    assert.equal(document.documentElement.classList.contains("dark"), true);
  });

  it("ThemeProvider adopts the same resolution with no cross-fade", () => {
    localStorage.setItem("edward-theme", "dark");
    localStorage.setItem("edward-theme-mode", "system");
    prefersDark(false);
    function Probe() {
      return <span data-testid="t">{useTheme().theme}</span>;
    }
    const { getByTestId } = render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    assert.equal(getByTestId("t").textContent, "light");
    assert.equal(document.documentElement.classList.contains("dark"), false);
    assert.equal(document.documentElement.classList.contains("theme-transition"), false);
  });
});

describe("app/layout.tsx", () => {
  const src = fs.readFileSync(path.join(ROOT, "app/layout.tsx"), "utf8");
  it("takes theme-color from the canvas tokens, not a fixed emerald", () => {
    assert.doesNotMatch(src, /#10b981/i);
    assert.match(src, /themeColorViewport\(\)/);
    assert.match(src, /themeBootScript\(\)/);
  });
  it("mounts the toast system for every route", () => {
    assert.match(src, /<ToastProvider>/);
  });
});

describe("public/manifest.json", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "public/manifest.json"), "utf8"));

  it("uses the dark canvas for the splash and the title bar", () => {
    assert.equal(manifest.theme_color, "#0a0e14");
    assert.equal(manifest.background_color, "#0a0e14");
  });

  it("has an id and a scope", () => {
    assert.equal(manifest.id, "/today");
    assert.equal(manifest.scope, "/");
    assert.equal(manifest.start_url, "/today");
  });

  it("lists `any` and `maskable` icons as separate entries", () => {
    for (const icon of manifest.icons) {
      assert.ok(["any", "maskable"].includes(icon.purpose), `${icon.src}: purpose "${icon.purpose}"`);
    }
    const maskable = manifest.icons.filter((i: { purpose: string }) => i.purpose === "maskable");
    assert.ok(maskable.some((i: { sizes: string }) => i.sizes === "512x512"));
    assert.ok(maskable.some((i: { sizes: string }) => i.sizes === "192x192"));
  });

  it("has shortcuts to Today and a new chat", () => {
    const byUrl = Object.fromEntries(manifest.shortcuts.map((s: { url: string; name: string }) => [s.url, s.name]));
    assert.equal(byUrl["/today"], "Today");
    assert.equal(byUrl["/chat"], "New chat");
    for (const s of manifest.shortcuts) {
      assert.ok(s.icons?.length, `${s.name} needs an icon`);
    }
  });
});
