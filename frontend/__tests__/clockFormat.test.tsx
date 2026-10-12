// One clock format, "7:34 pm", everywhere a time of day is shown in words
// (L1 leftovers, 2026-09-27 integration). lib/time's formatClock /
// joinClockParts said it for the sidebar and the logs, but the events
// panel ("Today at 3:45 PM"), the hub's clocks, /screen's calendar and
// "now thinking" card and the Locale panel still printed Intl's "AM"/"PM"
// (or "8:04pm") beside Today's "7:34 pm". A big clock face that shows no
// am/pm at all (hour12: false) is deliberate and stays.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import React from "react";
import { cleanup, render } from "@testing-library/react";

const ROOT = path.resolve(__dirname, "..");

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name.startsWith(".")) continue;
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) out.push(...sources(p));
    else if (/\.(ts|tsx)$/.test(name) && !name.endsWith(".d.ts")) out.push(p);
  }
  return out;
}

const FILES = ["app", "components", "lib", "hooks"].flatMap((d) => sources(path.join(ROOT, d)));
const rel = (p: string) => path.relative(ROOT, p);
const lineOf = (src: string, i: number) => src.slice(0, i).split("\n").length;

/** The source of a call starting at `open` (the index of its "("). */
function callAt(src: string, open: number): string {
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === "(") depth++;
    else if (src[i] === ")" && --depth === 0) return src.slice(open, i + 1);
  }
  return src.slice(open);
}

describe("one clock format in the source", () => {
  it("no toLocaleTimeString: a time of day goes through lib/time (browser locale, Intl's AM/PM)", () => {
    const offenders = FILES.flatMap((f) => {
      const src = readFileSync(f, "utf8");
      return [...src.matchAll(/\.toLocaleTimeString\(/g)].map((m) => `${rel(f)}:${lineOf(src, m.index!)}`);
    });
    assert.deepEqual(offenders, []);
  });

  it("an Intl clock with a day period is joined through joinClockParts (arithmetic in h23, or a 24-hour face, is fine)", () => {
    const offenders: string[] = [];
    for (const f of FILES) {
      const src = readFileSync(f, "utf8");
      for (const m of src.matchAll(/new Intl\.DateTimeFormat\(/g)) {
        const open = m.index! + m[0].length - 1;
        const call = callAt(src, open);
        if (!/\bhour\s*:/.test(call)) continue;
        if (/hourCycle\s*:\s*["']h23["']|hour12\s*:\s*false/.test(call)) continue;
        const before = src.slice(Math.max(0, m.index! - 80), m.index!);
        const after = src.slice(open + call.length, open + call.length + 20);
        if (/joinClockParts\(\s*$/.test(before) && after.startsWith(".formatToParts(")) continue;
        offenders.push(`${rel(f)}:${lineOf(src, m.index!)}`);
      }
    }
    assert.deepEqual(offenders, []);
  });
});

import { HubHeader } from "../app/(display)/hub/components/HubHeader.tsx";

// The hub is written for Next's automatic JSX runtime (no React import).
(globalThis as unknown as { React: typeof React }).React = React;
afterEach(cleanup);

describe("the hub's clock", () => {
  it("reads like every other clock in the app", () => {
    const { container } = render(<HubHeader title="Pets" />);
    const clock = container.querySelector(".hub-clock")?.textContent ?? "";
    assert.match(clock, /^\d{1,2}:\d{2} (am|pm)$/, `the hub clock read ${JSON.stringify(clock)}`);
  });
});
