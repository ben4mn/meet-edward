"use client";

// Settings → Mind → Journal: the week as Edward saw himself — what he
// noticed (tools that failed, messages he misjudged, pushes you ignored)
// and how his wakes went. The wake-by-wake detail and your verdicts live
// in Thoughts; this is the week at a glance. Wording: lib/journal.ts.

import React, { useCallback, useEffect, useState } from "react";
import { getJournalWeek, type JournalWeek } from "@/lib/panelsApi";
import { observationKindLabel, observationMeta, outcomeSummary } from "@/lib/journal";
import { outcomeLabel, wakeMeta, wakeTitle, type MindWake } from "@/lib/mindLog";
import { Button, Card, EmptyState, Notice, Pill, SectionLabel, Skeleton, type Tone } from "@/components/ui";

const OUTCOME_TONE: Record<string, Tone> = { acted: "edward", left_alone: "neutral", failed: "bad", skipped: "warn" };

export function JournalPanel(_props: { isExpanded?: boolean; hideHeader?: boolean }) {
  const [data, setData] = useState<JournalWeek | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await getJournalWeek());
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (!data) {
    return error ? (
      <Notice
        tone="bad"
        title="I can't read my journal right now."
        action={
          <Button variant="ghost" size="sm" onClick={load}>
            Try again
          </Button>
        }
      />
    ) : (
      <div className="space-y-3">
        <Card>
          <Skeleton lines={2} />
        </Card>
        <Card>
          <Skeleton lines={4} />
        </Card>
      </div>
    );
  }

  const obs = data.observations;
  const wakes = data.wakes;

  return (
    <div className="space-y-6">
      <Card className="space-y-3 p-4">
        <p className="text-title3 text-text-primary">{outcomeSummary(wakes.by_outcome)}</p>
        {obs.by_kind.length ? (
          <div className="flex flex-wrap gap-1.5">
            {obs.by_kind.map((k) => (
              <Pill key={k.kind}>
                {observationKindLabel(k.kind)} <span className="tabular-nums opacity-70">{k.count}</span>
              </Pill>
            ))}
          </div>
        ) : null}
        <p className="text-footnote text-text-muted">The last {data.window_days} days.</p>
      </Card>

      <section className="space-y-2">
        <SectionLabel action={obs.total_in_window || null}>Things I noticed</SectionLabel>
        {obs.recent.length === 0 ? (
          <Card>
            <EmptyState>Nothing stood out about how I worked this week.</EmptyState>
          </Card>
        ) : (
          <Card className="px-4 py-1">
            {obs.recent.map((o) => (
              <div key={o.id} className="border-b border-border py-3 last:border-b-0">
                <p className="text-caption font-semibold text-text-secondary">{observationKindLabel(o.kind)}</p>
                <p className="mt-0.5 text-body text-text-primary [overflow-wrap:anywhere]">{o.description}</p>
                <p className="mt-1 text-footnote text-text-muted">{observationMeta(o)}</p>
              </div>
            ))}
          </Card>
        )}
      </section>

      <section className="space-y-2">
        <SectionLabel actionHref="/settings/thoughts" action="All of them">
          When I woke
        </SectionLabel>
        {wakes.recent.length === 0 ? (
          <Card>
            <EmptyState>I haven&apos;t woken this week.</EmptyState>
          </Card>
        ) : (
          <Card className="px-4 py-1">
            {wakes.recent.slice(0, 6).map((w) => {
              const wake: MindWake = { ...w, turn_id: null, pushes: [], verdict: null };
              return (
                <div key={w.id} className="border-b border-border py-3 last:border-b-0">
                  <p className="text-body text-text-primary [overflow-wrap:anywhere]">{wakeTitle(wake)}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-footnote text-text-muted">
                    <Pill tone={OUTCOME_TONE[w.outcome] ?? "neutral"}>{outcomeLabel(w.outcome)}</Pill>
                    <span>{wakeMeta(wake)}</span>
                  </div>
                </div>
              );
            })}
          </Card>
        )}
      </section>
    </div>
  );
}
