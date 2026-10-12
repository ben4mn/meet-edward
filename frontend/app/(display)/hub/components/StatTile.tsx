"use client";

// A headline number. The number is the chart — no plot, no decoration.
// Values wear text tokens, not status colour.

export function StatTile({
  value,
  label,
  sub,
}: {
  value: string;
  label: string;
  sub?: string;
}) {
  return (
    <div className="hub-stat">
      <div className="hub-stat-value">{value}</div>
      <div className="hub-stat-label">{label}</div>
      {sub && <div className="hub-stat-sub">{sub}</div>}
    </div>
  );
}
