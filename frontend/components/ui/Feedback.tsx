"use client";

// Inline feedback: Notice, Meter, Skeleton and Pulse
// (docs/design-system-2026-09-25.md §3). Loading is Edward, not a spinner:
// `Pulse` (alias `EdwardPulse`) is a small breathing emerald pip that
// replaces `Loader2 animate-spin` in panels, and holds still under reduced
// motion.

import React from "react";
import type { ReactNode } from "react";
import { AlertCircle, AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { cn } from "./cn";
import type { Tone } from "./primitives";

// ------------------------------------------------------------ Notice
export type NoticeTone = "ok" | "bad" | "info" | "warn";

const NOTICE_TONE: Record<NoticeTone, string> = {
  ok: "bg-ok-soft text-ok",
  bad: "bg-bad-soft text-bad",
  info: "bg-info-soft text-info",
  warn: "bg-warn-soft text-warn",
};

const NOTICE_ICON: Record<NoticeTone, typeof Info> = {
  ok: CheckCircle2,
  bad: AlertCircle,
  info: Info,
  warn: AlertTriangle,
};

/** One inline message. `bad` is an alert (announced at once); the rest are
 *  polite status messages. */
export function Notice({
  tone = "info",
  title,
  children,
  action,
  className,
}: {
  tone?: NoticeTone;
  title?: ReactNode;
  children?: ReactNode;
  /** A small control on the right ("Try again"). */
  action?: ReactNode;
  className?: string;
}) {
  const Icon = NOTICE_ICON[tone];
  return (
    <div
      role={tone === "bad" ? "alert" : "status"}
      className={cn("flex items-start gap-2.5 rounded-md px-3 py-2.5 text-footnote", NOTICE_TONE[tone], className)}
    >
      <Icon className="mt-px h-4 w-4 flex-none" aria-hidden />
      <div className="min-w-0 flex-1">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div className={cn(title && "mt-0.5 opacity-90")}>{children}</div> : null}
      </div>
      {action ? <div className="-my-1 flex-none">{action}</div> : null}
    </div>
  );
}

// ------------------------------------------------------------ Meter
const METER_FILL: Record<Tone, string> = {
  neutral: "bg-text-secondary",
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  info: "bg-info",
  edward: "bg-terminal",
};

function clampPct(v: number, max: number): number {
  if (!Number.isFinite(v) || max <= 0) return 0;
  return Math.min(100, Math.max(0, (v / max) * 100));
}

/** A thin bar (a quota, a week) with an optional pace marker: where it
 *  "should" be by now. Values are clamped to 0..max. */
export function Meter({
  value,
  max = 100,
  pace,
  label,
  tone = "neutral",
  className,
}: {
  value: number;
  max?: number;
  /** Same unit as `value`; draws a tick at that point. */
  pace?: number | null;
  label: string;
  tone?: Tone;
  className?: string;
}) {
  const pct = clampPct(value, max);
  const pacePct = pace == null ? null : clampPct(pace, max);
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      className={cn("relative h-1.5 w-full rounded-full bg-surface-3", className)}
    >
      <div
        data-meter-fill=""
        className={cn("h-full rounded-full transition-[width] duration-slow ease-out", METER_FILL[tone])}
        style={{ width: `${pct}%` }}
      />
      {pacePct == null ? null : (
        <div
          data-meter-pace=""
          aria-hidden
          className="absolute -bottom-1 -top-1 w-0.5 -translate-x-1/2 rounded-full bg-text-primary/70"
          style={{ left: `${pacePct}%` }}
        />
      )}
    </div>
  );
}

// ------------------------------------------------------------ Skeleton
/** Placeholder lines while something loads. Hidden from assistive tech;
 *  pair it with a Pulse (or a status message) that says what is loading. */
export function Skeleton({
  lines = 3,
  className,
  lineClassName,
}: {
  lines?: number;
  className?: string;
  lineClassName?: string;
}) {
  return (
    <div aria-hidden="true" className={cn("space-y-2.5", className)}>
      {Array.from({ length: Math.max(1, lines) }, (_, i) => (
        <div
          key={i}
          data-skeleton-line=""
          className={cn(
            "h-3 rounded-full bg-surface-3 motion-safe:animate-pulse",
            lines > 1 && i === lines - 1 ? "w-3/5" : "w-full",
            lineClassName,
          )}
        />
      ))}
    </div>
  );
}

// ------------------------------------------------------------ Pulse
// Pulse lives in ./Pulse (2026-09-26 perf pass) so the auth wait, the login
// door and Today's pull-to-refresh draw it without carrying Notice, Meter,
// Skeleton and their icons; re-exported here.
export { EdwardPulse, Pulse } from "./Pulse";
