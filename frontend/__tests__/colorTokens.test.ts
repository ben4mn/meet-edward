// Guardrails for the design tokens (docs/design-system-2026-09-25.md §7).
//
// Three bugs this pins, all found by the 2026-09-25 UX audit:
//  1. Colours were `var(--x)` strings, so every alpha modifier
//     (`bg-terminal/20`, `border-border/70`, `bg-primary-bg/95`: 128 uses in
//     35 files) compiled to NOTHING. The compiled CSS must contain them.
//  2. A global `[class*="bg-"] { transition: ... }` rule overrode every
//     transition utility on anything with a background.
//  3. Classes naming tokens that never existed (`bg-surface-bg`,
//     `text-text-tertiary`, `bg-accent-blue`, ...) rendered unstyled.
//     Every colour class must resolve to a configured key.

import { describe, it, before } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import postcss from "postcss";
import tailwindcss from "tailwindcss";
import resolveConfig from "tailwindcss/resolveConfig.js";

import config from "../tailwind.config.ts";

const ROOT = path.resolve(import.meta.dirname, "..");
const GLOBALS = fs.readFileSync(path.join(ROOT, "app/globals.css"), "utf8");

function walk(dir: string, out: string[] = []): string[] {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(tsx?|jsx?)$/.test(e.name)) out.push(p);
  }
  return out;
}
const SOURCES = ["app", "components", "lib"].flatMap((d) => walk(path.join(ROOT, d)));

// ---------------------------------------------------------------- compiled CSS
describe("compiled CSS", () => {
  let css = "";
  before(async () => {
    const result = await postcss([
      tailwindcss({
        ...(config as object),
        // The probe classes ride along as raw content so this checks that the
        // alpha channel COMPILES, not that some panel still happens to use them.
        content: [
          ...SOURCES.map((f) => ({ raw: fs.readFileSync(f, "utf8"), extension: path.extname(f).slice(1) })),
          { raw: '<div class="bg-terminal/20 border-border/70 bg-primary-bg/95"></div>', extension: "html" },
        ],
      } as any),
    ]).process(GLOBALS, { from: path.join(ROOT, "app/globals.css") });
    css = result.css;
  });

  for (const cls of ["bg-terminal\\/20", "border-border\\/70", "bg-primary-bg\\/95"]) {
    it(`contains .${cls.replace("\\", "")}`, () => {
      assert.ok(css.includes(`.${cls}`), `.${cls} did not compile`);
    });
  }

  it("border-border draws the hairline alpha, not an opaque line", () => {
    const rule = css.match(/\.border-border\s*\{[^}]*\}/)?.[0] ?? "";
    assert.match(rule, /--border-alpha/);
  });

  it("gives bare `border` the hairline by default", () => {
    const base = css.match(/\*,\s*::before,\s*::after\s*\{[^}]*border-color:[^;]*;/)?.[0] ?? "";
    assert.match(base, /--border-rgb/, "preflight border-color should be the hairline token");
  });
});

// ---------------------------------------------------------------- globals.css
describe("globals.css", () => {
  it("has no [class*= selectors (they override every transition utility)", () => {
    assert.doesNotMatch(GLOBALS, /\[class\*=/);
  });

  function block(selector: RegExp): string {
    const m = GLOBALS.match(selector);
    assert.ok(m, `no block for ${selector}`);
    return m![1];
  }

  it("defines every -rgb channel the Tailwind colours read, in light and dark", () => {
    const extend = (config.theme?.extend ?? {}) as Record<string, any>;
    const values: string[] = [];
    const collect = (o: Record<string, unknown>) => {
      for (const v of Object.values(o)) {
        if (typeof v === "function") values.push(String(v({ opacityValue: "1" })));
        else if (v && typeof v === "object") collect(v as Record<string, unknown>);
        else values.push(String(v));
      }
    };
    collect(extend.colors);
    collect(extend.borderColor);
    collect(extend.boxShadow);
    const vars = new Set(values.flatMap((s) => Array.from(s.matchAll(/--([a-z0-9-]+)/g), (m) => m[1])));
    assert.ok(vars.size > 10, "expected the config to read -rgb variables");
    const light = block(/:root\s*\{([^}]*)\}/);
    const dark = block(/\.dark\s*\{([^}]*)\}/);
    for (const v of vars) {
      assert.match(light, new RegExp(`--${v}:`), `:root is missing --${v}`);
      assert.match(dark, new RegExp(`--${v}:`), `.dark is missing --${v}`);
    }
  });

  it("keeps the raw var(--x) colours that login, AuthGuard and pane CSS read", () => {
    const dark = block(/\.dark\s*\{([^}]*)\}/);
    for (const v of ["primary-bg", "surface", "border", "terminal", "text-primary", "text-muted", "input-bg", "input-border"]) {
      assert.match(dark, new RegExp(`--${v}:`), `--${v}`);
    }
  });

  it("uses the spec's dark canvas and accent", () => {
    const dark = block(/\.dark\s*\{([^}]*)\}/);
    assert.match(dark, /--primary-bg:\s*#0a0e14/i);
    assert.match(dark, /--terminal:\s*#3ecf9e/i);
    const light = block(/:root\s*\{([^}]*)\}/);
    assert.match(light, /--primary-bg:\s*#f5f6f8/i);
    assert.match(light, /--terminal:\s*#047857/i);
  });

  it("has the base polish", () => {
    assert.match(GLOBALS, /-webkit-tap-highlight-color:\s*transparent/);
    assert.match(GLOBALS, /overscroll-behavior:\s*none/);
    assert.match(GLOBALS, /overscroll-behavior:\s*contain/);
    assert.match(GLOBALS, /color-scheme:\s*dark/);
    assert.match(GLOBALS, /color-scheme:\s*light/);
    assert.match(GLOBALS, /::selection/);
    assert.match(GLOBALS, /scrollbar-width:\s*thin/);
    assert.match(GLOBALS, /:focus-visible/);
    assert.match(GLOBALS, /"cv11"/);
    assert.match(GLOBALS, /-webkit-font-smoothing:\s*antialiased/);
  });

  it("pads the bottom by the home indicator or 8px, whichever is larger", () => {
    const rule = GLOBALS.match(/\.pb-safe\s*\{([^}]*)\}/)?.[1] ?? "";
    assert.match(rule, /max\(env\(safe-area-inset-bottom\),\s*8px\)/);
  });

  it("switches themes with a brief html.theme-transition class instead", () => {
    assert.match(GLOBALS, /html\.theme-transition/);
  });

  it("drops the dead avatar and landing CSS, keeps the pane buddy", () => {
    assert.doesNotMatch(GLOBALS, /\.edward-(blink|mouth|float|antenna|eye-glow|screen-flicker)/);
    assert.doesNotMatch(GLOBALS, /\.landing-/);
    assert.match(GLOBALS, /\.pane-avatar/);
  });
});

// ---------------------------------------------------------------- source sweep
const COLOR_PREFIX =
  /^(bg|text|border(?:-[trblxyse])?|ring-offset|ring|divide|outline|fill|stroke|placeholder|caret|accent|decoration|shadow)-(.+)$/;

function flattenKeys(obj: Record<string, unknown>, prefix = "", out = new Set<string>()): Set<string> {
  for (const [k, v] of Object.entries(obj)) {
    const key = k === "DEFAULT" ? prefix.replace(/-$/, "") : `${prefix}${k}`;
    if (v && typeof v === "object") flattenKeys(v as Record<string, unknown>, `${key}-`, out);
    else out.add(key);
  }
  return out;
}

/** Suffixes after a colour prefix that are not colours (sizes, widths, styles). */
function nonColourSuffixes(theme: Record<string, any>): Record<string, Set<string>> {
  const keys = (o: object | undefined) => Object.keys(o ?? {}).filter((k) => k !== "DEFAULT");
  const widths = keys(theme.borderWidth);
  const common = ["opacity"];
  return {
    text: new Set([
      ...keys(theme.fontSize),
      "left", "center", "right", "justify", "start", "end",
      "ellipsis", "clip", "wrap", "nowrap", "balance", "pretty", ...common,
    ]),
    bg: new Set([
      ...keys(theme.backgroundImage), ...keys(theme.backgroundSize), ...keys(theme.backgroundPosition),
      "fixed", "local", "scroll", "repeat", "no-repeat", "repeat-x", "repeat-y", "repeat-round", "repeat-space",
      "clip-border", "clip-padding", "clip-content", "clip-text", "origin-border", "origin-padding", "origin-content",
      ...common,
    ]),
    border: new Set([
      ...widths, "t", "r", "b", "l", "x", "y", "s", "e",
      "solid", "dashed", "dotted", "double", "hidden", "none", "collapse", "separate", ...common,
      // CSS property names inside inline `transition: "border-color .2s"` strings.
      "color", "radius", "width", "style",
    ]),
    ring: new Set([...keys(theme.ringWidth), "inset", ...common]),
    "ring-offset": new Set(keys(theme.ringOffsetWidth)),
    divide: new Set(["x", "y", "x-reverse", "y-reverse", "solid", "dashed", "dotted", "double", "none",
      ...widths.flatMap((w) => [`x-${w}`, `y-${w}`]), ...common]),
    outline: new Set(["none", "dashed", "dotted", "double", ...keys(theme.outlineWidth)]),
    fill: new Set(["none"]),
    stroke: new Set([...keys(theme.strokeWidth), "none"]),
    placeholder: new Set(common),
    caret: new Set(),
    accent: new Set(["auto"]),
    decoration: new Set(["solid", "double", "dotted", "dashed", "wavy", "auto", "from-font", "slice", "clone",
      ...keys(theme.textDecorationThickness)]),
    shadow: new Set(keys(theme.boxShadow)),
  };
}

function colourClassesIn(src: string): string[] {
  const found: string[] = [];
  // A class-ish token: optional variants, optional !/-, prefix-rest, bounded
  // by quote/space/brace so prose like `var(--text-primary)` is skipped.
  const re = /(?<=^|[\s"'`{(])((?:[\w\-\[\]&>*@.=]+:)*)!?-?((?:bg|text|border|ring|divide|outline|fill|stroke|placeholder|caret|accent|decoration|shadow)-(?:\[[^\]\s]*\](?:\/[\w.\[\]]+)?|[A-Za-z0-9_.\-/]+))(?=$|[\s"'`}),;])/gm;
  for (const m of src.matchAll(re)) found.push(m[2]);
  return found;
}

function unresolvedColourClasses(): Map<string, Set<string>> {
  const full = resolveConfig(config as any).theme as Record<string, any>;
  const colours = flattenKeys(full.colors);
  const skip = nonColourSuffixes(full);
  const bad = new Map<string, Set<string>>();
  for (const file of SOURCES) {
    const src = fs.readFileSync(file, "utf8");
    for (const cls of colourClassesIn(src)) {
      const m = cls.match(COLOR_PREFIX);
      if (!m) continue;
      const family = m[1].startsWith("border") ? "border" : m[1];
      let rest = m[2];
      if (rest.startsWith("[")) continue; // arbitrary value: the author chose it
      rest = rest.replace(/\/(\d+(\.\d+)?|\[[^\]]+\])$/, "");
      if (m[1] !== family && skip.border.has(rest)) continue; // border-t-2
      if (skip[family]?.has(rest)) continue;
      if (/^opacity-/.test(rest)) continue;
      if (colours.has(rest)) continue;
      const rel = path.relative(ROOT, file);
      if (!bad.has(cls)) bad.set(cls, new Set());
      bad.get(cls)!.add(rel);
    }
  }
  return bad;
}

describe("colour classes in app/ components/ lib/", () => {
  it("every one resolves to a configured colour key (or a Tailwind default)", () => {
    const bad = unresolvedColourClasses();
    const report = [...bad].map(([cls, files]) => `${cls}  (${[...files].join(", ")})`).join("\n");
    assert.equal(bad.size, 0, `classes that compile to nothing:\n${report}`);
  });

  it("the scanner itself catches an undefined token", () => {
    assert.deepEqual(colourClassesIn(`className="p-2 bg-surface-bg hover:text-text-tertiary/50"`), [
      "bg-surface-bg",
      "text-text-tertiary/50",
    ]);
  });

  // Spec §7: no raw zinc/gray/slate anywhere — use the tokens. The legacy
  // list that let old panels keep them emptied in the Sep 2026 restyle.
  it("no raw zinc/gray/slate palette anywhere (use the tokens)", () => {
    const hits: string[] = [];
    for (const file of SOURCES) {
      const rel = path.relative(ROOT, file);
      const src = fs.readFileSync(file, "utf8");
      for (const cls of colourClassesIn(src)) {
        if (/-(zinc|gray|slate|neutral|stone)-\d{2,3}\b/.test(cls)) hits.push(`${cls} (${rel})`);
      }
    }
    assert.deepEqual(hits, []);
  });
});
