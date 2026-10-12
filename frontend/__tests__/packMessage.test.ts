// Reading Edward's thinking (2026-09-25). Every wake of the mind lands in
// `edward-mind` with a "user" message that is the raw signal pack — 10–20K
// chars that start `[WAKE: <reason>] …` (the surfaces pulse's start
// `[EXPRESSION PULSE] …`). Rendered as a chat bubble they made the thread a
// wall of text Ellis could not read. The chat folds them into one line; this
// pins what counts as a pack and what the folded line says.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { formatChars, isPackMessage, packLabel, packSummary, PACK_MIN_CHARS } from "../lib/packMessage.ts";

const body = (n: number) => Array.from({ length: n }, (_, i) => `line ${i}: ${"x".repeat(60)}`).join("\n");

const WAKE = `[WAKE: morning] thu 7:00am. Ellis is at mac:mini.\nSince your last wake (9h): 3 new messages.\n\n${body(40)}`;
const PULSE = `[EXPRESSION PULSE] thu 7:15am. Ellis is away.\nSince your last pulse: 3 new messages; the widget was changed by owner\n${body(40)}`;

describe("isPackMessage", () => {
  it("folds a long wake pack and a long pulse pack", () => {
    assert.ok(WAKE.length > PACK_MIN_CHARS);
    assert.equal(isPackMessage(WAKE), true);
    assert.equal(isPackMessage(PULSE), true);
  });

  it("folds any long message behind an all-caps bracketed header", () => {
    assert.equal(isPackMessage(`[RESEARCH] ${body(20)}`), true);
    assert.equal(isPackMessage(`[REFLECT_LITE: nightly] ${body(20)}`), true);
    assert.equal(isPackMessage(`  \n[WAKE: self] ${body(20)}`), true, "leading whitespace is tolerated");
  });

  it("leaves short bracketed messages alone, whatever Ellis types", () => {
    assert.equal(isPackMessage("[test]"), false);
    assert.equal(isPackMessage("[WAKE: morning] short one"), false);
    assert.equal(isPackMessage(`[WAKE: morning] ${"y".repeat(PACK_MIN_CHARS - 40)}`), false);
  });

  it("needs the header at the very start, in capitals", () => {
    assert.equal(isPackMessage(`hello [WAKE: morning] ${body(20)}`), false);
    assert.equal(isPackMessage(`[test] ${body(20)}`), false, "lowercase is Ellis typing");
    assert.equal(isPackMessage(`[OK] ${body(20)}`), false, "two letters is not a header");
    assert.equal(isPackMessage(`${body(20)}`), false);
    assert.equal(isPackMessage(""), false);
    assert.equal(isPackMessage(undefined as unknown as string), false);
  });
});

describe("packSummary", () => {
  it("reads a wake pack's reason, time and size", () => {
    const s = packSummary(WAKE);
    assert.equal(s.kind, "wake");
    assert.equal(s.reason, "morning");
    assert.equal(s.when, "thu 7:00am");
    assert.equal(s.chars, WAKE.length);
    assert.deepEqual(s.firstLines.slice(0, 2), [
      "[WAKE: morning] thu 7:00am. Ellis is at mac:mini.",
      "Since your last wake (9h): 3 new messages.",
    ]);
    assert.ok(s.firstLines.length <= 3);
  });

  it("names the pulse without a reason", () => {
    const s = packSummary(PULSE);
    assert.equal(s.kind, "pulse");
    assert.equal(s.reason, null);
    assert.equal(s.when, "thu 7:15am");
  });

  it("keeps an unknown header as its own kind", () => {
    const s = packSummary(`[REFLECT_LITE: nightly] a long first sentence that is certainly not a time of day. ${body(20)}`);
    assert.equal(s.kind, "reflect_lite");
    assert.equal(s.reason, "nightly");
    assert.equal(s.when, null, "a long first sentence is not a time");
  });

  it("clips very long first lines", () => {
    const s = packSummary(`[WAKE: mention] ${"z".repeat(900)}`);
    assert.ok(s.firstLines[0].length <= 161);
    assert.ok(s.firstLines[0].endsWith("…"));
  });
});

describe("packLabel", () => {
  it("reads as one line: kind, reason, time, size", () => {
    assert.equal(
      packLabel({ kind: "wake", reason: "morning", when: "thu 7:00am", chars: 14_230, firstLines: [] }),
      "Wake pack · morning · thu 7:00am · 14.2K chars",
    );
    assert.equal(
      packLabel({ kind: "pulse", reason: null, when: "thu 7:15am", chars: 9_870, firstLines: [] }),
      "Pulse pack · thu 7:15am · 9.9K chars",
    );
    assert.equal(
      packLabel({ kind: "reflect_lite", reason: null, when: null, chars: 700, firstLines: [] }),
      "Reflect lite pack · 700 chars",
    );
  });
});

describe("formatChars", () => {
  it("rounds to thousands past a thousand", () => {
    assert.equal(formatChars(999), "999 chars");
    assert.equal(formatChars(1_000), "1.0K chars");
    assert.equal(formatChars(14_230), "14.2K chars");
    assert.equal(formatChars(123_456), "123K chars");
  });
});
