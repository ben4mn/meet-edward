"use client";

import React, { useEffect, useReducer, useState } from "react";
import { usePoll } from "../hooks/usePoll";
import {
  fetchHeartbeatEvents,
  fetchHeartbeatStatus,
  type HeartbeatEvent,
} from "../lib/displayApi";
import {
  filterHeartbeatEvents,
  heartbeatPanelReducer,
  initialHeartbeatPanelState,
  type HeartbeatFilter,
} from "../lib/heartbeatPanelState";
import { isStale, staleAgeLabel } from "../lib/staleness";
import { Card, Pill, StatusDot } from "./primitives";
import { PulseLine } from "./PulseLine";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { joinClockParts } from "@/lib/time";
import { useUserTimezone } from "@/lib/useTimezone";

const TRIAGE_TONE: Record<string, "ok" | "warn" | "info" | "danger" | "mute"> = {
  pending: "ok",
  noted: "info",
  acted: "info",
  escalated: "danger",
  dismissed: "mute",
  mention: "warn",
  calendar_urgent: "warn",
};

const SOURCE_COLORS: Record<string, string> = {
  imessage: "var(--ed-info)",
  calendar: "var(--ed-warn)",
  email: "var(--ed-accent)",
  gmail: "var(--ed-accent)",
};

const FILTERS: { id: HeartbeatFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "urgent", label: "Urgent" },
  { id: "pending", label: "Pending" },
  { id: "done", label: "Done" },
];

const ROW_GRID = "minmax(40px,56px) 14px minmax(80px,140px) minmax(0,1fr) auto";

function fmtClock(iso: string, tz: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date(iso));
  } catch {
    return "--:--";
  }
}

/** "Sep 27, 9:00 am" — the app's one clock (L1, 2026-09-27). */
function fmtFullTime(iso: string, tz: string): string {
  try {
    return joinClockParts(
      new Intl.DateTimeFormat("en-US", {
        timeZone: tz,
        hour: "numeric",
        minute: "2-digit",
        month: "short",
        day: "numeric",
      }).formatToParts(new Date(iso)),
    );
  } catch {
    return iso;
  }
}

export function HeartbeatPanel() {
  const tz = useUserTimezone();
  const { data: events, lastSuccessAt } = usePoll(() => fetchHeartbeatEvents(20), 10_000);
  const { data: status } = usePoll(fetchHeartbeatStatus, 30_000);
  const staleLabel = isStale(lastSuccessAt, 10_000) ? staleAgeLabel(lastSuccessAt) : null;

  const [state, dispatch] = useReducer(heartbeatPanelReducer, initialHeartbeatPanelState);

  // Cycle counter — local tick to give the header some life between polls.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const items: HeartbeatEvent[] = events ?? [];
  const filtered = filterHeartbeatEvents(items, state.filter).slice(0, 12);
  const buffered = items.length;

  const tracksLine = status
    ? Object.entries(status.tracks)
        .filter(([, t]) => t.enabled)
        .map(([k]) => k)
        .join(" · ")
    : "—";

  return (
    <Card
      title={
        <>
          <StatusDot tone="ok" pulse /> HEARTBEAT · LIVE
        </>
      }
      right={
        <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span className="mc-mono" style={{ fontSize: 10, color: "var(--ed-text-dim)" }}>
            cycle · {tick % 60}s
          </span>
          <Pill tone={status?.running ? "ok" : "mute"}>
            {status?.running ? "listening" : "idle"}
          </Pill>
        </span>
      }
      style={{ height: "100%", display: "flex", flexDirection: "column" }}
      bodyStyle={{ overflow: "auto", flex: 1, minHeight: 0 }}
      staleLabel={staleLabel}
    >
      <div style={{ marginBottom: 8 }}>
        <PulseLine />
      </div>

      <div
        role="tablist"
        aria-label="Heartbeat filter"
        style={{ display: "flex", gap: 6, marginBottom: 8, flexWrap: "wrap" }}
      >
        {FILTERS.map((f) => {
          const active = state.filter === f.id;
          return (
            <button
              key={f.id}
              role="tab"
              aria-selected={active}
              onClick={() => dispatch({ type: "SET_FILTER", filter: f.id })}
              className="mc-mono"
              style={{
                fontSize: 10,
                letterSpacing: "0.05em",
                padding: "3px 8px",
                borderRadius: 999,
                border: `1px solid ${active ? "var(--ed-accent)" : "var(--ed-border)"}`,
                background: active ? "var(--ed-accent-soft)" : "transparent",
                color: active ? "var(--ed-accent)" : "var(--ed-text-dim)",
                cursor: "pointer",
                textTransform: "uppercase",
              }}
            >
              {f.label}
            </button>
          );
        })}
      </div>

      <div
        className="mc-mono"
        style={{
          fontSize: 10,
          color: "var(--ed-text-dim)",
          display: "flex",
          justifyContent: "space-between",
          marginBottom: 8,
        }}
      >
        <span>{tracksLine || "—"}</span>
        <span>↳ {buffered} events buffered</span>
      </div>

      <div>
        {filtered.map((e) => {
          const tone = TRIAGE_TONE[e.triage_status] || "mute";
          const important =
            e.triage_status === "mention" || e.triage_status === "escalated";
          const sourceColor = SOURCE_COLORS[e.source] || "var(--ed-text-faint)";
          const expanded = state.expandedId === e.id;
          return (
            <div key={e.id}>
              <div
                role="button"
                tabIndex={0}
                aria-expanded={expanded}
                onClick={() => dispatch({ type: "TOGGLE_EXPAND", id: e.id })}
                onKeyDown={(ev) => {
                  if (ev.key === "Enter" || ev.key === " ") {
                    ev.preventDefault();
                    dispatch({ type: "TOGGLE_EXPAND", id: e.id });
                  }
                }}
                className="mc-row"
                style={{
                  gridTemplateColumns: ROW_GRID,
                  cursor: "pointer",
                  background: expanded ? "var(--ed-surface-2)" : undefined,
                  borderRadius: 4,
                  transition: "background-color 0.15s ease",
                }}
              >
                <span className="mc-mono" style={{ color: "var(--ed-text-faint)", fontSize: 10 }}>
                  {fmtClock(e.created_at, tz)}
                </span>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: 2,
                    background: sourceColor,
                    opacity: 0.7,
                  }}
                />
                <span
                  style={{
                    color: "var(--ed-text)",
                    fontSize: 12,
                    fontWeight: important ? 500 : 400,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {e.contact_name || e.sender || e.source}
                </span>
                <span
                  style={{
                    color: important ? "var(--ed-text)" : "var(--ed-text-dim)",
                    fontSize: 12,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {e.summary || ""}
                </span>
                <Pill tone={tone} style={{ fontSize: 9 }}>
                  {(e.triage_status || "pending").replace("_", " ")}
                </Pill>
              </div>
              {expanded && (
                <div
                  data-testid="heartbeat-expanded"
                  style={{
                    padding: "8px 12px 10px",
                    fontSize: 11,
                    color: "var(--ed-text-dim)",
                    background: "var(--ed-surface-2)",
                    borderRadius: 4,
                    marginBottom: 4,
                  }}
                >
                  <div style={{ display: "flex", gap: 12, marginBottom: 6, flexWrap: "wrap" }}>
                    <span className="mc-mono" style={{ fontSize: 10 }}>
                      {fmtFullTime(e.created_at, tz)}
                    </span>
                    <span className="mc-mono" style={{ fontSize: 10 }}>
                      {e.source} · {e.event_type}
                    </span>
                    {e.is_from_user && (
                      <span className="mc-mono" style={{ fontSize: 10, color: "var(--ed-accent)" }}>
                        from you
                      </span>
                    )}
                  </div>
                  <div style={{ color: "var(--ed-text)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                    {e.summary || "(no summary)"}
                  </div>
                </div>
              )}
            </div>
          );
        })}

        {filtered.length === 0 && items.length === 0 && (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 8,
              padding: "24px 0",
              color: "var(--ed-text-faint)",
            }}
          >
            <EdwardAvatar size={56} mood="idle" animated={false} />
            <span style={{ fontSize: 12 }}>All quiet.</span>
          </div>
        )}
        {filtered.length === 0 && items.length > 0 && (
          <div style={{ fontSize: 12, color: "var(--ed-text-faint)", padding: "8px 4px" }}>
            — no events match the {state.filter} filter
          </div>
        )}
      </div>
    </Card>
  );
}
