"use client";

import type { ReactNode } from "react";
import { usePoll } from "../hooks/usePoll";
import { fetchVitals } from "../lib/displayApi";
import { isStale, staleAgeLabel } from "../lib/staleness";
import type { VitalsResponse } from "../lib/types";
import { Bar, Card, Pill } from "./primitives";

function Stat({
  label,
  value,
  mono = false,
  bar,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
  bar?: ReactNode;
}) {
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
        <span
          style={{
            fontSize: 9,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            color: "var(--ed-text-dim)",
          }}
        >
          {label}
        </span>
        <span className={mono ? "mc-mono" : ""} style={{ fontSize: 11, color: "var(--ed-text)" }}>
          {value}
        </span>
      </div>
      {bar}
    </div>
  );
}

function fmtUptime(seconds: number | null): string {
  if (seconds == null) return "—";
  const days = Math.floor(seconds / 86400);
  const hrs = Math.floor((seconds % 86400) / 3600);
  return days ? `${days}d ${hrs}h` : `${hrs}h`;
}

function fmtPct(v: number | null): string {
  return v == null ? "—" : `${Math.round(v)}%`;
}

function fmtNum(n: number): string {
  return n.toLocaleString();
}

function fmtKbps(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1024) return `${(n / 1024).toFixed(1)} mb/s`;
  return `${Math.round(n)} kb/s`;
}

/** `title`: the one Edward gave the piece on the stage. */
export function VitalsPanel({ title }: { title?: string | null } = {}) {
  const { data, lastSuccessAt } = usePoll(fetchVitals, 5000);
  const v: VitalsResponse | null = data;
  const staleLabel = isStale(lastSuccessAt, 5000) ? staleAgeLabel(lastSuccessAt) : null;

  const cpu = v?.cpu_pct ?? null;
  const mem = v?.mem_pct ?? null;
  const disk = v?.disk_pct ?? null;
  const net = v?.net_kbps ?? null;

  return (
    <Card
      title={title ? <>{title}</> : <><span>⌬</span> SYSTEM · {(v?.host || "this Mac").toUpperCase()}</>}
      right={<Pill tone="ok">healthy</Pill>}
      staleLabel={staleLabel}
    >
      {/* Two columns in a narrow tile, three once there is room (display.css):
          the stock 2×1 tile at 1920×1080 cut the last row in half. */}
      <div className="mc-vitals">
      <div className="mc-vitals-grid">
        <Stat label="uptime" value={fmtUptime(v?.uptime_seconds ?? null)} />
        <Stat label="model" value={v?.model || "—"} mono />
        <Stat label="cpu" value={fmtPct(cpu)} bar={cpu != null ? <Bar value={cpu} tone={cpu > 80 ? "warn" : "ok"} /> : null} />
        <Stat label="mem" value={fmtPct(mem)} bar={mem != null ? <Bar value={mem} tone={mem > 80 ? "warn" : "ok"} /> : null} />
        <Stat label="disk" value={fmtPct(disk)} bar={disk != null ? <Bar value={disk} tone={disk > 80 ? "warn" : "ok"} /> : null} />
        <Stat
          label="net"
          value={fmtKbps(net)}
          bar={net != null ? <Bar value={Math.min(1000, net)} max={1000} tone="info" /> : null}
        />
        <Stat label="tokens in" value={fmtNum(v?.tokens_in_today ?? 0)} mono />
        <Stat label="tokens out" value={fmtNum(v?.tokens_out_today ?? 0)} mono />
        <Stat
          label="cost · today"
          value={v?.cost_today != null ? `$${v.cost_today.toFixed(2)}` : "—"}
          mono
        />
        <Stat label="convos · today" value={fmtNum(v?.convos_today ?? 0)} mono />
        <Stat label="memories" value={fmtNum(v?.memory_count ?? 0)} mono />
        <Stat label="docs" value={fmtNum(v?.docs_count ?? 0)} mono />
      </div>
      </div>
    </Card>
  );
}
