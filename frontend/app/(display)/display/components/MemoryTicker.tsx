"use client";

import { usePoll } from "../hooks/usePoll";
import { fetchMemoryTicker } from "../lib/displayApi";
import { isStale, staleAgeLabel } from "../lib/staleness";
import { Card, Pill } from "./primitives";

const FALLBACK = [
  "Listening keeps watch while you are away",
  "memories index in pgvector · 384-dim embeddings · cosine similarity",
];

/** `title`: the one Edward gave the piece on the stage. */
export function MemoryTicker({ title }: { title?: string | null } = {}) {
  const { data, lastSuccessAt } = usePoll(fetchMemoryTicker, 5 * 60 * 1000);
  const items = (data?.items?.length ? data.items : FALLBACK) as string[];
  const staleLabel = isStale(lastSuccessAt, 5 * 60 * 1000) ? staleAgeLabel(lastSuccessAt) : null;
  // Duplicate the list so the marquee animation can loop seamlessly
  // (shifts -50%, lands on the start of the second copy).
  const doubled = [...items, ...items];
  const total = data?.total ?? 0;

  return (
    <Card
      title={
        title ? (
          <>{title}</>
        ) : (
          <>
            <span style={{ color: "var(--ed-accent)" }}>◆</span> MEMORY ·{" "}
            {total ? total.toLocaleString() : "—"} ITEMS
          </>
        )
      }
      right={<Pill tone="mute">indexed · pgvector</Pill>}
      padded={false}
      staleLabel={staleLabel}
    >
      <div style={{ overflow: "hidden", padding: "10px 0", position: "relative" }}>
        <div className="mc-marquee">
          {doubled.map((m, i) => (
            <span
              key={i}
              style={{ padding: "0 32px", fontSize: 13, color: "var(--ed-text-dim)" }}
            >
              <span style={{ color: "var(--ed-accent)", marginRight: 8 }}>·</span>
              {m}
            </span>
          ))}
        </div>
        <div
          style={{
            position: "absolute",
            inset: 0,
            pointerEvents: "none",
            background:
              "linear-gradient(90deg, var(--ed-bg-2), transparent 8%, transparent 92%, var(--ed-bg-2))",
          }}
        />
      </div>
    </Card>
  );
}
