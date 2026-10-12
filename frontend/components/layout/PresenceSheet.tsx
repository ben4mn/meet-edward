"use client";

// The presence sheet — tap Edward anywhere (docs/design-system-2026-09-25.md
// §1: "tapping him opens what he did and is doing"). A bottom sheet on the
// phone, a centred dialog on md+ (the Sheet primitive).
//
//   Edward, large and live, with his honest line
//   Right now      — the wake running (reason, how long) or nothing
//   Waiting on you — sends held for approval → Today (plain, neutral)
//   What I did     — his last five wakes: outcome, closing line, "Read my thinking →"
//   Running on     — next wake, the week's quota, his link to your Chrome
//   Wake him now · Open Thoughts
//
// Offline (the host Mac unreachable) it claims only what is known: "I can't
// reach the Mac", when it last answered, and what he did before — no
// "nothing running", no next wake, no quota or Chrome link, no Wake.
//
// Everything comes from GET /api/mind/now through useEdwardPresence; the
// sheet refreshes it when it opens. "Wake him now" posts a manual wake (the
// backend answers 202 at once) and shows "Waking…" until the poll shows the
// wake running. Words are plain (presenceSheetModel.ts); nothing cute on the
// trust rows.

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, ChevronRight, Clock, Gauge, Plug, ShieldCheck, X } from "lucide-react";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { Button, Card, Meter, Pill, SectionLabel, Sheet, useToast } from "@/components/ui";
import { wakeMind } from "@/lib/api";
import { useEdwardPresence, useFreshBeat } from "@/lib/presence";
import { usePresenceSheet } from "@/lib/presenceHero";
import { formatResetTime } from "@/lib/edwardMood";
import { cn } from "@/lib/utils";
import {
  approvalsText,
  doingNow,
  handsText,
  nextWakeLine,
  offlineNow,
  outcomePill,
  quotaText,
  wakeButton,
  wakeRowMeta,
  wakeRowText,
} from "./presenceSheetModel";

/** Give up on the optimistic "Waking…" if the poll never shows the wake. */
const WAKE_PENDING_MAX_MS = 12_000;

function useNow(active: boolean, everyMs = 5_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!active) return;
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), everyMs);
    return () => clearInterval(t);
  }, [active, everyMs]);
  return now;
}

const TONE_DOT: Record<string, string> = {
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  neutral: "bg-text-muted",
  edward: "bg-terminal",
};

export function PresenceSheet() {
  const { isOpen, close } = usePresenceSheet();
  const presence = useEdwardPresence();
  const router = useRouter();
  const { toast } = useToast();
  const now = useNow(isOpen);
  const [pending, setPending] = useState(false);
  const pendingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { refresh } = presence;
  // Only beats that arrive while the sheet is open: opening it never
  // replays the morning wave or an old wince.
  const sheetBeat = useFreshBeat(presence.beat, isOpen);

  // Fresh data every time it opens.
  useEffect(() => {
    if (isOpen) refresh();
  }, [isOpen, refresh]);

  const mind = presence.mindNow;
  const waking = mind?.waking ?? null;

  // The wake shows up in the poll: the optimistic state has done its job.
  useEffect(() => {
    if (waking && pending) setPending(false);
  }, [waking, pending]);
  useEffect(
    () => () => {
      if (pendingTimer.current) clearTimeout(pendingTimer.current);
    },
    [],
  );

  const wake = async () => {
    setPending(true);
    if (pendingTimer.current) clearTimeout(pendingTimer.current);
    pendingTimer.current = setTimeout(() => setPending(false), WAKE_PENDING_MAX_MS);
    try {
      await wakeMind("manual");
      refresh();
      setTimeout(refresh, 1500);
    } catch {
      setPending(false);
      toast({ title: "I couldn't wake up", body: "The Mac didn't answer. Try again in a moment.", tone: "bad" });
    }
  };

  const go = (href: string) => {
    close();
    router.push(href);
    refresh();
  };

  const online = presence.online;
  const doing = online ? doingNow(waking, now) : { ...offlineNow(mind?.now, now), active: false };
  // Offline, anything "now" is a stale claim: only history stays. A spent
  // week has no next wake (it would be skipped) and no live Wake button.
  const next = nextWakeLine(mind, online, { now });
  const spent = mind?.quota?.exhausted === true;
  const spentUntil = spent ? (mind?.quota.resets_at ? formatResetTime(mind.quota.resets_at, { now }) ?? "" : "") : null;
  const quota = online ? quotaText(mind?.quota, { now }) : null;
  const hands = online ? handsText(mind?.hands) : null;
  const approvals = approvalsText(mind?.approvals_waiting ?? 0);
  const recent = mind?.recent ?? [];
  const btn = wakeButton({ waking: Boolean(waking), pending, online, quotaSpentUntil: spentUntil });
  const busy = presence.mood === "think" || presence.mood === "work";

  return (
    <Sheet
      open={isOpen}
      onClose={close}
      ariaLabel="Edward — what he's doing"
      hideClose
      className="md:max-w-md"
      footer={
        <div className="flex gap-2">
          <Button
            variant="edward"
            className="flex-1"
            onClick={wake}
            disabled={btn.disabled}
            loading={btn.busy}
          >
            {btn.label}
          </Button>
          <Button variant="secondary" className="flex-1" onClick={() => go("/settings/thoughts")}>
            Open Thoughts
          </Button>
        </div>
      }
    >
      {/* Edward, large and live, and his line */}
      <div className="relative flex flex-col items-center pb-5 pt-1 text-center">
        <button
          type="button"
          aria-label="Close"
          onClick={close}
          className="absolute -right-2 -top-1 inline-flex h-11 w-11 items-center justify-center rounded-full text-text-muted transition-[background-color,color,transform] duration-press hover:bg-surface-3 hover:text-text-primary active:scale-[.94]"
        >
          <X className="h-5 w-5" aria-hidden />
        </button>
        <div
          className={cn(
            "relative flex h-[132px] w-[132px] items-center justify-center rounded-full",
            "bg-[radial-gradient(closest-side,rgb(var(--terminal-rgb)/0.14),transparent)]",
          )}
        >
          <EdwardAvatar
            size="xl"
            live={isOpen}
            mood={presence.mood}
            beat={sheetBeat}
            beatKey={presence.beat?.key}
            aria-label={presence.label}
          />
        </div>
        <h2 className="mt-2 text-title2 text-text-primary">Edward</h2>
        <p className={cn("mt-0.5 text-footnote", busy ? "text-terminal-strong" : "text-text-secondary")}>
          {presence.status}
        </p>
      </div>

      <div className="space-y-5 pb-2">
        {/* Right now */}
        <section className="space-y-2">
          <SectionLabel>Right now</SectionLabel>
          <Card className="flex items-center gap-3 px-3.5 py-3">
            <span
              aria-hidden
              className={cn(
                "h-2 w-2 flex-none rounded-full",
                doing.active
                  ? "bg-terminal shadow-[0_0_0_3px_rgb(var(--terminal-rgb)/0.16)]"
                  : "bg-text-muted/50",
              )}
            />
            <span className="min-w-0 flex-1">
              <span className="block text-callout text-text-primary">{doing.title}</span>
              {doing.detail || (!doing.active && next) ? (
                <span className="block text-footnote text-text-muted">
                  {doing.detail ?? `Next wake ${next}`}
                </span>
              ) : null}
            </span>
          </Card>
        </section>

        {/* Waiting on you — plain, neutral: this is about trust */}
        {approvals ? (
          <section className="space-y-2">
            <SectionLabel>Waiting on you</SectionLabel>
            <Link
              href="/today"
              onClick={(e) => {
                e.preventDefault();
                go("/today");
              }}
              className={cn(
                "flex min-h-[52px] items-center gap-3 rounded-lg border border-border bg-surface px-3.5 py-3 shadow-e1",
                "transition-[transform,background-color] duration-press ease-out hover:bg-surface-elevated active:scale-[.985]",
              )}
            >
              <ShieldCheck className="h-[18px] w-[18px] flex-none text-text-secondary" aria-hidden />
              <span className="min-w-0 flex-1 text-callout text-text-primary">{approvals}</span>
              <ChevronRight className="h-4 w-4 flex-none text-text-muted" aria-hidden />
            </Link>
          </section>
        ) : null}

        {/* What I did */}
        <section className="space-y-2">
          <SectionLabel>What I did</SectionLabel>
          {recent.length === 0 ? (
            <Card className="px-3.5 py-3 text-footnote text-text-muted">
              {mind
                ? "Nothing yet today — I'll note what I do here."
                : online
                  ? "Checking in…"
                  : "I can't see what I did until the Mac answers."}
            </Card>
          ) : (
            <Card className="divide-y divide-border p-0">
              {recent.map((row, i) => {
                const pill = outcomePill(row.outcome);
                return (
                  <div key={`${row.at}-${i}`} className="px-3.5 py-3">
                    <div className="flex items-center gap-2">
                      {pill ? <Pill tone={pill.tone}>{pill.label}</Pill> : null}
                      <span className="min-w-0 truncate text-caption text-text-muted">{wakeRowMeta(row, now)}</span>
                    </div>
                    <p className="mt-1.5 text-body text-text-primary">{wakeRowText(row)}</p>
                    {row.thought_id ? (
                      <Link
                        href={`/chat?c=${encodeURIComponent(row.thought_id)}`}
                        onClick={(e) => {
                          e.preventDefault();
                          go(`/chat?c=${encodeURIComponent(row.thought_id!)}`);
                        }}
                        className={cn(
                          "relative -mx-1 mt-1 inline-flex min-h-[36px] items-center gap-1 rounded-sm px-1 text-footnote font-medium text-terminal-strong transition-colors hover:text-text-primary",
                          // A 44px hit area without growing the row.
                          "after:absolute after:inset-x-0 after:-inset-y-1 after:content-['']",
                        )}
                      >
                        Read my thinking <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                      </Link>
                    ) : null}
                  </div>
                );
              })}
            </Card>
          )}
        </section>

        {/* Running on */}
        {next || quota || hands ? (
          <section className="space-y-2">
            <SectionLabel>Running on</SectionLabel>
            <Card className="divide-y divide-border p-0">
              {next ? (
                <div className="flex items-center gap-3 px-3.5 py-3">
                  <Clock className="h-4 w-4 flex-none text-text-muted" aria-hidden />
                  <span className="flex-1 text-footnote text-text-secondary">Next wake</span>
                  <span className="text-footnote tabular-nums text-text-primary">{next}</span>
                </div>
              ) : null}
              {quota ? (
                <div className="px-3.5 py-3">
                  <div className="flex items-center gap-3">
                    <Gauge className="h-4 w-4 flex-none text-text-muted" aria-hidden />
                    <span className={cn("flex-1 text-footnote", quota.tone === "warn" ? "text-warn" : "text-text-secondary")}>
                      {quota.text}
                    </span>
                  </div>
                  <Meter
                    value={quota.percent}
                    label="The week's quota used"
                    tone={quota.tone === "warn" ? "warn" : "neutral"}
                    className="ml-7 mt-2 w-auto"
                  />
                </div>
              ) : null}
              {hands ? (
                <div className="flex items-center gap-3 px-3.5 py-3">
                  <Plug className="h-4 w-4 flex-none text-text-muted" aria-hidden />
                  <span className="flex-1 text-footnote text-text-secondary">{hands.text}</span>
                  <span aria-hidden className={cn("h-2 w-2 rounded-full", TONE_DOT[hands.tone])} />
                </div>
              ) : null}
            </Card>
          </section>
        ) : null}
      </div>
    </Sheet>
  );
}
