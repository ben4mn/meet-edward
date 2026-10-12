// The chat composer's primary button (the one slot right of the text box).
//
// The ChatGPT pattern: nothing typed → the button starts a live voice
// conversation; something typed (or attached) → it sends. The memo mic is
// a separate button, so "record a clip" and "talk live" are always one
// tap each, and neither hides behind a menu.
//
// While Edward is working (2026-09-24, plan Phase 5 "queue while
// running"): anything typed queues for when the turn ends — the slot
// becomes "queue" and Stop moves to its own button beside it. With
// nothing typed the slot is Stop, as before. Enter never stops a turn.

export type PrimaryAction = "talk" | "send" | "send_disabled" | "queue" | "stop" | "busy" | "none";

export interface ComposerState {
  message: string;
  fileCount: number;
  isLoading: boolean;
  canStop: boolean;
  isRecording: boolean;
  voiceEnabled: boolean;
  /** A queue to put the next message in (the ChatProvider's). Defaults to true. */
  canQueue?: boolean;
}

function hasContent(s: ComposerState): boolean {
  return s.message.trim().length > 0 || s.fileCount > 0;
}

export function primaryAction(s: ComposerState): PrimaryAction {
  if (s.isRecording) return "none";
  if (s.isLoading || s.canStop) {
    if (hasContent(s) && s.canQueue !== false) return "queue";
    return s.canStop ? "stop" : "busy";
  }
  if (hasContent(s)) return "send";
  return s.voiceEnabled ? "talk" : "send_disabled";
}

/** Stop stays one tap away while the primary slot is busy queueing. */
export function showsSeparateStop(s: ComposerState): boolean {
  return s.canStop && primaryAction(s) === "queue";
}

/** What Enter (and the primary button, when it is not Stop) does. */
export function enterAction(s: ComposerState): "send" | "queue" | "none" {
  const action = primaryAction(s);
  return action === "send" || action === "queue" ? action : "none";
}

// ---- the Enter key (2026-09-26) -------------------------------------------
//
// Phone (coarse pointer): Enter makes a new line and the send button sends,
// the way Messages works — a stray tap on return never fires half a
// thought. Desktop: Enter sends, Shift+Enter is a new line. Everywhere an
// IME composition owns Enter until it commits (isComposing, or Safari's
// keyCode 229), and Cmd/Ctrl+Enter always sends (an iPad with a keyboard).

export interface EnterKeyEvent {
  key: string;
  shiftKey: boolean;
  metaKey: boolean;
  ctrlKey: boolean;
  isComposing: boolean;
  keyCode?: number;
  /** `(pointer: coarse)`: the phone. */
  coarse: boolean;
}

export type EnterKeyIntent = "submit" | "newline" | "ignore";

export function enterKeyIntent(e: EnterKeyEvent): EnterKeyIntent {
  if (e.key !== "Enter") return "ignore";
  if (e.isComposing || e.keyCode === 229) return "ignore";
  if (e.metaKey || e.ctrlKey) return "submit";
  if (e.coarse || e.shiftKey) return "newline";
  return "submit";
}

/** The return key's label on a virtual keyboard: honest about what Enter does. */
export function enterKeyHintFor(coarse: boolean): "enter" | "send" {
  return coarse ? "enter" : "send";
}

// ---- the bottom inset -----------------------------------------------------
//
// Only the layout owns the home indicator (design system §5). The shell sets
// --shell-inset-bottom (lib/chrome.ts shellBottomInset): 0 while the dock is
// shown — the dock carries the inset under the composer — and the inset
// while the dock folds away for the phone's focus mode. The composer pads by
// that variable (at least 8px) and never reads env(safe-area-inset-bottom)
// itself, so the two never double. md+ has no dock: a fixed 16px.

export function composerInsetClass(): string {
  return "pb-[max(var(--shell-inset-bottom,0px),8px)] md:pb-4";
}
