// When does a route change end live voice? (2026-09-26) — pure, pinned in
// __tests__/voiceRoute.test.ts; ChatContext runs it on every pathname change.
//
// Leaving /chat while voice is on (a push tap router.push()es, a desktop
// shortcut, back) unmounts the voice view but left isVoiceMode on: presence
// read "Listening" with no mic open, the queue drain held, and /chat
// reopened the mic unasked. Voice ends on the change *away* from chat — not
// whenever the path is not chat, so the palette's Talk live (push /chat,
// then enter voice 60 ms later) keeps working.

import { isNavActive } from "@/components/layout/navItems";

export function shouldEndVoiceOnRoute(prev: string, next: string, isVoiceMode: boolean): boolean {
  if (!isVoiceMode || prev === next) return false;
  return isNavActive(prev, "/chat") && !isNavActive(next, "/chat");
}
