// Pure config + helper for the global mobile ThumbDock and the desktop
// header nav. Listed here (rather than inlined) so the set and active-state
// logic are testable under node --test without JSX.
//
// Three tabs, three questions (redesign, Sep 2026):
//   Today   — what does Edward need from me, and what happened?
//   Chat    — talk to him.
//   Edward  — what does he know, watch and do? (route stays /settings so
//             every old /settings/<slug> link keeps working)

export type NavItem = {
  label: "Today" | "Chat" | "Edward";
  href: "/today" | "/chat" | "/settings";
};

export const NAV_ITEMS: readonly NavItem[] = [
  { label: "Today", href: "/today" },
  { label: "Chat", href: "/chat" },
  { label: "Edward", href: "/settings" },
] as const;

/** A nav item is active when the current pathname equals its href, or
 *  — for hub routes like /settings — starts with `${href}/`. Query
 *  strings and hashes are stripped before matching. */
export function isNavActive(pathname: string, href: string): boolean {
  const path = pathname.split("?")[0].split("#")[0];
  if (path === href) return true;
  return path.startsWith(`${href}/`);
}

/** The Chat tab does double duty: from anywhere else it opens the chat;
 *  tapped again while you're already there it starts a fresh one. (The
 *  dock used to carry a separate "New" button for that — two buttons that
 *  both landed you in a chat.) */
export function chatTabAction(pathname: string): "open" | "new" {
  return isNavActive(pathname, "/chat") ? "new" : "open";
}

/** Window event: "scroll this page back to the top" (the active tab, tapped). */
export { SCROLL_TOP_EVENT } from "./scrollTopEvent";

export type DockTap = "navigate" | "scroll-top" | "new-chat";

function pathOnly(pathname: string): string {
  return pathname.split("?")[0].split("#")[0];
}

/** What tapping the dock tab `href` does from `pathname`, the iOS way: a tab
 *  you are not on navigates; Chat tapped on chat starts a new one; a tab you
 *  are on scrolls back to the top at its root and pops back to the root from
 *  a sub-page (Edward → /settings/room/mind → Edward). */
export function dockTapAction(pathname: string, href: string): DockTap {
  if (!isNavActive(pathname, href)) return "navigate";
  if (href === "/chat") return "new-chat";
  return pathOnly(pathname) === href ? "scroll-top" : "navigate";
}

export type BarAction = "history" | "new-chat" | "search";

/** The phone presence bar's contextual slot: chat gets its history drawer
 *  and a new chat; every other page gets search (the command palette). */
export function barActions(pathname: string): BarAction[] {
  return isNavActive(pathname, "/chat") ? ["history", "new-chat"] : ["search"];
}

/** The Edward tab's emerald pip: shown while he wants the owner (a held send or an
 *  unread thought), with an accessible name that says which. */
export function edwardTabBadge(s: { unreadThoughts: number; approvalsWaiting: number }): { show: boolean; label: string } {
  const parts: string[] = [];
  if (s.approvalsWaiting > 0) parts.push(`${s.approvalsWaiting} waiting for your approval`);
  if (s.unreadThoughts > 0) parts.push(`${s.unreadThoughts} new thought${s.unreadThoughts === 1 ? "" : "s"}`);
  return { show: parts.length > 0, label: parts.length ? `Edward, ${parts.join(", ")}` : "Edward" };
}
