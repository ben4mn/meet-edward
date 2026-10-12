"use client";

import "../hub.css";
import { HubRoot } from "../components/HubRoot";

import { useMemo } from "react";

import { formatClock } from "@/lib/time";

import { usePoll } from "../../display/hooks/usePoll";
import { isAllDayEvent } from "../../display/lib/calendarDay";
import { useWakeLock } from "../../display/hooks/useWakeLock";
import { HubHeader } from "../components/HubHeader";
import { useIdleRedirect } from "../lib/useIdleRedirect";
import { fetchCalendar, fetchScheduledEvents } from "../lib/hubApi";
import type { CalendarResponse, ScheduledEvent } from "../lib/types";

const POLL_MS = 60_000;

// new Date(iso), not parseIso: the calendar's times can be naive local ones.
function clockOf(iso: string): string {
  return formatClock(new Date(iso));
}

function isToday(iso: string): boolean {
  const d = new Date(iso);
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

export default function HubTodayPage() {
  useWakeLock(true);
  useIdleRedirect();

  const { data: calendar } = usePoll<CalendarResponse>(fetchCalendar, POLL_MS);
  const { data: events } = usePoll<ScheduledEvent[]>(fetchScheduledEvents, POLL_MS);

  const now = Date.now();

  const todayEvents = useMemo(
    () => (calendar?.events ?? []).filter((e) => isToday(e.start)),
    [calendar],
  );

  const upcomingReminders = useMemo(
    () =>
      (events ?? [])
        .filter((e) => e.next_fire_at && new Date(e.next_fire_at).getTime() > now - 60_000)
        .slice(0, 8),
    [events, now],
  );

  return (
    <HubRoot>
      <HubHeader title="Today" />

      <main className="hub-two-col">
        <section className="hub-card">
          <h2 className="hub-card-title">Calendar</h2>
          {todayEvents.length ? (
            <div className="hub-rows">
              {todayEvents.map((e, i) => {
                // The backend sends no all-day flag yet: same rule as /screen
                // (display/lib/calendarDay.ts). An all-day event is past only
                // once the day is over.
                const allDay = isAllDayEvent(e, "");
                const past = allDay
                  ? !!e.end && new Date(e.end).getTime() < now
                  : new Date(e.start).getTime() < now;
                return (
                  <div className="hub-row" data-past={past} key={`${e.title}-${i}`}>
                    <span className="hub-row-time">
                      {allDay ? "all day" : clockOf(e.start)}
                    </span>
                    <span className="hub-row-text">{e.title}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="hub-empty-lg">Nothing on the calendar today</div>
          )}
        </section>

        <section className="hub-card">
          <h2 className="hub-card-title">Edward&apos;s reminders</h2>
          {upcomingReminders.length ? (
            <div className="hub-rows">
              {upcomingReminders.map((e) => (
                <div className="hub-row" key={e.id}>
                  <span className="hub-row-time">{clockOf(e.next_fire_at)}</span>
                  <span className="hub-row-text">
                    {e.description}
                    {e.recurrence_pattern && <span className="hub-row-tag">repeats</span>}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="hub-empty-lg">Nothing scheduled</div>
          )}
        </section>
      </main>
    </HubRoot>
  );
}
