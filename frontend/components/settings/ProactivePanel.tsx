"use client";

// Settings → Attention → Proactive: the wakes Edward takes on his own (and
// the dossiers he keeps on people you hear from a lot), the open loops he
// is tracking — Done or Snooze, both undoable — and the people and things
// he keeps track of.

import React, { useCallback, useEffect, useState } from "react";
import { Check, Clock } from "lucide-react";
import {
  getEntities,
  getOpenLoops,
  getProactiveStatus,
  updateOpenLoop,
  updateProactiveConfig,
  type EntityInfo,
  type OpenLoop,
  type ProactiveConfig,
  type ProactiveStatus,
} from "@/lib/api";
import { formatRelative } from "@/lib/time";
import {
  Button,
  Card,
  EmptyState,
  Notice,
  SectionLabel,
  Segment,
  Select,
  Skeleton,
  Switch,
  useToast,
} from "@/components/ui";
import { WakesNote } from "./WakesNote";

type Tab = "wakes" | "loops" | "entities";

const WEEK_MS = 7 * 86400_000;

export function ProactivePanel(_props: { isExpanded?: boolean; hideHeader?: boolean }) {
  const { toast } = useToast();
  const [tab, setTab] = useState<Tab>("wakes");
  const [status, setStatus] = useState<ProactiveStatus | null>(null);
  const [loops, setLoops] = useState<OpenLoop[] | null>(null);
  const [entities, setEntities] = useState<EntityInfo[] | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    const [s, l, e] = await Promise.allSettled([getProactiveStatus(), getOpenLoops("open"), getEntities()]);
    if (s.status === "fulfilled") setStatus(s.value);
    if (l.status === "fulfilled") setLoops(l.value);
    if (e.status === "fulfilled") setEntities(e.value);
    setFailed(s.status === "rejected" && l.status === "rejected");
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const setConfig = async (change: Partial<ProactiveConfig>) => {
    setStatus((s) => (s ? { ...s, config: { ...s.config, ...change } } : s));
    try {
      const updated = await updateProactiveConfig(change);
      setStatus((s) => (s ? { ...s, config: updated } : s));
    } catch {
      toast({ title: "I couldn't save that.", tone: "bad" });
      load();
    }
  };

  const settle = async (loop: OpenLoop, how: "resolved" | "snoozed") => {
    setLoops((prev) => prev?.filter((l) => l.id !== loop.id) ?? prev);
    const restore = () => setLoops((prev) => (prev && !prev.some((l) => l.id === loop.id) ? [loop, ...prev] : prev));
    try {
      if (how === "resolved") await updateOpenLoop(loop.id, { status: "resolved" });
      else await updateOpenLoop(loop.id, { status: "snoozed", snooze_until: new Date(Date.now() + WEEK_MS).toISOString() });
      toast({
        title: how === "resolved" ? "Closed." : "I'll bring it back in a week.",
        tone: "neutral",
        undo: () => {
          restore();
          updateOpenLoop(loop.id, { status: "open" }).catch(() => load());
        },
      });
    } catch {
      restore();
      toast({ title: "I couldn't update that loop.", tone: "bad" });
    }
  };

  if (failed && !status) {
    return (
      <Notice
        tone="bad"
        title="I can't read this right now."
        action={
          <Button variant="ghost" size="sm" onClick={load}>
            Try again
          </Button>
        }
      />
    );
  }

  return (
    <div className="space-y-5">
      <Segment<Tab>
        label="Show"
        value={tab}
        onChange={setTab}
        options={[
          { value: "wakes", label: "Wakes" },
          { value: "loops", label: "Open loops", count: loops?.length },
          { value: "entities", label: "People", count: entities?.length },
        ]}
      />

      {tab === "wakes" ? (
        <div className="space-y-6">
          <section className="space-y-2">
            <SectionLabel>When I wake on my own</SectionLabel>
            <Card className="px-4 py-0">
              {status ? <WakesNote wakes={status.wakes ?? []} /> : <Skeleton lines={3} className="py-3" />}
            </Card>
          </section>
          {status?.config ? <Dossiers config={status.config} onChange={setConfig} /> : null}
        </div>
      ) : null}

      {tab === "loops" ? (
        loops === null ? (
          <Card>
            <Skeleton lines={3} />
          </Card>
        ) : loops.length === 0 ? (
          <Card>
            <EmptyState>No open loops — nothing I&apos;m waiting on you for.</EmptyState>
          </Card>
        ) : (
          <div className="space-y-2">
            {loops.map((loop) => (
              <Card key={loop.id} as="article" className="space-y-3 p-4">
                <div>
                  <p className="text-body text-text-primary [overflow-wrap:anywhere]">{loop.description}</p>
                  <p className="mt-1 text-footnote text-text-muted">
                    {[
                      loop.detected_at ? `noticed ${formatRelative(loop.detected_at)}` : null,
                      loop.nudge_count > 0 ? `nudged ${loop.nudge_count}×` : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button variant="secondary" size="sm" icon={<Check />} onClick={() => settle(loop, "resolved")}>
                    Done
                  </Button>
                  <Button variant="ghost" size="sm" icon={<Clock />} onClick={() => settle(loop, "snoozed")}>
                    Snooze a week
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )
      ) : null}

      {tab === "entities" ? (
        entities === null ? (
          <Card>
            <Skeleton lines={4} />
          </Card>
        ) : entities.length === 0 ? (
          <Card>
            <EmptyState>I&apos;m not keeping track of anyone or anything yet.</EmptyState>
          </Card>
        ) : (
          <Card className="px-4 py-0">
            {entities.map((e) => (
              <div key={e.id} className="flex min-h-[52px] items-center gap-3 border-b border-border py-2.5 last:border-b-0">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-callout text-text-primary">{e.name}</p>
                  <p className="truncate text-footnote text-text-muted">
                    {e.entity_type}
                    {e.aliases.length ? ` · also ${e.aliases.join(", ")}` : ""}
                  </p>
                </div>
                <div className="flex-none text-right text-caption tabular-nums text-text-muted">
                  <p>{e.mention_count}×</p>
                  {e.last_seen ? <p>{formatRelative(e.last_seen)}</p> : null}
                </div>
              </div>
            ))}
          </Card>
        )
      ) : null}
    </div>
  );
}

function Dossiers({ config, onChange }: { config: ProactiveConfig; onChange: (c: Partial<ProactiveConfig>) => void }) {
  return (
    <section className="space-y-2">
      <SectionLabel>Dossiers</SectionLabel>
      <Card className="space-y-4 p-4">
        <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-callout text-text-primary">Keep notes on people you hear from a lot</p>
            <p className="text-footnote text-text-muted">A short page per person, so I have context when they write.</p>
          </div>
          <Switch label="Dossiers" checked={config.dossier_enabled} onChange={(v) => onChange({ dossier_enabled: v })} />
        </div>
        {config.dossier_enabled ? (
          <div className="border-t border-border">
            <DossierSetting
              label="Look back"
              value={config.dossier_window_days}
              options={[3, 7, 14, 30].map((d) => ({ value: d, label: `${d} days` }))}
              onChange={(v) => onChange({ dossier_window_days: v })}
            />
            <DossierSetting
              label="Pushes you acted on"
              value={config.dossier_min_acted_pushes}
              options={[1, 2, 3, 5].map((n) => ({ value: n, label: `${n} or more` }))}
              onChange={(v) => onChange({ dossier_min_acted_pushes: v })}
            />
            <DossierSetting
              label="Threads with them"
              value={config.dossier_min_inbound_threads}
              options={[1, 2, 3, 5, 8].map((n) => ({ value: n, label: `${n} or more` }))}
              onChange={(v) => onChange({ dossier_min_inbound_threads: v })}
            />
          </div>
        ) : null}
      </Card>
    </section>
  );
}

function DossierSetting({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: number;
  options: { value: number; label: string }[];
  onChange: (v: number) => void;
}) {
  const id = `dossier-${label.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className="flex min-h-[56px] items-center gap-3 border-b border-border py-2 last:border-b-0">
      <label htmlFor={id} className="min-w-0 flex-1 text-footnote text-text-secondary">
        {label}
      </label>
      <Select id={id} value={value} onChange={(e) => onChange(Number(e.target.value))} wrapperClassName="w-36 flex-none">
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    </div>
  );
}
