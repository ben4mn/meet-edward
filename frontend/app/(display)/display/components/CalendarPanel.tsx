"use client";

import React, { useEffect, useMemo, useState } from "react";
import { usePoll } from "../hooks/usePoll";
import { isAllDayEvent, nextUpEvent } from "../lib/calendarDay";
import { fetchCalendar } from "../lib/displayApi";
import { isStale, staleAgeLabel } from "../lib/staleness";
import type { CalendarEvent } from "../lib/types";
import { Card, Pill } from "./primitives";
import { formatClock } from "@/lib/time";
import { useUserTimezone } from "@/lib/useTimezone";

// new Date(iso), not parseIso: apple-mcp's times can be naive local ones.
function fmtTime(iso: string, tz: string): string {
  try {
    return formatClock(new Date(iso), { tz }) || "—";
  } catch {
    return "—";
  }
}

function minutesUntil(iso: string, now: Date): number {
  return Math.round((new Date(iso).getTime() - now.getTime()) / 60000);
}

function fmtUntil(m: number): string {
  if (m <= 0) return "now";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return mm ? `${h}h ${mm}m` : `${h}h`;
}

const KIND_COLORS: Record<CalendarEvent["kind"], string> = {
  work: "var(--ed-info)",
  focus: "var(--ed-accent)",
  personal: "var(--ed-warn)",
};

export function CalendarPanel() {
  const tz = useUserTimezone();
  const { data, lastSuccessAt } = usePoll(fetchCalendar, 60_000);
  const staleLabel = isStale(lastSuccessAt, 60_000) ? staleAgeLabel(lastSuccessAt) : null;
  // Tick once a minute so the "in 18m" countdown stays fresh.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(id);
  }, []);

  const events = useMemo(() => data?.events ?? [], [data]);
  // The next real meeting: an all-day event is part of the day, not "next
  // up", and a past event is never "in now".
  const next = useMemo(() => nextUpEvent(events, now, tz), [events, now, tz]);

  return (
    <Card
      title={
        <>
          <span style={{ color: "var(--ed-accent)" }}>▣</span> CALENDAR · TODAY
        </>
      }
      right={<Pill tone="ok">{events.length} events</Pill>}
      style={{ height: "100%", display: "flex", flexDirection: "column" }}
      bodyStyle={{ overflow: "auto", flex: 1, minHeight: 0 }}
      staleLabel={staleLabel}
    >
      {next && (
        <div style={{ marginBottom: 14 }}>
          <div
            style={{
              fontSize: 10,
              letterSpacing: "0.16em",
              color: "var(--ed-text-dim)",
              marginBottom: 6,
            }}
          >
            NEXT UP
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <div style={{ fontSize: 22, fontWeight: 500, color: "var(--ed-text)" }}>{next.title}</div>
            <div className="mc-mono" style={{ fontSize: 13, color: "var(--ed-accent)" }}>
              in {fmtUntil(minutesUntil(next.start, now))}
            </div>
          </div>
          <div style={{ fontSize: 12, color: "var(--ed-text-dim)", marginTop: 4 }}>
            {fmtTime(next.start, tz)} – {fmtTime(next.end, tz)}
            {next.where ? ` · ${next.where}` : ""}
          </div>
        </div>
      )}

      <div style={{ position: "relative", paddingLeft: 14 }}>
        <div
          style={{
            position: "absolute",
            left: 4,
            top: 4,
            bottom: 4,
            width: 1,
            background:
              "linear-gradient(180deg, var(--ed-border-strong), var(--ed-border) 50%, transparent)",
          }}
        />
        {events.map((e) => {
          const isPast = new Date(e.end) < now;
          const isNext = next && e.id === next.id;
          const dotColor = isNext
            ? "var(--ed-accent)"
            : isPast
              ? "var(--ed-text-faint)"
              : "var(--ed-text-dim)";
          return (
            <div key={e.id} style={{ position: "relative", padding: "5px 0" }}>
              <span
                style={{
                  position: "absolute",
                  left: -14,
                  top: 11,
                  width: 9,
                  height: 9,
                  borderRadius: "50%",
                  background: isNext ? dotColor : "transparent",
                  border: `1.5px solid ${dotColor}`,
                  boxShadow: isNext ? "0 0 8px var(--ed-accent)" : "none",
                }}
              />
              <div style={{ display: "grid", gridTemplateColumns: "60px 1fr auto", gap: 10, alignItems: "center" }}>
                <span
                  className="mc-mono"
                  style={{
                    fontSize: 11,
                    color: isPast ? "var(--ed-text-faint)" : "var(--ed-text-dim)",
                  }}
                >
                  {isAllDayEvent(e, tz) ? "all day" : fmtTime(e.start, tz)}
                </span>
                <span
                  style={{
                    fontSize: 13,
                    color: isPast ? "var(--ed-text-faint)" : "var(--ed-text)",
                    textDecoration: isPast ? "line-through" : "none",
                    textDecorationColor: "var(--ed-text-faint)",
                  }}
                >
                  {e.title}
                  {e.where && (
                    <span style={{ color: "var(--ed-text-faint)", marginLeft: 8, fontSize: 11 }}>
                      · {e.where}
                    </span>
                  )}
                </span>
                <span
                  style={{
                    fontSize: 9,
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                    color: KIND_COLORS[e.kind] || "var(--ed-text-dim)",
                  }}
                >
                  {e.kind}
                </span>
              </div>
            </div>
          );
        })}
        {events.length === 0 && (
          <div style={{ fontSize: 12, color: "var(--ed-text-faint)", padding: "8px 0" }}>
            — nothing on the books
          </div>
        )}
      </div>
    </Card>
  );
}
