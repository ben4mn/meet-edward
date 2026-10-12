"use client";

// Work's timeline of what Edward did on his own (activityModel decides):
// his recurring maintenance folded into one "His rhythm" row that opens to
// its routines, wakes that acted in his own words with the thinking behind
// them, fired events, workers and wishes — newest first. Each row opens the
// thing it is about.

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Calendar, ChevronRight, GitBranch, Lightbulb, Repeat, Sparkles, type LucideIcon } from "lucide-react";
import {
  getDreamingStatus,
  getMindLog,
  getOrchestratorTasks,
  getWishlist,
  listEvents,
  type OrchestratorTask,
  type ScheduledEventItem,
} from "@/lib/api";
import type { WishItem } from "@/lib/wishlist";
import type { MindWake } from "@/lib/mindLog";
import { formatRelative } from "@/lib/time";
import { EmptyState, Notice, Pill, Row, Segment, Skeleton } from "@/components/ui";
import { buildActivity, filterActivity, type ActivityFilter, type ActivityKind, type ActivityRow } from "./activityModel";

const ICON: Record<ActivityKind, LucideIcon> = {
  rhythm: Repeat,
  wake: Sparkles,
  event: Calendar,
  worker: GitBranch,
  wish: Lightbulb,
};

const PAGE = 25;

function RhythmRow({ row }: { row: ActivityRow }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-border last:border-b-0 [&>button]:border-b-0">
      <Row
        onClick={() => setOpen((o) => !o)}
        expanded={open}
        icon={<Repeat aria-hidden />}
        title={row.title}
        detail={row.detail}
      />
      {open ? (
        <ul aria-label="His routines" className="mb-2 ml-11 animate-fade-rise">
          {(row.routines ?? []).map((r) => (
            <li key={r.id}>
              <Link
                href={r.href}
                className="flex min-h-[44px] items-center gap-3 rounded-md px-2 text-footnote transition-colors duration-fast hover:bg-surface-3/50 active:bg-surface-3/80"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-text-primary">{r.title}</span>
                  <span className="block truncate text-caption text-text-muted">{r.detail}</span>
                </span>
                <ChevronRight className="h-4 w-4 flex-none text-text-muted" aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ActivityItemRow({ row }: { row: ActivityRow }) {
  if (row.kind === "rhythm") return <RhythmRow row={row} />;
  const Icon = ICON[row.kind];
  const when = formatRelative(row.at);
  const detail =
    row.kind === "wake" ? (
      `${row.detail} · ${when}`
    ) : (
      <>
        <Pill tone={row.tone} className="mr-1.5 align-[1px]">
          {row.status}
        </Pill>
        {when} · {row.detail}
      </>
    );
  return (
    <Row
      href={row.href}
      icon={<Icon aria-hidden />}
      tone={row.kind === "wake" ? "edward" : undefined}
      title={row.title}
      detail={detail}
    />
  );
}

/** What a failed source is called in his voice (review 2026-09-26,
 *  failures-rendered-as-empty: a failure is said, never shown as nothing). */
const SOURCE_WORDS: Record<string, string> = {
  events: "my reminders",
  tasks: "my helpers",
  wishes: "my wishlist",
  wakes: "my wakes",
};

function cantRead(failed: string[]): string {
  const words = failed.map((f) => SOURCE_WORDS[f] ?? f);
  const list = words.length > 1 ? `${words.slice(0, -1).join(", ")} or ${words[words.length - 1]}` : words[0];
  return `I can't read ${list} right now — the rest is below.`;
}

export function ActivityTimeline() {
  const [filter, setFilter] = useState<ActivityFilter>("all");
  const [rows, setRows] = useState<ActivityRow[] | null>(null);
  const [failed, setFailed] = useState<string[]>([]);
  const [limit, setLimit] = useState(PAGE);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [events, tasks, wishes, dreaming, log] = await Promise.all([
        listEvents(undefined, "desc").catch(() => null as ScheduledEventItem[] | null),
        getOrchestratorTasks({ limit: 20 }).catch(() => null as OrchestratorTask[] | null),
        getWishlist().catch(() => null as WishItem[] | null),
        getDreamingStatus().catch(() => null),
        getMindLog(7).catch(() => null),
      ]);
      if (cancelled) return;
      setFailed(
        [
          events === null ? "events" : null,
          tasks === null ? "tasks" : null,
          wishes === null ? "wishes" : null,
          log === null ? "wakes" : null,
        ].filter((x): x is string => x !== null),
      );
      const wakes = ((log?.wakes ?? []) as MindWake[]).map((w) => ({
        id: w.id,
        at: w.at,
        source: w.source,
        outcome: w.outcome,
        note: w.note,
        thought_id: w.thought_id ?? null,
      }));
      setRows(buildActivity({ events: events ?? [], tasks: tasks ?? [], wishes: wishes ?? [], dreaming, wakes }));
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const all = useMemo(() => filterActivity(rows ?? [], filter), [rows, filter]);
  const shown = all.slice(0, limit);

  return (
    <div className="space-y-3">
      <Segment
        value={filter}
        onChange={(v) => {
          setFilter(v);
          setLimit(PAGE);
        }}
        label="Show"
        options={[
          { value: "all", label: "All" },
          { value: "wake", label: "Wakes" },
          { value: "event", label: "Events" },
          { value: "worker", label: "Workers" },
          { value: "wish", label: "Wishes" },
        ]}
      />
      {rows !== null && failed.length > 0 && failed.length < 4 && shown.length > 0 ? (
        <Notice tone="warn">{cantRead(failed)}</Notice>
      ) : null}
      <div className="rounded-lg border border-border bg-surface px-3 shadow-e1">
        {rows === null ? (
          <div className="py-4" aria-busy="true">
            <Skeleton lines={4} />
          </div>
        ) : failed.length === 4 ? (
          <Notice tone="warn" className="my-3">
            {"I can't see what I've been doing right now — the Mac didn't answer. Try again in a moment."}
          </Notice>
        ) : shown.length === 0 && (filter === "wake" ? failed.includes("wakes") : failed.length > 0) ? (
          <Notice tone="warn" className="my-3">
            {filter === "wake" ? "I can't read my wakes right now." : cantRead(failed)}
          </Notice>
        ) : shown.length === 0 ? (
          <EmptyState>
            {filter === "wake" ? "I haven't acted on my own this week." : "Nothing here yet — I'll fill it as I go."}
          </EmptyState>
        ) : (
          <>
            {shown.map((r) => (
              <ActivityItemRow key={r.id} row={r} />
            ))}
            {all.length > shown.length ? (
              <button
                type="button"
                onClick={() => setLimit((n) => n + PAGE)}
                className="flex min-h-[44px] w-full items-center justify-center text-footnote font-medium text-text-secondary transition-colors hover:text-text-primary"
              >
                Show {Math.min(PAGE, all.length - shown.length)} more
              </button>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
