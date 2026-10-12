"use client";

import { usePoll } from "../hooks/usePoll";
import { fetchRecents } from "../lib/displayApi";
import { isStale, staleAgeLabel } from "../lib/staleness";
import { Card } from "./primitives";
import { useUserTimezone } from "@/lib/useTimezone";

function fmtClock(iso: string | null, tz: string): string {
  if (!iso) return "--:--";
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

/** `title`: the one Edward gave the piece on the stage. */
export function RecentsPanel({ title }: { title?: string | null } = {}) {
  const tz = useUserTimezone();
  const { data, lastSuccessAt } = usePoll(fetchRecents, 15_000);
  const items = data?.items ?? [];
  const staleLabel = isStale(lastSuccessAt, 15_000) ? staleAgeLabel(lastSuccessAt) : null;

  return (
    <Card title={title ? <>{title}</> : <><span>↻</span> EDWARD · RECENT ACTIONS</>} staleLabel={staleLabel}>
      {items.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--ed-text-faint)", padding: "8px 4px" }}>
          — quiet so far
        </div>
      ) : (
        items.slice(0, 7).map((r, i) => (
          <div
            key={i}
            className="mc-row"
            style={{ gridTemplateColumns: "44px 1fr auto" }}
          >
            <span className="mc-mono" style={{ color: "var(--ed-text-faint)", fontSize: 10 }}>
              {fmtClock(r.t, tz)}
            </span>
            <span style={{ fontSize: 12, color: "var(--ed-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              <span style={{ color: "var(--ed-accent)", fontWeight: 500 }}>{r.verb}</span>{" "}
              <span style={{ color: "var(--ed-text-dim)" }}>{r.obj}</span>
            </span>
            <span
              style={{
                fontSize: 10,
                color: "var(--ed-text-faint)",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                whiteSpace: "nowrap",
              }}
            >
              {r.status}
            </span>
          </div>
        ))
      )}
    </Card>
  );
}
