"use client";

// Edward's fuel: the Codex week as one story (docs/design-system-2026-09-25.md §3 Meter).
// How much of the week is used against an even pace, where it will close
// and how much would go unspent, chat's reserve, what his own spare-time
// work is doing, and who spent the week. The wording is pure and pinned in
// lib/codexUsage.ts (describeFuel); this is the thin shell around it.
// On the Claude API (2026-10-10, provider "anthropic") the same card tells
// the day instead: what it cost against the daily cap, where background
// work stops, and the credits left (describeClaudeFuel).

import { setVisibleInterval } from "@/lib/visibleInterval";
import React, { useCallback, useEffect, useState } from "react";
import { getCodexUsage, type CodexUsageStatus } from "@/lib/api";
import { describeFuel, type FuelDescription, type UsageTone } from "@/lib/codexUsage";
import { Button, Card, Meter, Notice, Skeleton, cn, type Tone } from "@/components/ui";

const POLL_MS = 120_000;
const TICK_MS = 30_000;

const METER_TONE: Record<UsageTone, Tone> = { ok: "edward", warn: "warn", bad: "bad", neutral: "neutral" };

export function CodexUsageCard({ className }: { className?: string }) {
  const [data, setData] = useState<CodexUsageStatus | null>(null);
  const [error, setError] = useState(false);
  const [, setTick] = useState(0); // keeps the countdown live

  const load = useCallback(async () => {
    try {
      setData(await getCodexUsage());
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    load();
    const stopPoll = setVisibleInterval(load, POLL_MS);
    const stopTick = setVisibleInterval(() => setTick((t) => t + 1), TICK_MS);
    return () => {
      stopPoll();
      stopTick();
    };
  }, [load]);

  if (!data) {
    return (
      <Card className={cn("space-y-4 p-4", className)}>
        {error ? (
          <Notice
            tone="bad"
            title="I can't read my fuel gauge right now."
            action={
              <Button variant="ghost" size="sm" onClick={load}>
                Try again
              </Button>
            }
          />
        ) : (
          <>
            <Skeleton lines={1} lineClassName="h-5 w-4/5" />
            <Skeleton lines={1} lineClassName="h-1.5" />
            <Skeleton lines={2} />
          </>
        )}
      </Card>
    );
  }

  return <FuelView fuel={describeFuel(data)} ceiling={data.background_ceiling_percent ?? 90} className={className} />;
}

function FuelView({ fuel: f, ceiling, className }: { fuel: FuelDescription; ceiling: number; className?: string }) {
  const top = f.spenders.reduce((m, s) => Math.max(m, s.pct), 0) || 1;
  return (
    <Card className={cn("space-y-4 p-4", className)}>
      <div className="space-y-3">
        <p className={cn("text-title3 text-text-primary", f.tone === "bad" && "text-bad")}>{f.headline}</p>
        {f.usedPct != null ? (
          <div className="space-y-2">
            <Meter
              value={f.usedPct}
              pace={f.pacePct}
              label={f.howItWorks ? "Today's spend against the cap" : "Fuel used this week"}
              tone={METER_TONE[f.tone]}
            />
            <div className="flex items-center justify-between gap-3 text-caption tabular-nums text-text-muted">
              <span>
                <span className="font-semibold text-text-primary">{f.usedPct}%</span> used
              </span>
              {f.pacePct != null ? (
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden className="h-2.5 w-0.5 rounded-full bg-text-primary/70" />
                  {f.paceLabel ?? "even pace"} {f.pacePct}%
                </span>
              ) : null}
              {f.resetsIn ? <span>refills in {f.resetsIn}</span> : null}
            </div>
          </div>
        ) : null}
      </div>

      {f.background ? <Notice tone="warn">{f.background}</Notice> : null}

      {f.surplus || f.reserve || f.lastJob ? (
        <div className="space-y-1 border-t border-border pt-3">
          {f.surplus ? <p className="text-footnote text-text-primary">{f.surplus}</p> : null}
          {f.surplusWhy ? <p className="text-footnote text-text-muted">{f.surplusWhy}</p> : null}
          {f.lastJob ? <p className="text-footnote text-text-secondary">{f.lastJob}</p> : null}
          {f.reserve ? <p className="text-footnote text-text-muted">{f.reserve}</p> : null}
        </div>
      ) : null}

      {f.spenders.length ? (
        <div className="space-y-2 border-t border-border pt-3">
          <p className="text-overline text-text-muted">{f.spendersTitle ?? "Who spent the week"}</p>
          <ul className="space-y-2">
            {f.spenders.map((s) => (
              <li key={s.label} className="grid grid-cols-[minmax(0,1fr)_56px_92px] items-center gap-3 text-footnote md:grid-cols-[200px_minmax(0,1fr)_92px]">
                <span className="truncate text-text-secondary">{s.label}</span>
                <span aria-hidden className="h-1 rounded-full bg-surface-3">
                  <span
                    className="block h-full rounded-full bg-text-muted/70"
                    style={{ width: `${Math.max(4, Math.min(100, (s.pct / top) * 100))}%` }}
                  />
                </span>
                <span className="text-right tabular-nums text-text-primary">{s.text}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <details className="group border-t border-border pt-2">
        <summary className="relative flex min-h-[36px] cursor-pointer list-none items-center after:absolute after:inset-x-0 after:-inset-y-1 after:content-[''] text-caption font-medium text-text-muted transition-colors duration-fast hover:text-text-primary [&::-webkit-details-marker]:hidden">
          How this works
        </summary>
        {f.howItWorks ? (
          <p className="pb-1 text-[12px] leading-4 text-text-muted">{f.howItWorks}</p>
        ) : (
          <p className="pb-1 text-[12px] leading-4 text-text-muted">
            Every Codex answer reports the week&apos;s meter. Chat and replies always run; my background work
            (memory, reflection, photo descriptions) is paced to spend the week evenly and stops at {ceiling}%.
            Whatever is left over chat&apos;s reserve goes to my own work — research, meeting prep, dossiers,
            summaries — so the week doesn&apos;t end unspent. The tick on the bar is an even pace.
          </p>
        )}
      </details>
    </Card>
  );
}
