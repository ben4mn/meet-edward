// R/G red for the missing Tailwind color tokens.
//
// The chat sidebar uses `text-secondary` and `surface-elevated`
// classes that don't resolve. Catch that by reading the config and
// asserting the keys exist. Pure module load — no DOM needed.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import config from "../tailwind.config.ts";

describe("tailwind.config.ts", () => {
  const colors = (config.theme?.extend?.colors ?? {}) as Record<string, unknown>;

  it("defines text-secondary", () => {
    assert.ok("text-secondary" in colors, "missing text-secondary token");
  });

  it("defines surface-elevated", () => {
    assert.ok("surface-elevated" in colors, "missing surface-elevated token");
  });

  it("keeps the terminal token", () => {
    assert.ok("terminal" in colors);
  });
});

// Redesign tokens (Sep 2026). Status colours are separate from Edward's
// accent: "overdue" and "problem" must not borrow the emerald that means
// "Edward acted". Each status has a soft fill for chips.
describe("status + soft tokens", () => {
  const colors = (config.theme?.extend?.colors ?? {}) as Record<string, unknown>;
  for (const t of ["ok", "warn", "bad", "info"]) {
    it(`defines ${t} and ${t}-soft`, () => {
      assert.ok(t in colors, `missing ${t}`);
      assert.ok(`${t}-soft` in colors, `missing ${t}-soft`);
    });
  }
  it("defines terminal-soft for Edward's own chips", () => {
    assert.ok("terminal-soft" in colors);
  });
});

// Calm-presence design system (2026-09-25, docs/design-system-2026-09-25.md).
// Every colour is a CSS variable with an `-rgb` channel twin so Tailwind's
// alpha modifiers compile: before this, `bg-terminal/20`, `border-border/70`
// and `bg-primary-bg/95` (128 uses in 35 files) compiled to nothing at all.
describe("design-system tokens", () => {
  const extend = (config.theme?.extend ?? {}) as Record<string, any>;
  const colors = (extend.colors ?? {}) as Record<string, unknown>;

  function flat(obj: Record<string, unknown>, prefix = ""): [string, unknown][] {
    return Object.entries(obj).flatMap(([k, v]) => {
      const key = k === "DEFAULT" ? prefix.replace(/-$/, "") : `${prefix}${k}`;
      return v && typeof v === "object" ? flat(v as Record<string, unknown>, `${key}-`) : [[key, v]];
    });
  }

  for (const k of ["surface-3", "terminal-ink", "terminal-strong", "glow"]) {
    it(`adds ${k}`, () => assert.ok(k in colors, `missing ${k}`));
  }

  it("maps every colour through an rgb channel variable so /NN compiles", () => {
    for (const [key, value] of flat(colors)) {
      if (typeof value === "function") continue;
      assert.match(
        String(value),
        /^rgb\(var\(--[a-z0-9-]+-rgb\) \/ <alpha-value>\)$/,
        `${key} = ${String(value)} cannot take an alpha modifier`,
      );
    }
  });

  it("border defaults to the hairline alpha and still scales with /NN", () => {
    const border = colors.border as (a: { opacityValue?: string }) => string;
    assert.equal(typeof border, "function", "border must be a colour function");
    const plain = border({ opacityValue: "var(--tw-border-opacity)" });
    assert.match(plain, /var\(--border-rgb\)/);
    assert.match(plain, /var\(--border-alpha\)/);
    const half = border({ opacityValue: "0.5" });
    assert.match(half, /var\(--border-alpha\)/);
    assert.match(half, /0\.5/);
  });

  it("has the type scale", () => {
    const fs = extend.fontSize as Record<string, [string, Record<string, string>]>;
    const want: Record<string, [string, string]> = {
      overline: ["11px", "14px"],
      caption: ["12px", "16px"],
      footnote: ["13px", "18px"],
      body: ["15px", "22px"],
      callout: ["15px", "22px"],
      title3: ["17px", "22px"],
      title2: ["20px", "26px"],
      title1: ["28px", "32px"],
      display: ["34px", "38px"],
    };
    for (const [name, [size, lh]] of Object.entries(want)) {
      assert.ok(fs?.[name], `missing text-${name}`);
      assert.equal(fs[name][0], size, `text-${name} size`);
      assert.equal(fs[name][1].lineHeight, lh, `text-${name} leading`);
    }
    assert.equal(fs.callout[1].fontWeight, "600");
    assert.equal(fs.title1[1].letterSpacing, "-0.02em");
  });

  it("has the radii", () => {
    const r = extend.borderRadius as Record<string, string>;
    assert.deepEqual(
      { xs: r.xs, sm: r.sm, md: r.md, lg: r.lg, xl: r.xl },
      { xs: "6px", sm: "8px", md: "12px", lg: "16px", xl: "22px" },
    );
  });

  it("has elevation and the presence glow", () => {
    const s = extend.boxShadow as Record<string, string>;
    for (const k of ["e1", "e2", "e3", "glow"]) assert.match(s?.[k] ?? "", /var\(--shadow-/, `shadow-${k}`);
  });

  it("has motion tokens", () => {
    assert.deepEqual(
      { ...extend.transitionDuration },
      { DEFAULT: "160ms", press: "90ms", fast: "160ms", base: "240ms", slow: "360ms" },
    );
    const ease = extend.transitionTimingFunction as Record<string, string>;
    assert.equal(ease.out, "cubic-bezier(.22,1,.36,1)");
    assert.equal(ease["in-out"], "cubic-bezier(.65,0,.35,1)");
    assert.equal(ease.in, "cubic-bezier(.4,0,1,1)");
    const kf = extend.keyframes as Record<string, unknown>;
    for (const k of ["fade-rise", "fade-in", "scale-in", "breathe", "shimmer"]) assert.ok(kf?.[k], `keyframes ${k}`);
    const anim = extend.animation as Record<string, string>;
    for (const k of ["fade-rise", "fade-in", "scale-in", "breathe", "shimmer"]) assert.ok(anim?.[k], `animate-${k}`);
  });

  it("only applies hover styles where hover exists (no sticky hover on iPhone)", () => {
    assert.equal((config as any).future?.hoverOnlyWhenSupported, true);
  });
});
