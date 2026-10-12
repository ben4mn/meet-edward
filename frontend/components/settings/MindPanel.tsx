"use client";

// Settings → Mind → Thoughts. The owner's window on the mind: what Edward is
// keeping an eye on (with the next time he will look), every wake with
// what it did and what came of it, and a "right call?" on each.
//
// "Wake now" does not block (2026-09-25: POST /api/mind/wake answers 202
// and the wake runs in the background). The panel shows a "Waking…" row at
// once and polls the log until a wake it did not have lands; a failed poll
// means "still running". Grouping, folding and wording live in lib/mindLog.

import { setVisibleInterval } from "@/lib/visibleInterval";
import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bell, Clock, Sparkles } from "lucide-react";
import { getMindIntentions, getMindLog, postMindVerdict, wakeMind } from "@/lib/api";
import {
  canJudge,
  groupWakesByDay,
  intentionMeta,
  intentionNextLook,
  landedWake,
  outcomeLabel,
  thoughtHref,
  VERDICT_OPTIONS,
  wakeMeta,
  wakeTitle,
  type LogRow,
  type MindIntention,
  type MindWake,
} from "@/lib/mindLog";
import { useEdwardPresence } from "@/lib/presence";
import {
  Button,
  Card,
  EmptyState,
  Notice,
  Pill,
  SectionLabel,
  Segment,
  Skeleton,
  useToast,
  type Tone,
} from "@/components/ui";

const POLL_MS = 60_000;
/** While a wake the owner asked for runs: how often to look for its row. */
const WAKE_POLL_MS = 2_500;
/** A wake has 240 s (a mention 420 s); past this the row stops hurrying. */
const WAKE_PATIENCE_MS = 8 * 60_000;

const OUTCOME_TONE: Record<string, Tone> = {
  acted: "edward",
  left_alone: "neutral",
  failed: "bad",
  skipped: "warn",
};

type Verdict = "right_call" | "wrong_call";

interface Waking {
  startedAt: number;
  known: Set<number>;
  joined: boolean;
  slow: boolean;
}

export interface MindPanelProps {
  /** Accepted while settingsConfig still passes the old accordion props. */
  isExpanded?: boolean;
  hideHeader?: boolean;
  /** How often to look for a requested wake's row (tests shorten it). */
  pollMs?: number;
}

export function MindPanel({ pollMs = WAKE_POLL_MS }: MindPanelProps) {
  const presence = useEdwardPresence();
  const { toast } = useToast();
  const [wakes, setWakes] = useState<MindWake[] | null>(null);
  const [intentions, setIntentions] = useState<MindIntention[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [waking, setWaking] = useState<Waking | null>(null);
  const wakesRef = useRef<MindWake[] | null>(null);
  wakesRef.current = wakes;

  const load = useCallback(async () => {
    const [log, ints] = await Promise.allSettled([getMindLog(7), getMindIntentions()]);
    if (log.status === "fulfilled") setWakes(log.value.wakes);
    if (ints.status === "fulfilled") setIntentions(ints.value.intentions);
    setLoadError(log.status === "rejected" && wakesRef.current === null);
    return log.status === "fulfilled" ? log.value.wakes : null;
  }, []);

  useEffect(() => {
    load();
    return setVisibleInterval(load, POLL_MS);
  }, [load]);

  // While a requested wake runs, look for its row until it lands.
  useEffect(() => {
    if (!waking || waking.slow) return;
    let cancelled = false;
    const tick = async () => {
      let log: MindWake[] | null = null;
      try {
        log = (await getMindLog(7)).wakes;
      } catch {
        return; // a 5xx or a timeout while it runs is "still running"
      }
      if (cancelled) return;
      setWakes(log);
      if (landedWake(log, waking.known, { joined: waking.joined })) {
        setWaking(null);
        presence.refresh();
        getMindIntentions().then((r) => !cancelled && setIntentions(r.intentions)).catch(() => {});
      } else if (Date.now() - waking.startedAt > WAKE_PATIENCE_MS) {
        setWaking((w) => (w ? { ...w, slow: true } : w));
      }
    };
    const t = setInterval(tick, pollMs);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [waking, pollMs, presence]);

  // A slow wake still clears itself when the regular poll finds it.
  useEffect(() => {
    if (waking?.slow && wakes && landedWake(wakes, waking.known, { joined: waking.joined })) setWaking(null);
  }, [wakes, waking]);

  const wakeNow = async () => {
    if (waking) return;
    const known = new Set((wakesRef.current ?? []).map((w) => w.id));
    setWaking({ startedAt: Date.now(), known, joined: false, slow: false });
    try {
      const r = await wakeMind("manual");
      if (r.joined) setWaking((w) => (w ? { ...w, joined: true } : w));
      presence.refresh();
    } catch {
      setWaking(null);
      toast({ title: "I couldn't start a wake just now.", body: "Try again in a moment.", tone: "bad" });
    }
  };

  const judge = async (id: number, verdict: Verdict) => {
    const before = wakesRef.current;
    setWakes((prev) => prev?.map((w) => (w.id === id ? { ...w, verdict: { verdict, note: null } } : w)) ?? prev);
    try {
      await postMindVerdict(id, verdict);
    } catch {
      setWakes(before);
      toast({ title: "I couldn't save that verdict.", body: "Try again in a moment.", tone: "bad" });
    }
  };

  const days = groupWakesByDay(wakes ?? []);
  const loading = wakes === null && !loadError;

  return (
    <div className="space-y-6">
      <Card className="flex items-center gap-3 p-4">
        <div className="min-w-0 flex-1">
          <p className="text-callout text-text-primary">
            {wakes ? `${wakes.length} ${wakes.length === 1 ? "wake" : "wakes"} this week` : "This week"}
          </p>
          <p className="mt-0.5 text-footnote text-text-muted">
            {intentions
              ? intentions.length
                ? `Keeping an eye on ${intentions.length} ${intentions.length === 1 ? "thing" : "things"} for you`
                : "Nothing I'm watching right now"
              : " "}
          </p>
        </div>
        <Button
          variant="edward"
          size="sm"
          icon={<Sparkles />}
          onClick={wakeNow}
          disabled={Boolean(waking)}
          title="Wake me now — I'll look around and decide what, if anything, to do"
        >
          Wake now
        </Button>
      </Card>

      {/* Right under the button, where the press happened — not below the fold. */}
      {waking ? <WakingRow slow={waking.slow} /> : null}

      {loadError ? (
        <Notice
          tone="bad"
          title="I couldn't read my log just now."
          action={
            <Button variant="ghost" size="sm" onClick={() => load()}>
              Try again
            </Button>
          }
        >
          The Mac may be busy. I&apos;ll keep trying.
        </Notice>
      ) : null}

      <section className="space-y-2" aria-label="What I'm keeping an eye on">
        <SectionLabel action={intentions?.length ? intentions.length : null}>What I&apos;m keeping an eye on</SectionLabel>
        {intentions === null ? (
          loading ? (
            <Card>
              <Skeleton lines={2} />
            </Card>
          ) : null
        ) : intentions.length === 0 ? (
          <Card>
            <EmptyState>I&apos;m not keeping an eye on anything for you right now.</EmptyState>
          </Card>
        ) : (
          <Card className="px-4 py-1">
            {intentions.map((i) => (
              <IntentionRow key={i.id} intention={i} />
            ))}
          </Card>
        )}
      </section>

      <section className="space-y-3" aria-label="When I woke">
        <SectionLabel>When I woke</SectionLabel>
        {loading ? (
          <div className="space-y-2">
            <Card>
              <Skeleton lines={3} />
            </Card>
            <Card>
              <Skeleton lines={2} />
            </Card>
          </div>
        ) : null}
        {wakes && wakes.length === 0 && !waking ? (
          <Card>
            <EmptyState>I haven&apos;t woken this week yet.</EmptyState>
          </Card>
        ) : null}
        {days.map((day) => (
          <div key={day.label} className="space-y-2">
            <p className="px-0.5 pt-1 text-caption font-semibold text-text-secondary">{day.label}</p>
            {day.rows.map((row) => (
              <LogRowView key={row.kind === "wake" ? row.wake.id : `quiet-${row.ids[0]}`} row={row} onVerdict={judge} />
            ))}
          </div>
        ))}
      </section>
    </div>
  );
}

function IntentionRow({ intention: i }: { intention: MindIntention }) {
  const scheduled = Boolean(i.next_fire_at);
  const meta = intentionMeta(i);
  return (
    <div className="border-b border-border py-3 last:border-b-0">
      <p className="text-body text-text-primary [overflow-wrap:anywhere]">{i.content}</p>
      <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-footnote text-text-muted">
        <span className={scheduled ? "inline-flex items-center gap-1 font-medium text-terminal-strong" : "inline-flex items-center gap-1"}>
          <Clock className="h-3.5 w-3.5" aria-hidden />
          {intentionNextLook(i)}
        </span>
        {meta ? <span>{meta}</span> : null}
      </p>
    </div>
  );
}

// The two verdicts read as "Right call? Yes / No"; each keeps its full
// name for assistive tech.
const VERDICT_CHOICES = VERDICT_OPTIONS.map((o) => ({
  value: o.value,
  label: (
    <>
      <span aria-hidden>{o.value === "right_call" ? "Yes" : "No"}</span>
      <span className="sr-only">{o.label}</span>
    </>
  ),
}));

function WakingRow({ slow }: { slow: boolean }) {
  return (
    <Card className="flex items-center gap-3 border-terminal/30 bg-terminal-soft/40">
      {/* Static: the one breathing thing on the screen is Edward himself. */}
      <span
        role="img"
        aria-label="Waking"
        className="h-2.5 w-2.5 flex-none rounded-full bg-terminal shadow-[0_0_0_4px_rgb(var(--terminal-rgb)/0.16),0_0_12px_rgb(var(--terminal-rgb)/0.5)]"
      />
      <div className="min-w-0 flex-1">
        <p className="text-callout text-text-primary">{slow ? "Still looking" : "Waking… I'm looking"}</p>
        <p className="text-footnote text-text-muted">
          {slow ? "It'll show up here when I'm done." : "This usually takes a minute or two."}
        </p>
      </div>
    </Card>
  );
}

function LogRowView({ row, onVerdict }: { row: LogRow; onVerdict: (id: number, v: Verdict) => void }) {
  if (row.kind === "quiet") {
    return (
      <p className="flex items-center gap-2 px-1 py-1 text-footnote text-text-muted">
        <span aria-hidden className="h-1 w-1 flex-none rounded-full bg-text-muted/60" />
        {row.count} quiet looks — nothing needed me.
      </p>
    );
  }
  const w = row.wake;
  const thinking = thoughtHref(w);
  const title = wakeTitle(w);
  const rawNote = (w.note ?? "").trim();
  const showRaw = rawNote && rawNote !== title && (w.outcome === "failed" || w.outcome === "skipped");
  return (
    <Card as="article" className="space-y-2 p-4">
      <p className="text-body text-text-primary [overflow-wrap:anywhere]">{title}</p>
      {showRaw ? (
        // The sentence above already says what happened; the internal
        // reason is for when the owner wants it, folded, in plain type.
        <details className="group text-caption text-text-muted">
          <summary className="-mx-1 inline-flex min-h-[32px] cursor-pointer list-none items-center rounded-sm px-1 font-medium hover:text-text-secondary">
            Details
          </summary>
          <p className="mt-0.5 [overflow-wrap:anywhere]">{rawNote}</p>
        </details>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-footnote text-text-muted">
        <Pill tone={OUTCOME_TONE[w.outcome] ?? "neutral"}>{outcomeLabel(w.outcome)}</Pill>
        <span>{wakeMeta(w)}</span>
      </div>
      {w.pushes.map((p) => (
        <p key={p.id} className="flex items-start gap-2 text-footnote text-text-secondary">
          <Bell className="mt-0.5 h-3.5 w-3.5 flex-none text-text-muted" aria-label="Pushed" />
          <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{p.title}</span>
          <Pill tone={p.opened ? "ok" : "neutral"}>{p.opened ? "opened" : "not opened"}</Pill>
        </p>
      ))}
      {thinking ? (
        // A Link, so the chat loads it from the URL — never from a click handler.
        <Link
          href={thinking}
          className="relative -mx-1 inline-flex min-h-[36px] items-center gap-1 rounded-sm px-1 text-footnote font-medium text-terminal-strong transition-colors duration-fast hover:text-terminal after:absolute after:inset-x-0 after:-inset-y-1 after:content-['']"
        >
          Read my thinking <span aria-hidden>→</span>
        </Link>
      ) : null}
      {canJudge(w) ? (
        <div className="flex items-center gap-3 border-t border-border pt-3">
          <span className="flex-1 text-footnote text-text-muted">Right call?</span>
          <Segment<Verdict | "">
            label="Right call?"
            value={(w.verdict?.verdict as Verdict | undefined) ?? ""}
            onChange={(v) => {
              if (v && v !== w.verdict?.verdict) onVerdict(w.id, v);
            }}
            options={VERDICT_CHOICES}
            className="w-[152px] flex-none"
          />
        </div>
      ) : null}
    </Card>
  );
}
