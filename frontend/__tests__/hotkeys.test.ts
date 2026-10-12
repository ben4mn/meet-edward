// R/G red for lib/hotkeys.ts — the keyboard layer (design system §5).
//
//   ⌘K / Ctrl+K   the command palette (even while typing)
//   /             focus the composer
//   Esc           close the top sheet or palette, else stop the running turn (even while typing)
//   ⌘⇧O           new chat
//   ⌘\            toggle the chat sidebar
//   g t / g c / g e   go to Today / Chat / Edward
//
// Keys are ignored while Ellis types in a field, except Esc, ⌘K and the two
// modifier combos (they type nothing); while a dialog is open only Esc and
// ⌘K count. On a Mac
// the modifier is ⌘ (Ctrl+K in a text field is "kill line" there); elsewhere
// it is Ctrl.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  resolveHotkey,
  isTypingTarget,
  shortcutLabel,
  isMacPlatform,
  FOCUS_COMPOSER_EVENT,
  STOP_TURN_EVENT,
  type KeyInput,
} from "../lib/hotkeys.ts";

const k = (key: string, mods: Partial<KeyInput> = {}): KeyInput => ({
  key,
  metaKey: false,
  ctrlKey: false,
  shiftKey: false,
  altKey: false,
  ...mods,
});
const mac = { typing: false, pending: null, mac: true } as const;
const pc = { typing: false, pending: null, mac: false } as const;

describe("resolveHotkey", () => {
  it("⌘K opens the palette on a Mac, Ctrl+K elsewhere, even while typing", () => {
    assert.equal(resolveHotkey(k("k", { metaKey: true }), mac).action, "palette");
    assert.equal(resolveHotkey(k("K", { metaKey: true }), { ...mac, typing: true }).action, "palette");
    assert.equal(resolveHotkey(k("k", { ctrlKey: true }), pc).action, "palette");
    assert.equal(resolveHotkey(k("k", { ctrlKey: true }), mac).action, null, "Ctrl+K on a Mac is kill-line");
  });

  it("Esc is always 'escape', typing or not", () => {
    assert.equal(resolveHotkey(k("Escape"), mac).action, "escape");
    assert.equal(resolveHotkey(k("Escape"), { ...mac, typing: true }).action, "escape");
  });

  it("/ focuses the composer, but not while typing", () => {
    assert.equal(resolveHotkey(k("/"), mac).action, "focus-composer");
    assert.equal(resolveHotkey(k("/"), { ...mac, typing: true }).action, null);
    assert.equal(resolveHotkey(k("/", { metaKey: true }), mac).action, null);
  });

  it("⌘⇧O starts a new chat; ⌘\\ toggles the sidebar — even from the composer", () => {
    // 2026-09-26: on desktop the composer is focused on open and after every
    // send, so behind the typing guard these never worked in Chat. They type
    // no text, so they are safe in a field.
    assert.equal(resolveHotkey(k("O", { metaKey: true, shiftKey: true }), mac).action, "new-chat");
    assert.equal(resolveHotkey(k("o", { ctrlKey: true, shiftKey: true }), pc).action, "new-chat");
    assert.equal(resolveHotkey(k("\\", { metaKey: true }), mac).action, "toggle-sidebar");
    assert.equal(resolveHotkey(k("O", { metaKey: true, shiftKey: true }), { ...mac, typing: true }).action, "new-chat");
    assert.equal(resolveHotkey(k("\\", { metaKey: true }), { ...mac, typing: true }).action, "toggle-sidebar");
    assert.equal(resolveHotkey(k("o", { ctrlKey: true, shiftKey: true }), { ...pc, typing: true }).action, "new-chat");
  });

  it("while a sheet or the palette is open only ⌘K and Esc count", () => {
    const modal = { ...mac, modal: true };
    assert.equal(resolveHotkey(k("/"), modal).action, null, "/ never pulls focus out of the dialog");
    assert.equal(resolveHotkey(k("g"), modal).pending, null, "no g-sequence behind the dialog");
    assert.equal(resolveHotkey(k("t"), { ...modal, pending: "g" }).action, null);
    assert.equal(resolveHotkey(k("O", { metaKey: true, shiftKey: true }), modal).action, null);
    assert.equal(resolveHotkey(k("\\", { metaKey: true }), modal).action, null);
    assert.equal(resolveHotkey(k("k", { metaKey: true }), modal).action, "palette");
    assert.equal(resolveHotkey(k("Escape"), modal).action, "escape");
  });

  it("g then t / c / e navigates; anything else drops the g", () => {
    const g = resolveHotkey(k("g"), mac);
    assert.equal(g.action, null);
    assert.equal(g.pending, "g");
    assert.equal(resolveHotkey(k("t"), { ...mac, pending: "g" }).action, "go-today");
    assert.equal(resolveHotkey(k("c"), { ...mac, pending: "g" }).action, "go-chat");
    assert.equal(resolveHotkey(k("e"), { ...mac, pending: "g" }).action, "go-edward");
    const other = resolveHotkey(k("x"), { ...mac, pending: "g" });
    assert.equal(other.action, null);
    assert.equal(other.pending, null);
    assert.equal(resolveHotkey(k("g"), { ...mac, typing: true }).pending, null, "typing a g is just a g");
  });

  it("plain letters, Alt combos and IME composition do nothing", () => {
    assert.equal(resolveHotkey(k("t"), mac).action, null);
    assert.equal(resolveHotkey(k("k", { metaKey: true, altKey: true }), mac).action, null);
    assert.equal(resolveHotkey(k("Escape", { isComposing: true }), mac).action, null);
  });

  it("says when the browser default must be prevented", () => {
    assert.equal(resolveHotkey(k("k", { metaKey: true }), mac).preventDefault, true);
    assert.equal(resolveHotkey(k("/"), mac).preventDefault, true, "no quick-find in Firefox");
    assert.equal(resolveHotkey(k("Escape"), mac).preventDefault, false, "Esc is left for whoever else wants it");
  });
});

describe("isTypingTarget", () => {
  it("inputs that take text, textareas, selects and editable content", () => {
    assert.equal(isTypingTarget({ tagName: "TEXTAREA" }), true);
    assert.equal(isTypingTarget({ tagName: "INPUT", type: "text" }), true);
    assert.equal(isTypingTarget({ tagName: "INPUT", type: "search" }), true);
    assert.equal(isTypingTarget({ tagName: "INPUT", type: "password" }), true);
    assert.equal(isTypingTarget({ tagName: "SELECT" }), true);
    assert.equal(isTypingTarget({ tagName: "DIV", isContentEditable: true }), true);
  });
  it("buttons, checkboxes and the page are not", () => {
    assert.equal(isTypingTarget({ tagName: "BUTTON" }), false);
    assert.equal(isTypingTarget({ tagName: "INPUT", type: "checkbox" }), false);
    assert.equal(isTypingTarget({ tagName: "BODY" }), false);
    assert.equal(isTypingTarget(null), false);
  });
});

describe("shortcut labels and platform", () => {
  it("draws ⌘ on a Mac and Ctrl elsewhere", () => {
    assert.equal(shortcutLabel("palette", true), "⌘K");
    assert.equal(shortcutLabel("palette", false), "Ctrl K");
    assert.equal(shortcutLabel("new-chat", true), "⌘⇧O");
    assert.equal(shortcutLabel("focus-composer", true), "/");
    assert.equal(shortcutLabel("go-today", true), "G T");
  });
  it("detects a Mac from the platform string", () => {
    assert.equal(isMacPlatform("MacIntel"), true);
    assert.equal(isMacPlatform("iPhone"), true);
    assert.equal(isMacPlatform("Win32"), false);
    assert.equal(isMacPlatform(undefined), false);
  });
  it("names the window events the pages listen to", () => {
    assert.equal(FOCUS_COMPOSER_EVENT, "edward:focus-composer");
    assert.equal(STOP_TURN_EVENT, "edward:stop-turn");
  });
});
