"use client";

import { usePoll } from "../hooks/usePoll";
import { fetchWorkers } from "../lib/displayApi";
import { isStale, staleAgeLabel } from "../lib/staleness";
import { Card, StatusDot } from "./primitives";

/** `title`: the one Edward gave the piece on the stage. */
export function WorkersPanel({ title }: { title?: string | null } = {}) {
  const { data, lastSuccessAt } = usePoll(fetchWorkers, 10_000);
  const workers = data?.workers ?? [];
  const running = data?.running_count ?? 0;
  const staleLabel = isStale(lastSuccessAt, 10_000) ? staleAgeLabel(lastSuccessAt) : null;

  return (
    <Card title={title ? <>{title}</> : <><span>⛭</span> WORKERS · {running} RUNNING</>} staleLabel={staleLabel}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 16px" }}>
        {workers.map((w, i) => (
          <div
            key={i}
            style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", fontSize: 11 }}
          >
            <StatusDot tone={w.tone} pulse={w.tone === "ok"} />
            <span className="mc-mono" style={{ color: "var(--ed-text)" }}>{w.name}</span>
            <span style={{ flex: 1 }} />
            <span style={{ fontSize: 10, color: "var(--ed-text-faint)" }}>{w.last}</span>
          </div>
        ))}
        {workers.length === 0 && (
          <div style={{ fontSize: 12, color: "var(--ed-text-faint)", padding: "4px 0" }}>—</div>
        )}
      </div>
    </Card>
  );
}
