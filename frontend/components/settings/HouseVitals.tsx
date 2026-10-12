"use client";

// Watchdog, launch agents, disk and backups — the same facts the hub's
// health screen shows, as the House room's live view. A self-repair is
// informational (blue), not amber: amber that needs no action trains the
// owner to ignore amber (the hub's alert-fatigue rule).

import React, { useEffect, useState } from "react";
import { fetchHealth } from "@/app/(display)/hub/lib/hubApi";
import type { HealthResponse, HealthStatus } from "@/app/(display)/hub/lib/types";
import { formatRelative } from "@/lib/time";
import { EmptyState, Pill, Row, Skeleton, type Tone } from "@/components/ui";
import { plainHealthNote } from "./edwardHomeModel";

export function healthTone(s: HealthStatus | undefined): Tone {
  switch (s) {
    case "healthy":
      return "ok";
    case "repaired":
      return "info";
    case "stale":
      return "warn";
    case "problem":
      return "bad";
    default:
      return "neutral";
  }
}

const WORD: Record<HealthStatus, string> = {
  healthy: "OK",
  repaired: "fixed itself",
  stale: "stale",
  problem: "needs you",
  unknown: "unknown",
};

export function HouseVitals() {
  const [h, setH] = useState<HealthResponse | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then((r) => !cancelled && setH(r))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, []);

  const frame = (children: React.ReactNode) => (
    <div className="rounded-lg border border-border bg-surface px-3 shadow-e1">{children}</div>
  );

  if (failed) return frame(<EmptyState>I can&apos;t reach the health check right now — the watchdog still runs every five minutes.</EmptyState>);
  if (!h)
    return frame(
      <div className="py-4" aria-busy="true">
        <Skeleton lines={4} />
      </div>,
    );
  const rows = [...h.components, ...h.agents];
  const raw = h.watchdog.problems[0] ?? h.watchdog.repairs[0] ?? null;
  const note = raw ? plainHealthNote(raw) : null;
  return frame(
    <>
      <Row
        title={h.host}
        detail={[
          h.watchdog.checked_at ? `Checked ${formatRelative(h.watchdog.checked_at)}` : "Not checked yet",
          note,
        ]
          .filter(Boolean)
          .join(" · ")}
        trailing={<Pill tone={healthTone(h.overall)}>{WORD[h.overall] ?? h.overall}</Pill>}
      />
      {rows.map((c) => (
        <Row
          key={c.label}
          title={c.label}
          detail={c.detail || undefined}
          trailing={<Pill tone={healthTone(c.status)}>{c.pct != null ? `${c.pct}%` : WORD[c.status] ?? c.status}</Pill>}
        />
      ))}
    </>,
  );
}
