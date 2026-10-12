"use client";

// A single ratio against a limit — the right form for CPU/memory/disk.
// Not a radial dial: on a 7" panel a horizontal track is far more legible
// per pixel, and several stacked meters are directly comparable in a way
// that a row of circles never is.
//
// The value is always printed. Status colour is a second channel on top
// of the number, never the only one carrying the meaning.

export type MeterLevel = "good" | "warning" | "critical";

export function levelFor(pct: number, warn = 70, crit = 88): MeterLevel {
  if (pct >= crit) return "critical";
  if (pct >= warn) return "warning";
  return "good";
}

export function Meter({
  label,
  pct,
  caption,
  warn,
  crit,
}: {
  label: string;
  pct: number;
  caption?: string;
  warn?: number;
  crit?: number;
}) {
  const clamped = Math.max(0, Math.min(100, pct));
  const level = levelFor(clamped, warn, crit);

  return (
    <div className="hub-meter">
      <div className="hub-meter-head">
        <span className="hub-meter-label">{label}</span>
        <span className="hub-meter-value">
          {Math.round(clamped)}%
          {caption && <span className="hub-meter-caption"> · {caption}</span>}
        </span>
      </div>
      <div
        className="hub-meter-track"
        role="meter"
        aria-valuenow={Math.round(clamped)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div className="hub-meter-fill" data-level={level} style={{ width: `${clamped}%` }} />
      </div>
    </div>
  );
}
