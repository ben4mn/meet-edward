"use client";

import React, { useState } from "react";
import { usePoll } from "../hooks/usePoll";
import { useSignals } from "../hooks/useSharedPoll";
import { fetchAlerts } from "../lib/displayApi";
import { alertTimeLabel } from "../lib/screenCopy";
import type { AlertItem, AlertLevel } from "../lib/types";
import { Pill, StatusDot } from "./primitives";

const TONE_FROM_LEVEL: Record<AlertLevel, "ok" | "warn" | "info" | "danger"> = {
  ok: "ok",
  warn: "warn",
  info: "info",
  danger: "danger",
};

export function AlertStrip() {
  const { data } = usePoll(fetchAlerts, 30_000);
  // The same signals poll Edward reads (one request per 4 s for the whole
  // screen, not one per component every 2 s).
  const { data: signals } = useSignals();
  const [open, setOpen] = useState(false);

  const items: AlertItem[] = data?.items ?? [];
  const head = items[0];
  const word = signals?.pending_interrupt ?? null;

  if (word) {
    // Edward wants a word: an interrupt landed on this screen. Tapping
    // acknowledges it (so it does not escalate to a push) and opens the
    // conversation on the display's compose pane.
    return (
      <button
        onClick={() => {
          fetch("/api/push/ack", {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ delivery_id: word.delivery_id, source: "display-tap" }),
          }).catch(() => {});
          try {
            localStorage.setItem("edward_display_conversation_id", word.url.split("c=")[1] ?? "");
          } catch {}
        }}
        className="mc-card"
        style={{
          width: "100%",
          padding: "10px 14px",
          display: "flex",
          alignItems: "center",
          gap: 14,
          textAlign: "left",
          cursor: "pointer",
          border: "1px solid var(--ed-accent)",
        }}
      >
        <span style={{ fontSize: 9, letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--ed-accent)" }}>
          Edward wants a word
        </span>
        <StatusDot tone={word.urgency === "high" ? "danger" : "info"} pulse />
        <div style={{ flex: 1, fontSize: 13, color: "var(--ed-text)" }}>
          <strong>{word.title}</strong> — {word.body}
        </div>
      </button>
    );
  }

  if (!head) {
    return (
      <div
        className="mc-card"
        style={{
          padding: "10px 14px",
          display: "flex",
          alignItems: "center",
          gap: 14,
          color: "var(--ed-text-faint)",
          fontSize: 12,
        }}
      >
        <span style={{ fontSize: 9, letterSpacing: "0.16em", textTransform: "uppercase" }}>Alerts</span>
        <span style={{ flex: 1 }}>Nothing pressing.</span>
      </div>
    );
  }

  return (
    <div style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="mc-card"
        style={{
          width: "100%",
          padding: "10px 14px",
          display: "flex",
          alignItems: "center",
          gap: 14,
          textAlign: "left",
          cursor: items.length > 1 ? "pointer" : "default",
          border: "1px solid var(--ed-border)",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span
            style={{
              fontSize: 9,
              letterSpacing: "0.16em",
              color: "var(--ed-text-dim)",
              textTransform: "uppercase",
            }}
          >
            Alert
          </span>
          <span style={{ fontSize: 10, color: "var(--ed-text-faint)" }}>{alertTimeLabel(head.t)}</span>
        </div>
        <div style={{ width: 1, height: 30, background: "var(--ed-border)" }} />
        <StatusDot tone={TONE_FROM_LEVEL[head.level]} pulse />
        <div style={{ flex: 1, fontSize: 13, color: "var(--ed-text)" }}>{head.text}</div>
        {items.length > 1 && <Pill tone="mute">{items.length - 1} more</Pill>}
      </button>

      {open && items.length > 1 && (
        <div
          className="mc-card"
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            right: 0,
            zIndex: 10,
            padding: 8,
          }}
        >
          {items.slice(1).map((a, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                gap: 10,
                padding: "6px 8px",
                borderTop: i ? "1px dashed rgba(255,255,255,0.05)" : "none",
              }}
            >
              <StatusDot tone={TONE_FROM_LEVEL[a.level]} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, color: "var(--ed-text)" }}>{a.text}</div>
                <div style={{ fontSize: 10, color: "var(--ed-text-faint)", marginTop: 2 }}>{alertTimeLabel(a.t)}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
