"use client";

// The calendar and Listening on the stage (2026-09-28). They read what the
// old tiles read, but speak the stage's language (lib/stageLive.ts):
// a sentence-case title (the one Edward gave the piece, else "Today" /
// "Listening"), Inter, plain words, the one clock format, no emerald (it
// means Edward), status colours only for status, and nothing that ticks
// for show — owner-facing copy says Listening, never heartbeat or triage.

import React, { useEffect, useMemo, useState } from "react";

import { useUserTimezone } from "@/lib/useTimezone";

import { usePoll } from "../hooks/usePoll";
import { fetchCalendar, fetchHeartbeatEvents } from "../lib/displayApi";
import { calendarStageView, listeningRows } from "../lib/stageLive";

export function CalendarStage({ title }: { title?: string | null }) {
  const tz = useUserTimezone();
  const { data } = usePoll(fetchCalendar, 60_000);
  // The "in 18 min" stays fresh; a text update, not motion.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(id);
  }, []);
  const view = useMemo(() => calendarStageView(data?.events ?? [], now, tz), [data, now, tz]);

  return (
    <div className="st-card st-calendar">
      <div className="st-card-title">{title || "Today"}</div>
      {view.next && <div className="st-card-next">Next: {view.next}</div>}
      <div className="st-list-rows">
        {view.rows.length === 0 && (
          <div className="st-meta">{data ? "Nothing on the calendar today." : "Reading the calendar…"}</div>
        )}
        {view.rows.map((r) => (
          <div key={r.id} className="st-list-row" data-past={r.past ? "true" : undefined}>
            <span className="st-list-label">{r.label}</span>
            {r.detail && <span className="st-list-detail">{r.detail}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

export function ListeningStage({ title }: { title?: string | null }) {
  const tz = useUserTimezone();
  const { data } = usePoll(() => fetchHeartbeatEvents(8), 10_000);
  const rows = listeningRows(data ?? [], tz);

  return (
    <div className="st-card st-listening">
      <div className="st-card-title">{title || "Listening"}</div>
      <div className="st-list-rows">
        {rows.length === 0 && <div className="st-meta">{data ? "All quiet." : "Listening…"}</div>}
        {rows.map((r) => (
          <div key={r.id} className="st-care-row">
            <div className="st-care-text">
              <span className="st-list-label">{r.who}</span>
              <span className="st-list-detail">{[r.what, r.when].filter(Boolean).join(" · ")}</span>
            </div>
            <span className="st-chip" data-tone={r.tone}>{r.word}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
