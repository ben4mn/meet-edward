// The stage's motion rules, in display.css (2026-09-28; the orbit rings'
// reduced-motion pins moved here when the rings went).
//
// "Only one thing loops: his breath." On the stage nothing else runs
// forever — no pinging dots, no shimmer, no marquee — pieces enter once in
// under a second, and under prefers-reduced-motion every entrance is a fade
// of 120 ms or less. No raw zinc/gray/slate and no [class*=] selectors.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../app/(display)/display/display.css", import.meta.url), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "");

/** Declarations of the first rule whose selector list includes `selector` exactly. */
function ruleFor(source: string, selector: string): string | null {
  const re = /([^{}@]+)\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    const selectors = m[1].split(",").map((s) => s.trim());
    if (selectors.includes(selector)) return m[2];
  }
  return null;
}

/** The body of `@media (prefers-reduced-motion: reduce) { … }` blocks, joined. */
function reducedMotionCss(source: string): string {
  const out: string[] = [];
  const re = /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    let depth = 1;
    let i = re.lastIndex;
    for (; i < source.length && depth > 0; i++) {
      if (source[i] === "{") depth++;
      else if (source[i] === "}") depth--;
    }
    out.push(source.slice(re.lastIndex, i - 1));
  }
  return out.join("\n");
}

/** The stylesheet without its @media blocks (the base rules). */
function baseCss(source: string): string {
  let out = "";
  const re = /@media[^{]*\{/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    out += source.slice(last, m.index);
    let depth = 1;
    let i = re.lastIndex;
    for (; i < source.length && depth > 0; i++) {
      if (source[i] === "{") depth++;
      else if (source[i] === "}") depth--;
    }
    last = i;
    re.lastIndex = i;
  }
  return out + source.slice(last);
}

const base = baseCss(css);

function ms(value: string): number {
  const m = /([\d.]+)\s*(ms|s)\b/.exec(value);
  if (!m) return 0;
  return m[2] === "s" ? parseFloat(m[1]) * 1000 : parseFloat(m[1]);
}

describe("the stage's motion", () => {
  it("nothing on the stage loops: dots, shimmer and the ticker stand still", () => {
    for (const sel of [".st-stage .mc-dot-pulse::after", ".st-stage .mc-shimmer", ".st-stage .mc-marquee"]) {
      assert.match(ruleFor(base, sel) ?? "", /animation:\s*none/, sel);
    }
  });

  it("no stage rule animates forever", () => {
    const re = /([^{}@]+)\{([^{}]*)\}/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(css)) !== null) {
      if (!/\.st-/.test(m[1])) continue;
      assert.doesNotMatch(m[2], /infinite/, m[1].trim());
    }
  });

  it("each entrance moves once, in under a second", () => {
    for (const sel of ['.st-piece[data-enter="fade"]', '.st-piece[data-enter="rise"]', '.st-piece[data-enter="type"] .st-words-text', ".st-lane"]) {
      const rule = ruleFor(base, sel);
      assert.ok(rule, `${sel} has an entrance`);
      const anim = /animation:\s*([^;]+);/.exec(rule!)?.[1] ?? "";
      assert.ok(ms(anim) > 0 && ms(anim) < 1000, `${sel}: ${anim}`);
    }
  });

  it("under reduced motion, entrances are ≤ 120 ms fades and the stage's dots stop", () => {
    const reduced = reducedMotionCss(css);
    for (const sel of [".st-piece", ".st-lane", ".st-edward"]) {
      const rule = ruleFor(reduced, sel);
      assert.ok(rule, `${sel} missing from the reduced-motion block`);
      const anim = /animation:\s*([^;]+);/.exec(rule!)?.[1] ?? "none";
      assert.ok(anim === "none" || (ms(anim) <= 120 && /mc-fade-in/.test(anim)), `${sel}: ${anim}`);
    }
    for (const sel of [".mc-marquee", ".mc-shimmer", ".mc-dot-pulse::after"]) {
      assert.match(ruleFor(reduced, sel) ?? "", /animation:\s*none/, sel);
    }
  });

  it("under reduced motion those rules actually win the cascade (order and specificity), for every stage entrance and move", () => {
    // A media query adds no specificity: a reduced-motion rule placed before
    // the stage's own rules, or less specific than them, is dead.
    type Rule = { sel: string; body: string; at: number; reduced: boolean };
    const rules: Rule[] = [];
    const reducedRanges: [number, number][] = [];
    const mre = /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{/g;
    let mm: RegExpExecArray | null;
    while ((mm = mre.exec(css)) !== null) {
      let depth = 1;
      let i = mre.lastIndex;
      for (; i < css.length && depth > 0; i++) {
        if (css[i] === "{") depth++;
        else if (css[i] === "}") depth--;
      }
      reducedRanges.push([mre.lastIndex, i]);
    }
    const inOtherMedia = (at: number) => {
      // Only base rules and the reduced-motion blocks apply here.
      const before = css.slice(0, at);
      const open = before.lastIndexOf("@media");
      if (open < 0) return false;
      const r = reducedRanges.find(([a, b]) => at >= a && at < b);
      if (r) return false;
      let depth = 0;
      for (let i = css.indexOf("{", open); i < at; i++) {
        if (css[i] === "{") depth++;
        else if (css[i] === "}") depth--;
      }
      return depth > 0;
    };
    const re = /([^{}@]+)\{([^{}]*)\}/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(css)) !== null) {
      if (inOtherMedia(m.index)) continue;
      const reduced = reducedRanges.some(([a, b]) => m!.index >= a && m!.index < b);
      for (const sel of m[1].split(",").map((x) => x.trim()).filter(Boolean)) rules.push({ sel, body: m[2], at: m.index, reduced });
    }
    const spec = (sel: string): number => {
      const ids = (sel.match(/#[\w-]+/g) ?? []).length;
      const pe = (sel.match(/::[\w-]+/g) ?? []).length;
      const cls = (sel.match(/\.[\w-]+|\[[^\]]+\]|(?<!:):(?!:)[\w-]+/g) ?? []).length;
      const el = (sel.replace(/\[[^\]]+\]/g, "").match(/(^|[\s>+~])[a-z][\w-]*/gi) ?? []).length;
      return ids * 10000 + cls * 100 + el + pe;
    };
    const general = (sel: string) => sel.replace(/\[([\w-]+)(?:[~|^$*]?=[^\]]+)?\]/g, "[$1]");
    const covers = (r: string, target: string) => r === target || r === general(target);
    const value = (body: string, prop: string) => new RegExp(`(?:^|;|\\s)${prop}:\\s*([^;]+);`).exec(body)?.[1]?.trim() ?? null;
    const calm = (prop: string, v: string) =>
      v === "none" || (ms(v) <= 120 && (prop === "transition" || /(mc-fade-in|st-fade)/.test(v)));

    const targets = rules.filter((r) => !r.reduced && /\.st-/.test(r.sel));
    for (const t of targets) {
      for (const prop of ["animation", "transition"]) {
        const own = value(t.body, prop);
        if (!own || calm(prop, own)) continue;
        const winner = rules
          .filter((r) => covers(r.sel, t.sel) && value(r.body, prop) !== null)
          .sort((a, b) => spec(a.sel) - spec(b.sel) || a.at - b.at)
          .pop()!;
        const v = value(winner.body, prop)!;
        assert.ok(calm(prop, v), `${t.sel} { ${prop}: ${v} } still wins under reduced motion`);
      }
    }
  });

  it("standing on a piece, his line sits beside him (absolute), on the side the page chose", () => {
    assert.match(ruleFor(base, '.st-edward[data-at="on"] .st-status') ?? "", /position:\s*absolute/);
    assert.match(ruleFor(base, '.st-edward[data-at="on"][data-status-side="left"] .st-status') ?? "", /right:\s*100%/);
    assert.match(ruleFor(base, '.st-edward[data-at="on"][data-status-side="right"] .st-status') ?? "", /left:\s*100%/);
  });

  it("in a corner or at an edge his line keeps a gutter from the screen's edge", () => {
    const right = ruleFor(base, '.st-edward[data-at="corner"][data-side="right"] .st-status') ?? "";
    const left = ruleFor(base, '.st-edward[data-at="corner"][data-side="left"] .st-status') ?? "";
    assert.match(right, /margin-right:\s*calc\(var\(--u\)\s*\*\s*2\.4\)/, right);
    assert.match(left, /margin-left:\s*calc\(var\(--u\)\s*\*\s*2\.4\)/, left);
    assert.equal(ruleFor(base, '.st-edward[data-at="edge"][data-side="right"] .st-status'), right, "the edge shares the corner's gutter");
    assert.equal(ruleFor(base, '.st-edward[data-at="edge"][data-side="left"] .st-status'), left);
  });

  it("live thinking: the header sits on top of the stream, both at the lane's foot — not stranded at the lane's top", () => {
    assert.match(ruleFor(base, ".st-thought") ?? "", /justify-content:\s*flex-end/);
    assert.doesNotMatch(ruleFor(base, ".st-thought-stream") ?? "", /margin-top:\s*auto/);
    // The stream keeps its newest line when it runs long: it overflows at the top, not the bottom.
    assert.match(ruleFor(base, ".st-thought-stream") ?? "", /justify-content:\s*flex-end/);
    // "since 5:42 pm" sits beside the title, not pushed to the lane's far edge over a (dimmed) piece.
    assert.doesNotMatch(ruleFor(base, ".st-thought-head") ?? "", /space-between/);
  });

  it("a clock inside an overline keeps its lower-case pm", () => {
    assert.match(ruleFor(base, ".st-overline .st-overline-clock") ?? "", /text-transform:\s*none/);
  });

  it("the compose bar is a layer of its own (a scrim), and nothing in it loops either", () => {
    assert.match(ruleFor(base, ".st-compose") ?? "", /background:\s*var\(--st-surface\)/);
    assert.match(ruleFor(base, ".st-root .mc-dot-pulse::after") ?? "", /animation:\s*none/);
  });

  it("the canvas is ink, darker at night", () => {
    assert.match(ruleFor(base, ".st-root") ?? "", /#0a0e14/);
    assert.match(css, /\.st-root\[data-mode="night"\][^{]*\{[^}]*#05080c/);
  });

  it("no raw zinc/gray/slate and no [class*=] selectors", () => {
    assert.doesNotMatch(css, /\[class\*=/);
    assert.doesNotMatch(css, /\b(zinc|gray|slate)-\d{2,3}\b/);
  });
});
