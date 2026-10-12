// Ways into a new chat (2026-09-26): the empty chat shows Edward, his
// greeting, and a quiet suggestion taken from Today — the top thing that
// needs the owner (worded as a prompt to handle it) — plus "Resume" for their last
// thread. (A live goal and one of Edward's ideas were suggestions too until
// unit G took them off Today, 2026-10-11.) A suggestion only fills the
// composer (`/chat?prompt=`); the owner still presses send. Pure.

import type { TodayConversation, TodayFeed, TodayNeed } from "@/components/today/todayModel";

export interface ChatSuggestion {
  id: string;
  kind: "need";
  /** What the chip says. */
  label: string;
  /** What lands in the composer. */
  prompt: string;
  /** The need's kind (dose, calendar, …) for its icon. */
  needKind?: string;
}

type FeedSlice = Partial<Pick<TodayFeed, "needs" | "conversations">> & {
  needs?: TodayNeed[];
  conversations?: TodayConversation[];
};

/** Sources that are the owner's own threads (not Edward's heartbeat, thought or event threads). */
const OWNERS_SOURCES = new Set(["user", "voice", "widget", "alexa", "twilio", ""]);

function sentence(text: string): string {
  const t = text.trim();
  return /[.!?…]$/.test(t) ? t : `${t}.`;
}

function lowerFirst(text: string): string {
  return text ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}

function needPrompt(need: TodayNeed): string {
  const title = need.title.trim();
  const detail = (need.detail || "").trim();
  if (need.kind === "dose" && detail) return `${title} — ${sentence(lowerFirst(detail))} What should I do?`;
  return `Help me with this: ${sentence(title)}`;
}

export function chatSuggestions(feed: FeedSlice | null | undefined, max = 3): ChatSuggestion[] {
  if (!feed) return [];
  const out: ChatSuggestion[] = [];
  const need = (feed.needs ?? []).find((n) => n.title?.trim());
  if (need) out.push({ id: `need-${need.id}`, kind: "need", label: need.title.trim(), prompt: needPrompt(need), needKind: need.kind });
  return out.slice(0, Math.max(0, max));
}

/** The owner's most recent conversation, to pick up where they left off. */
export function resumeTarget(feed: FeedSlice | null | undefined): { id: string; title: string; href: string } | null {
  const c = (feed?.conversations ?? []).find((x) => OWNERS_SOURCES.has(x.source ?? "") && x.title?.trim());
  return c ? { id: c.id, title: c.title.trim(), href: c.href || `/chat?c=${encodeURIComponent(c.id)}` } : null;
}
