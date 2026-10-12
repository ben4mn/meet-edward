"use client";

// Settings → Work → Usage: the fuel story first (the same card as the Work
// room), then the tokens under it — lifetime and since the last restart,
// and per model.

import React, { useCallback, useEffect, useState } from "react";
import { getUsage, type UsageSummary } from "@/lib/api";
import { formatDateTime } from "@/lib/time";
import { useUserTimezone } from "@/lib/useTimezone";
import { Button, Card, EmptyState, Notice, SectionLabel, Skeleton } from "@/components/ui";
import { CodexUsageCard } from "./CodexUsageCard";

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
};

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return n.toString();
}

function calls(n: number): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? "call" : "calls"}`;
}

type Stats = { input_tokens: number; output_tokens: number; requests: number };

export function UsagePanel(_props: { isExpanded?: boolean; hideHeader?: boolean }) {
  const tz = useUserTimezone();
  const [data, setData] = useState<UsageSummary | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await getUsage());
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const providers = data ? Object.entries(data.providers) : [];

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <SectionLabel>Fuel this week</SectionLabel>
        <CodexUsageCard />
      </section>

      <section className="space-y-2">
        <SectionLabel>Tokens</SectionLabel>
        {error && !data ? (
          <Notice
            tone="bad"
            title="I can't read my token counts right now."
            action={
              <Button variant="ghost" size="sm" onClick={load}>
                Try again
              </Button>
            }
          />
        ) : !data ? (
          <Card>
            <Skeleton lines={3} />
          </Card>
        ) : providers.length === 0 ? (
          <Card>
            <EmptyState>Nothing counted yet — send me a message and I&apos;ll start keeping track.</EmptyState>
          </Card>
        ) : (
          providers.map(([providerId, provider]) => (
            <Card key={providerId} className="space-y-3 p-4">
              {providers.length > 1 ? (
                <p className="text-callout text-text-primary">{PROVIDER_LABELS[providerId] || providerId}</p>
              ) : null}
              <div className="grid grid-cols-2 gap-2">
                <StatTile label="All time" stats={provider.lifetime} />
                <StatTile
                  label={data.session_start ? `Since ${formatDateTime(data.session_start, { tz })}` : "Since restart"}
                  stats={provider.session}
                />
              </div>
              {Object.keys(provider.by_model).length > 0 ? (
                <div className="border-t border-border pt-1">
                  {Object.entries(provider.by_model).map(([modelId, m]) => (
                    <div key={modelId} className="flex min-h-[44px] items-center justify-between gap-3 border-b border-border py-2 last:border-b-0">
                      <span className="min-w-0 truncate font-mono text-footnote text-text-primary">{modelId}</span>
                      <span className="flex-none text-caption tabular-nums text-text-muted">
                        {formatTokens(m.lifetime.input_tokens)} in · {formatTokens(m.lifetime.output_tokens)} out ·{" "}
                        {calls(m.lifetime.requests)}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
            </Card>
          ))
        )}
      </section>
    </div>
  );
}

function StatTile({ label, stats }: { label: string; stats: Stats }) {
  return (
    <div className="rounded-md bg-surface-3/60 p-3">
      <div className="text-caption text-text-muted">{label}</div>
      <div className="mt-0.5 text-title2 tabular-nums text-text-primary">
        {formatTokens(stats.input_tokens + stats.output_tokens)}
      </div>
      <div className="mt-0.5 text-caption tabular-nums text-text-muted">
        {formatTokens(stats.input_tokens)} in · {formatTokens(stats.output_tokens)} out · {calls(stats.requests)}
      </div>
    </div>
  );
}
