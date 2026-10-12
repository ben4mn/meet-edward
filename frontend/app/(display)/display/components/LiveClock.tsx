"use client";

import React, { useEffect, useState } from "react";
import { formatClock } from "@/lib/time";
import { useOwnerName } from "@/lib/owner";
import { useUserTimezone } from "@/lib/useTimezone";

function greetingFor(hour: number): string {
  if (hour < 6) return "still up";
  if (hour < 12) return "good morning";
  if (hour < 18) return "good afternoon";
  if (hour < 22) return "good evening";
  return "late night";
}

function partsFor(now: Date, tz: string) {
  // Use Intl with explicit timeZone so the wall-clock matches the user's
  // configured zone, not the browser's resolved zone.
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  const hh = get("hour");
  const mm = get("minute");
  const ss = get("second");
  return { hh, mm, ss, hour: parseInt(hh, 10) };
}

/**
 * The clock. `face="stage"` is the stage's (2026-09-28): "5:52 pm" over
 * "Monday, Sep 28" in his one clock format, updated once a minute is enough
 * but read every few seconds so it turns over on time. The default face is
 * the old 24-hour mission-control clock; its sign-off names the owner (from
 * /api/auth/status) and the host when a caller has it (the vitals' `host`),
 * and leaves out whichever it does not know.
 */
export function LiveClock({
  face = "mono",
  title,
  host,
}: { face?: "mono" | "stage"; title?: string | null; host?: string | null } = {}) {
  const tz = useUserTimezone();
  const ownerName = useOwnerName();
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), face === "stage" ? 5_000 : 1000);
    return () => clearInterval(id);
  }, [face]);

  if (face === "stage") {
    const day = new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "long", month: "short", day: "numeric" }).format(now);
    return (
      <div className="st-clock">
        {title && <div className="st-overline">{title}</div>}
        <div className="st-clock-time">{formatClock(now, { tz })}</div>
        <div className="st-clock-day">{day}</div>
      </div>
    );
  }

  const { hh, mm, ss, hour } = partsFor(now, tz);
  const date = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(now);

  return (
    <div style={{ lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>
      <div
        style={{
          fontSize: "clamp(56px, 7vw, 92px)",
          fontWeight: 300,
          letterSpacing: "-0.02em",
          color: "var(--ed-text)",
        }}
      >
        {hh}:{mm}
        <span style={{ color: "var(--ed-text-faint)" }}>:{ss}</span>
      </div>
      <div
        style={{
          fontSize: 11,
          letterSpacing: "0.18em",
          textTransform: "uppercase",
          color: "var(--ed-text-dim)",
          marginTop: 8,
        }}
      >
        {date}
      </div>
      <div
        style={{
          marginTop: 10,
          fontSize: 11,
          color: "var(--ed-text-dim)",
          letterSpacing: "0.06em",
        }}
      >
        {greetingFor(hour)}
        {ownerName ? `, ${ownerName.toLowerCase()}` : ""}
        {host?.trim() ? ` · ${host.trim().toLowerCase()}` : ""}
      </div>
    </div>
  );
}
