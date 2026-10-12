// Today on the desktop (UX pass 2026-09-26): j / k move between the cards
// that need Ellis, e runs the focused card's primary action, x dismisses it.
// Never while typing, never with a modifier (⌘K is the palette).

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { todayKeyCommand } from "../components/today/todayKeys.ts";

const k = (key: string, over: Record<string, unknown> = {}) => ({ key, ...over });

describe("todayKeyCommand", () => {
  it("j and k move, starting at the first card", () => {
    assert.deepEqual(todayKeyCommand(k("j"), { focused: null, count: 3 }), { kind: "focus", index: 0 });
    assert.deepEqual(todayKeyCommand(k("j"), { focused: 0, count: 3 }), { kind: "focus", index: 1 });
    assert.deepEqual(todayKeyCommand(k("j"), { focused: 2, count: 3 }), { kind: "focus", index: 2 });
    assert.deepEqual(todayKeyCommand(k("k"), { focused: 2, count: 3 }), { kind: "focus", index: 1 });
    assert.deepEqual(todayKeyCommand(k("k"), { focused: 0, count: 3 }), { kind: "focus", index: 0 });
    assert.deepEqual(todayKeyCommand(k("k"), { focused: null, count: 3 }), { kind: "focus", index: 0 });
  });
  it("e and x act on the focused card only", () => {
    assert.deepEqual(todayKeyCommand(k("e"), { focused: 1, count: 3 }), { kind: "primary", index: 1 });
    assert.deepEqual(todayKeyCommand(k("x"), { focused: 1, count: 3 }), { kind: "dismiss", index: 1 });
    assert.equal(todayKeyCommand(k("e"), { focused: null, count: 3 }), null);
    assert.equal(todayKeyCommand(k("x"), { focused: 5, count: 3 }), null);
  });
  it("stays out of the way while typing, with modifiers, or with nothing to move to", () => {
    assert.equal(todayKeyCommand(k("j", { editable: true }), { focused: 0, count: 3 }), null);
    assert.equal(todayKeyCommand(k("j", { metaKey: true }), { focused: 0, count: 3 }), null);
    assert.equal(todayKeyCommand(k("e", { ctrlKey: true }), { focused: 0, count: 3 }), null);
    assert.equal(todayKeyCommand(k("x", { altKey: true }), { focused: 0, count: 3 }), null);
    assert.equal(todayKeyCommand(k("j"), { focused: null, count: 0 }), null);
    assert.equal(todayKeyCommand(k("J"), { focused: 0, count: 3 }), null, "shift-J is not j");
    assert.equal(todayKeyCommand(k("q"), { focused: 0, count: 3 }), null);
  });
});

// Review 2026-09-26 (today-e-key-sends-held-message, today-keys-stale-focus):
// the focused card is read from the DOM when the key is pressed — never a
// remembered index — and no key ever answers a held send.

import { focusedCardIndex, keyAction } from "../components/today/todayKeys.ts";
import type { TodayNeed } from "../components/today/todayModel.ts";

const linkA = { name: "link A" };
const linkB = { name: "link B" };
const insideB = { name: "B's Log given button" };
const cards = [
  { link: linkA, contains: (n: unknown) => n === linkA },
  { link: linkB, contains: (n: unknown) => n === linkB || n === insideB },
];

describe("focusedCardIndex", () => {
  it("is the card whose link, or anything inside it, has focus now", () => {
    assert.equal(focusedCardIndex(cards, linkA), 0);
    assert.equal(focusedCardIndex(cards, linkB), 1);
    assert.equal(focusedCardIndex(cards, insideB), 1);
  });
  it("is nothing once focus has left the cards (a click elsewhere, a card gone)", () => {
    assert.equal(focusedCardIndex(cards, { name: "body" }), null);
    assert.equal(focusedCardIndex(cards, null), null);
    assert.equal(focusedCardIndex([null, undefined], linkA), null);
  });
});

const need = (over: Partial<TodayNeed>): TodayNeed => ({
  id: "n",
  kind: "dose",
  tone: "warn",
  priority: 1,
  title: "Biscuit · Carprofen",
  detail: "",
  href: "/hub/pets",
  actions: [],
  ...over,
});

describe("keyAction", () => {
  it("e runs a card's primary action and x its Done", () => {
    const log = { id: "log", label: "Log given", method: "POST", url: "/api/hub/care/x/log", primary: true };
    const done = { id: "done", label: "Done", method: "POST", url: "/api/notes/n/dismiss", primary: false };
    assert.equal(keyAction(need({ actions: [log] }), "primary")?.id, "log");
    assert.equal(keyAction(need({ kind: "note", actions: [done] }), "dismiss")?.id, "done");
    assert.equal(keyAction(need({ actions: [log] }), "dismiss"), null, "x never logs a dose");
  });
  it("never answers a held send: e does not send, x does not deny", () => {
    const held = need({
      kind: "approval",
      actions: [
        { id: "approve", label: "Approve", method: "POST", url: "/api/approvals/31", body: { decision: "approve" }, primary: true },
        { id: "deny", label: "Deny", method: "POST", url: "/api/approvals/31", body: { decision: "deny" }, primary: false },
        { id: "dismiss", label: "Dismiss", method: "POST", url: "/api/approvals/31", primary: false },
      ],
    });
    assert.equal(keyAction(held, "primary"), null);
    assert.equal(keyAction(held, "dismiss"), null);
  });
  it("a navigation is not an action", () => {
    const open = { id: "open", label: "Open", method: "GET", url: "/chat", primary: true };
    assert.equal(keyAction(need({ actions: [open] }), "primary"), null);
  });
});
