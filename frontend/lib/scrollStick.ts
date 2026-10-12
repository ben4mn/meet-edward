// Stick-to-bottom for the chat transcript (2026-09-26), as pure decisions.
//
// While Edward streams, the transcript follows the newest line. The owner
// scrolling up — even a nudge — lets go, and nothing yanks them back: new
// text only raises "New reply ↓". Scrolling back to the bottom, sending, a
// new conversation and tapping the pill all stick again. ChatWindow measures
// (scroll events, a ResizeObserver on the content) and writes scrollTop
// directly when `pin` says so — no smooth scrolling per token.
//
// Only the owner lets go (2026-09-26 review). A scroll event carries `user`:
// whether their hands made it (wheel, touch, a scrolling key, the scrollbar —
// ChatWindow tracks the intent). Layout clamps are not the owner: the composer
// re-measuring its height collapses the box for an instant, the transcript
// grows taller, the browser clamps scrollTop down a line — read as "moved
// up", that dropped the live turn behind the pill the moment a queued
// follow-up wrapped to a second line. And opening something in the
// transcript (a receipt, a pack card) is the owner reading: `toggle` lets go
// without a pin, so it opens under their finger instead of the pin throwing
// the header they tapped off the top.

/** Within this many px of the bottom counts as "at the bottom". */
export const STICK_PX = 32;

export interface StickState {
  /** Follow new content to the bottom. */
  stuck: boolean;
  /** Content arrived while the owner was scrolled up during a stream. */
  unseen: boolean;
  /** The last scrollTop seen, to tell a scroll up from content shrinking. */
  lastTop: number;
}

export type StickEvent =
  /** A different conversation (or the first load of one) is on screen. */
  | { type: "reset" }
  /** The owner sent a message. */
  | { type: "send" }
  /** The owner tapped "New reply ↓". */
  | { type: "jump" }
  /** A scroll event: scrollTop, the distance left below the viewport, and
   *  whether the owner made it (omitted: assume they did). */
  | { type: "scroll"; top: number; distance: number; user?: boolean }
  /** The owner opened or closed something in the transcript. */
  | { type: "toggle" }
  /** The content's height changed. */
  | { type: "grow"; streaming: boolean };

export const INITIAL_STICK: StickState = { stuck: true, unseen: false, lastTop: 0 };

export function distanceFromBottom(el: { scrollTop: number; scrollHeight: number; clientHeight: number }): number {
  return Math.max(0, el.scrollHeight - el.scrollTop - el.clientHeight);
}

/** The next state, and whether to put the scroll position at the bottom now. */
export function reduceStick(state: StickState, event: StickEvent): { state: StickState; pin: boolean } {
  switch (event.type) {
    case "reset":
      return { state: { stuck: true, unseen: false, lastTop: 0 }, pin: true };
    case "send":
    case "jump":
      return { state: { ...state, stuck: true, unseen: false }, pin: true };
    case "scroll": {
      const { top, distance } = event;
      // Moving up away from the bottom is the owner reading — when they made
      // the move. Content shrinking under them clamps scrollTop but leaves them at
      // the bottom; a layout clamp that leaves a line below is not them either.
      const letGo = event.user !== false && top < state.lastTop - 1;
      if (distance <= STICK_PX && !(letGo && distance > 0)) {
        return { state: { stuck: true, unseen: false, lastTop: top }, pin: false };
      }
      return { state: { ...state, stuck: letGo ? false : state.stuck, lastTop: top }, pin: false };
    }
    case "toggle":
      return { state: { ...state, stuck: false }, pin: false };
    case "grow":
      if (state.stuck) return { state, pin: true };
      if (event.streaming && !state.unseen) return { state: { ...state, unseen: true }, pin: false };
      return { state, pin: false };
  }
}

/** A scroll this soon after the owner's wheel, touch, key or scrollbar counts as theirs. */
export const INTENT_MS = 600;

const SCROLL_KEYS = new Set(["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " ", "Spacebar"]);

/** A key that scrolls the transcript — never one typed into a text field. */
export function isScrollKey(key: string, inTextField: boolean): boolean {
  return !inTextField && SCROLL_KEYS.has(key);
}

export function showNewReplyPill(state: StickState): boolean {
  return !state.stuck && state.unseen;
}
