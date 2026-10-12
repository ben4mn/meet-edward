"use client";

// "What I did" (UX pass 2026-09-26): Edward's own account of the day, in
// parts of the day. His wakes fold into one row at the top (it opens the
// Thoughts panel); in each part rows run newest first, and what he chose
// to do is in his colour, one tap from the thought entry that says why.
// Rows wrap to two lines; a row whose point is its detail expands in place.

import React, { useState } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  Brain,
  CalendarCheck,
  ChevronDown,
  ChevronRight,
  Lightbulb,
  MessagesSquare,
  StickyNote,
  Zap,
} from "lucide-react";
import { Card, EmptyState, SectionLabel, TONE_CHIP } from "@/components/ui";
import { cn } from "@/lib/utils";
import { clockOnly } from "./heroModel";
import { bucketDid, didDetail, didRowModel, didTitle, splitDid, wakesDetail, type TodayDid } from "./todayModel";

const ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  action: Zap,
  note: StickyNote,
  triage: MessagesSquare,
  event_fired: CalendarCheck,
  wish: Lightbulb,
  wakes: Brain,
};

const ROW =
  "block w-full px-3 py-2.5 text-left transition-colors duration-fast hover:bg-surface-3/50 active:bg-surface-3/80 " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-terminal/60";

function DidRow({ item, tz }: { item: TodayDid; tz: string | undefined }) {
  const [open, setOpen] = useState(false);
  const m = didRowModel(item);
  const Icon = ICON[item.kind] ?? Activity;
  const mine = m.badge !== null;
  const detail = didDetail(item);
  const count = item.count && item.count > 1 ? item.count : 0;

  const head = (
    <span className="flex items-start gap-3">
      <span
        aria-hidden
        className={cn(
          "mt-px flex h-8 w-8 flex-none items-center justify-center rounded-sm [&_svg]:h-4 [&_svg]:w-4",
          mine ? TONE_CHIP.edward : "bg-surface-3 text-text-secondary",
        )}
      >
        <Icon />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline gap-3">
          <span className="line-clamp-2 min-w-0 flex-1 text-callout text-text-primary">
            {mine ? <span className="sr-only">Edward did: </span> : null}
            {didTitle(item)}
            {count ? <span className="font-normal text-text-muted"> ×{count}</span> : null}
          </span>
          <span className="flex-none text-caption tabular-nums text-text-muted" suppressHydrationWarning>
            {clockOnly(item.at, tz)}
          </span>
        </span>
        {detail && (m.inlineDetail || m.expandable) ? (
          <span
            className={cn(
              "mt-0.5 block text-footnote",
              mine ? "text-text-secondary" : "text-text-muted",
              open ? "whitespace-pre-wrap" : m.expandable ? "line-clamp-1" : "line-clamp-2",
            )}
          >
            {detail}
          </span>
        ) : null}
      </span>
      {m.expandable ? (
        <ChevronDown
          aria-hidden
          className={cn("mt-1 h-4 w-4 flex-none text-text-muted transition-transform duration-base ease-in-out", open && "rotate-180")}
        />
      ) : null}
    </span>
  );

  if (m.expandable) {
    return (
      <div className="border-b border-border last:border-b-0">
        <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className={ROW}>
          {head}
        </button>
        {open && item.href ? (
          <div className="animate-fade-in pb-2 pl-[56px] pr-3">
            <Link
              href={item.href}
              className="relative inline-flex min-h-[36px] items-center gap-1 rounded-md text-footnote font-medium text-text-secondary transition-colors hover:text-text-primary after:absolute after:inset-x-0 after:-inset-y-1 after:content-['']"
            >
              Open <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          </div>
        ) : null}
      </div>
    );
  }
  if (m.href) {
    return (
      <Link href={m.href} className={cn(ROW, "border-b border-border last:border-b-0")}>
        {head}
      </Link>
    );
  }
  return <div className={cn(ROW, "border-b border-border last:border-b-0 hover:bg-transparent active:bg-transparent")}>{head}</div>;
}

export function DidSection({ did, now, tz }: { did: TodayDid[]; now: Date; tz: string | undefined }) {
  const { wakes, rows } = splitDid(did);
  const buckets = bucketDid(rows, now, tz);
  const wakesLine = wakes ? wakesDetail(wakes) : null;

  return (
    <section aria-labelledby="today-did">
      <SectionLabel action="All thoughts" actionHref="/settings/thoughts">
        <span id="today-did">What I did</span>
      </SectionLabel>
      <div className="mt-2 space-y-2">
        {wakes ? (
          <Link
            href={wakes.href || "/settings/thoughts"}
            className={cn(
              "flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-3 shadow-e1",
              "transition-[transform,background-color] duration-press ease-out hover:bg-surface-elevated active:scale-[.985]",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60",
            )}
          >
            <span aria-hidden className={cn("flex h-8 w-8 flex-none items-center justify-center rounded-sm [&_svg]:h-4 [&_svg]:w-4", TONE_CHIP.edward)}>
              <Brain />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-callout text-text-primary">{wakes.title}</span>
              {wakesLine ? <span className="mt-0.5 line-clamp-2 block text-footnote text-text-muted">{wakesLine}</span> : null}
            </span>
            <ChevronRight className="h-4 w-4 flex-none text-text-muted" aria-hidden />
          </Link>
        ) : null}
        {rows.length === 0 ? (
          wakes ? null : (
            <Card>
              <EmptyState className="py-6">Nothing yet today. I&apos;ll list what I do here.</EmptyState>
            </Card>
          )
        ) : (
          <Card className="overflow-hidden px-0 py-0">
            {buckets.map((b) => (
              <div key={b.label} role="group" aria-label={b.label}>
                <p className="px-3 pb-0.5 pt-3 text-caption font-medium text-text-muted">{b.label}</p>
                {b.items.map((d) => (
                  <DidRow key={d.id} item={d} tz={tz} />
                ))}
              </div>
            ))}
          </Card>
        )}
      </div>
    </section>
  );
}
