// Keyboard on Today (UX pass 2026-09-26, desktop): j / k move between the
// cards that need the owner, e runs the focused card's primary action, x
// dismisses it. Pure; pinned in __tests__/todayKeys.test.ts.
//
// Review 2026-09-26: the focused card is whatever holds focus when the key
// is pressed (`focusedCardIndex`, read from the DOM each time) — never an
// index remembered from earlier, which a click elsewhere or a list reflow
// turned into a stray dose log or a sent message. And no key answers a
// held send (`keyAction`): texting someone from the owner's number is a tap.

import { dismissAction, type TodayAction, type TodayNeed } from "./todayModel";

export interface KeyInput {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  /** The event came from an input, a textarea or something editable. */
  editable?: boolean;
}

export type TodayKeyCommand =
  | { kind: "focus"; index: number }
  | { kind: "primary"; index: number }
  | { kind: "dismiss"; index: number };

export function todayKeyCommand(e: KeyInput, s: { focused: number | null; count: number }): TodayKeyCommand | null {
  if (e.editable || e.metaKey || e.ctrlKey || e.altKey || s.count <= 0) return null;
  const has = s.focused != null && s.focused >= 0 && s.focused < s.count;
  switch (e.key) {
    case "j":
      return { kind: "focus", index: has ? Math.min((s.focused as number) + 1, s.count - 1) : 0 };
    case "k":
      return { kind: "focus", index: has ? Math.max((s.focused as number) - 1, 0) : 0 };
    case "e":
      return has ? { kind: "primary", index: s.focused as number } : null;
    case "x":
      return has ? { kind: "dismiss", index: s.focused as number } : null;
    default:
      return null;
  }
}

/** A card on screen: its title link, and whether a node sits inside it. */
export interface CardHandle {
  link: unknown;
  contains: (node: unknown) => boolean;
}

/** The card that has focus right now (its link, or a button inside it),
 *  or null once focus is anywhere else. */
export function focusedCardIndex(cards: ReadonlyArray<CardHandle | null | undefined>, active: unknown): number | null {
  if (!active) return null;
  const i = cards.findIndex((c) => !!c && (c.link === active || c.contains(active)));
  return i >= 0 ? i : null;
}

/** What `e` (primary) or `x` (dismiss) runs on a card, or null. Never a
 *  navigation, never anything on a held send (Send, Don't send), and `x`
 *  is only ever a Done. */
export function keyAction(need: TodayNeed, kind: "primary" | "dismiss"): TodayAction | null {
  if (need.kind === "approval") return null;
  if (kind === "dismiss") return dismissAction(need);
  return need.actions.find((a) => a.primary && a.method !== "GET") ?? null;
}
