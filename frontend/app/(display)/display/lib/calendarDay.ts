// All-day events on Edward's screen — pure (__tests__/calendarDay.test.tsx).
//
// An all-day event ("Rowan's sister in town") is part of the day, not an
// appointment: it reads "all day" and never takes NEXT UP from the real next
// meeting. The backend's calendar normaliser does not pass apple-mcp's
// isAllDay through yet, so an event is also all-day by its shape: it starts
// at local midnight and runs to a later local midnight (≥ 23 h, for the DST
// days). An explicit `all_day` from the backend always wins.

import type { CalendarEvent } from "./types";

function localClock(iso: string, tz: string): { h: number; m: number; s: number } | null {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz || undefined,
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
      hourCycle: "h23",
    }).formatToParts(d);
    const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? NaN);
    return { h: get("hour") % 24, m: get("minute"), s: get("second") };
  } catch {
    return null;
  }
}

function atMidnight(iso: string, tz: string): boolean {
  const c = localClock(iso, tz);
  return !!c && c.h === 0 && c.m === 0 && c.s === 0;
}

const MIN_ALL_DAY_MS = 23 * 3_600_000;

/** Enough of an event to tell (the hub's events may lack an end). */
export interface DayEventLike {
  start: string;
  end?: string | null;
  all_day?: boolean;
}

export function isAllDayEvent(e: DayEventLike, tz: string): boolean {
  if (typeof e.all_day === "boolean") return e.all_day;
  if (!e.end) return false;
  const start = new Date(e.start).getTime();
  const end = new Date(e.end).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return false;
  return end - start >= MIN_ALL_DAY_MS && atMidnight(e.start, tz) && atMidnight(e.end, tz);
}

/** The next timed event that has not ended — never an all-day one, never a past one. */
export function nextUpEvent<E extends CalendarEvent>(events: readonly E[], now: Date, tz: string): E | null {
  const t = now.getTime();
  for (const e of events) {
    if (isAllDayEvent(e, tz)) continue;
    const end = new Date(e.end).getTime();
    if (Number.isFinite(end) && end > t) return e;
  }
  return null;
}
