// The keyboard layer (docs/design-system-2026-09-25.md §5) — pure, so the
// keymap is pinned in __tests__/hotkeys.test.ts. components/layout/
// KeyboardLayer.tsx listens on window and runs what this decides.
//
//   ⌘K / Ctrl+K      palette (even while typing)
//   /                focus the composer            -> window "edward:focus-composer"
//   Esc              close the top sheet / palette, else stop the turn -> "edward:stop-turn"
//   ⌘⇧O              new chat
//   ⌘\               toggle the chat sidebar
//   g t · g c · g e  Today · Chat · Edward
//
// While the owner types in a field only Esc, ⌘K and the two modifier combos count
// (⌘⇧O and ⌘\ type nothing; on desktop the composer is focused on open, so
// behind the typing guard they never worked in Chat). While a dialog (a
// sheet, the palette) is open only Esc and ⌘K count: "/" must not pull focus
// out of the dialog, nor "g c" change the page under it. On a Mac the
// modifier is ⌘ (Ctrl+K is kill-line in a text field there); elsewhere Ctrl.

export const FOCUS_COMPOSER_EVENT = "edward:focus-composer";
export const STOP_TURN_EVENT = "edward:stop-turn";

/** How long a `g` waits for its second key. */
export const SEQUENCE_TIMEOUT_MS = 1200;

export type HotkeyAction =
  | "palette"
  | "focus-composer"
  | "escape"
  | "new-chat"
  | "toggle-sidebar"
  | "go-today"
  | "go-chat"
  | "go-edward";

export interface KeyInput {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  isComposing?: boolean;
}

export interface HotkeyContext {
  /** Focus is in a field that takes text. */
  typing: boolean;
  /** The first key of a sequence, waiting for its second. */
  pending: "g" | null;
  mac: boolean;
  /** A modal dialog (a sheet, the palette) is open. */
  modal?: boolean;
}

export interface HotkeyResult {
  action: HotkeyAction | null;
  pending: "g" | null;
  preventDefault: boolean;
}

const NONE: HotkeyResult = { action: null, pending: null, preventDefault: false };

function hit(action: HotkeyAction, preventDefault = true): HotkeyResult {
  return { action, pending: null, preventDefault };
}

const GO: Record<string, HotkeyAction> = { t: "go-today", c: "go-chat", e: "go-edward" };

export function resolveHotkey(e: KeyInput, ctx: HotkeyContext): HotkeyResult {
  if (e.isComposing) return NONE;
  if (e.key === "Escape") return hit("escape", false);
  if (e.altKey) return NONE;

  const mod = ctx.mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;

  if (mod && !e.shiftKey && key === "k") return hit("palette");
  if (ctx.modal) return NONE;

  if (mod && e.shiftKey && key === "o") return hit("new-chat");
  if (mod && !e.shiftKey && key === "\\") return hit("toggle-sidebar");
  if (ctx.typing) return NONE;
  if (e.metaKey || e.ctrlKey) return NONE;

  if (ctx.pending === "g") {
    const go = GO[key];
    return go ? hit(go) : NONE;
  }
  if (key === "/" && !e.shiftKey) return hit("focus-composer");
  if (key === "g" && !e.shiftKey) return { action: null, pending: "g", preventDefault: false };
  return NONE;
}

const TEXT_INPUTS = new Set([
  "text", "search", "email", "password", "url", "tel", "number", "date", "datetime-local", "month", "time", "week", "",
]);

/** Does focus sit in something that takes typing? */
export function isTypingTarget(
  el: { tagName?: string; type?: string; isContentEditable?: boolean } | null | undefined,
): boolean {
  if (!el) return false;
  if (el.isContentEditable) return true;
  const tag = (el.tagName ?? "").toUpperCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") return TEXT_INPUTS.has((el.type ?? "text").toLowerCase());
  return false;
}

export function isMacPlatform(platform: string | undefined | null): boolean {
  return !!platform && /mac|iphone|ipad|ipod/i.test(platform);
}

/** The shortcut as drawn in a hint ("⌘K", "Ctrl K", "G T"). */
export function shortcutLabel(action: HotkeyAction, mac: boolean): string {
  const mod = mac ? "⌘" : "Ctrl ";
  switch (action) {
    case "palette":
      return `${mod}K`;
    case "new-chat":
      return mac ? "⌘⇧O" : "Ctrl Shift O";
    case "toggle-sidebar":
      return `${mod}\\`;
    case "focus-composer":
      return "/";
    case "escape":
      return "Esc";
    case "go-today":
      return "G T";
    case "go-chat":
      return "G C";
    case "go-edward":
      return "G E";
  }
}
