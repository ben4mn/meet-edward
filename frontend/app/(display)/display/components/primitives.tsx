"use client";

import React, { type CSSProperties, type ReactNode } from "react";

type Tone = "ok" | "warn" | "info" | "danger" | "mute";

const TONE_COLORS: Record<Tone, string> = {
  ok: "var(--ed-accent)",
  warn: "var(--ed-warn)",
  info: "var(--ed-info)",
  danger: "var(--ed-danger)",
  mute: "var(--ed-text-faint)",
};

export function StatusDot({
  tone = "ok",
  pulse = false,
  size = 8,
}: {
  tone?: Tone;
  pulse?: boolean;
  size?: number;
}) {
  const color = TONE_COLORS[tone];
  return (
    <span
      className={"mc-dot " + (pulse ? "mc-dot-pulse" : "")}
      style={{ background: color, color, width: size, height: size }}
    />
  );
}

export function Pill({
  tone = "mute",
  children,
  style,
}: {
  tone?: Tone;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <span className={`mc-pill mc-pill-${tone}`} style={style}>
      {children}
    </span>
  );
}

export function Card({
  title,
  right,
  children,
  className = "",
  style,
  padded = true,
  bodyStyle,
  staleLabel,
}: {
  title?: ReactNode;
  right?: ReactNode;
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
  padded?: boolean;
  bodyStyle?: CSSProperties;
  staleLabel?: string | null;
}) {
  return (
    <div className={`mc-card ${className}`} style={style}>
      {title && (
        <div className="mc-card-header">
          <div className="mc-card-title">{title}</div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {staleLabel && (
              <span
                title={`Last successful refresh ${staleLabel}`}
                className="mc-mono"
                style={{
                  fontSize: 9,
                  letterSpacing: "0.06em",
                  padding: "2px 6px",
                  borderRadius: 4,
                  background: "rgba(245, 158, 11, 0.12)",
                  color: "var(--ed-warn)",
                  textTransform: "uppercase",
                }}
              >
                stale · {staleLabel}
              </span>
            )}
            {right}
          </div>
        </div>
      )}
      <div className={padded ? "mc-card-body" : ""} style={bodyStyle}>
        {children}
      </div>
    </div>
  );
}

export function Bar({
  value,
  max = 100,
  tone = "ok",
}: {
  value: number;
  max?: number;
  tone?: Tone;
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="mc-bar">
      <span style={{ width: `${pct}%`, background: TONE_COLORS[tone] }} />
    </div>
  );
}
