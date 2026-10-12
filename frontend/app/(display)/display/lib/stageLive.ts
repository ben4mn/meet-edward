/** The live pieces' stage faces (2026-09-28) — pure, tested in __tests__/stageLivePieces.test.tsx.
 *
 * The calendar and Listening pieces read the same endpoints the old tile
 * grid did, but on the stage they speak its language: sentence case, Inter,
 * plain words, the one clock format ("7:34 pm"), no emerald (it means
 * Edward), and status colours only for status.
 */

import { formatClock } from "@/lib/time";

import { isAllDayEvent, nextUpEvent } from "./calendarDay";
import type { HeartbeatEvent } from "./displayApi";
import type { CalendarEvent } from "./types";

// new Date(iso), not parseIso: apple-mcp's times can be naive local ones.
function clock(iso: string, tz: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  try {
    return formatClock(d, { tz }) || "";
  } catch {
    return "";
  }
}

/** "now", "in 18 min", "in 1 hr 20 min", "in 5 hr". */
export function untilWords(ms: number): string {
  const m = Math.round(ms / 60_000);
  if (m <= 0) return "now";
  if (m < 60) return `in ${m} min`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return mm ? `in ${h} hr ${mm} min` : `in ${h} hr`;
}

export interface CalendarStageRow {
  id: string;
  label: string;
  /** "2:18 pm · Zoom", "all day". */
  detail: string;
  past: boolean;
}

export interface CalendarStageView {
  /** "1:1 with Priya · in 18 min", or null when nothing timed is ahead. */
  next: string | null;
  rows: CalendarStageRow[];
}

export function calendarStageView(events: readonly CalendarEvent[], now: Date, tz: string): CalendarStageView {
  const next = nextUpEvent(events, now, tz);
  const rows = events.map((e) => {
    const when = isAllDayEvent(e, tz) ? "all day" : clock(e.start, tz);
    const detail = [when, e.where].filter(Boolean).join(" · ");
    const end = new Date(e.end).getTime();
    return { id: e.id, label: e.title, detail, past: Number.isFinite(end) && end < now.getTime() };
  });
  return {
    next: next ? `${next.title} · ${untilWords(new Date(next.start).getTime() - now.getTime())}` : null,
    rows,
  };
}

/** What Listening did with a message, in plain words (never triage's). */
export function listeningWord(status: string | null | undefined): { word: string; tone: "warn" | "mute" } {
  switch (status) {
    case "escalated":
      return { word: "needs you", tone: "warn" };
    case "mention":
      return { word: "mentioned you", tone: "warn" };
    case "calendar_urgent":
      return { word: "coming up", tone: "warn" };
    case "acted":
      return { word: "handled", tone: "mute" };
    case "noted":
      return { word: "noted", tone: "mute" };
    case "dismissed":
      return { word: "nothing needed", tone: "mute" };
    default:
      return { word: "reading", tone: "mute" };
  }
}

export interface ListeningRow {
  id: string;
  who: string;
  what: string;
  /** "3:28 pm" */
  when: string;
  word: string;
  tone: "warn" | "mute";
}

export function listeningRows(events: readonly HeartbeatEvent[], tz: string): ListeningRow[] {
  return events.map((e) => {
    const { word, tone } = listeningWord(e.triage_status);
    return {
      id: e.id,
      who: e.contact_name || e.sender || e.source,
      what: e.summary || "",
      when: clock(e.created_at, tz),
      word,
      tone,
    };
  });
}
