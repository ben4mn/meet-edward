// R/G red for per-person theming on the hub.
//
// Ellis and Rowan both tap the same screen, and the who-toggle is a small
// control in a corner. Tinting the whole panel makes the active person
// readable at a glance from across the room, which is the actual failure
// we're guarding against: Rowan logging Biscuit's dose as Ellis.
//
// Themes go by position in the household (EDWARD_HOUSEHOLD, owner first),
// never by a hard-coded name: the first person gets the first palette.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { themeFor, THEMES, DEFAULT_THEME } from "../app/(display)/hub/lib/whoTheme.ts";

const HOUSEHOLD = ["Ellis", "Rowan"] as const;

describe("themeFor", () => {
  it("maps each person to their own theme, by their place in the household", () => {
    assert.equal(themeFor("Ellis", HOUSEHOLD), "first");
    assert.equal(themeFor("Rowan", HOUSEHOLD), "second");
  });

  it("follows the household's order, not the names", () => {
    assert.equal(themeFor("Rowan", ["Rowan", "Ellis"]), "first");
    assert.equal(themeFor("Ellis", ["Rowan", "Ellis"]), "second");
    assert.equal(themeFor("Parker", ["Ellis", "Rowan", "Parker"]), "third");
  });

  it("is case-insensitive, since it round-trips through localStorage", () => {
    assert.equal(themeFor("rowan", HOUSEHOLD), "second");
    assert.equal(themeFor("ROWAN", HOUSEHOLD), "second");
    assert.equal(themeFor("eLLis", HOUSEHOLD), "first");
  });

  it("tolerates stray whitespace", () => {
    assert.equal(themeFor("  Rowan ", HOUSEHOLD), "second");
  });

  it("falls back for an unknown person rather than rendering untinted", () => {
    // A pet sitter will eventually log too; they should get a usable panel,
    // not one missing its accent colour entirely.
    assert.equal(themeFor("Sitter", HOUSEHOLD), DEFAULT_THEME);
  });

  it("falls back for someone past the last palette", () => {
    const big = ["Ellis", "Rowan", "Parker", "Sitter"];
    assert.equal(themeFor("Sitter", big), DEFAULT_THEME);
  });

  it("falls back for empty or missing input", () => {
    assert.equal(themeFor("", HOUSEHOLD), DEFAULT_THEME);
    assert.equal(themeFor(undefined as unknown as string, HOUSEHOLD), DEFAULT_THEME);
    assert.equal(themeFor("Ellis", []), DEFAULT_THEME);
    assert.equal(themeFor("Ellis", undefined as unknown as string[]), DEFAULT_THEME);
  });

  it("every theme is a known slug", () => {
    for (const t of Object.values(THEMES)) {
      assert.ok(["first", "second", "third"].includes(t), `unexpected theme ${t}`);
    }
  });

  it("the default is one of the real themes", () => {
    assert.ok(Object.values(THEMES).includes(DEFAULT_THEME));
  });
});
