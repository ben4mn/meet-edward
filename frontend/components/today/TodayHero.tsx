"use client";

// The presence hero at the top of Today (UX pass 2026-09-26): the date,
// the one live Edward (mood and beats from real signals) beside his
// greeting, and one quiet line that says only what the signals say.
// Tapping him opens the presence sheet: what he did and is doing.

import React, { useEffect, useRef, useState } from "react";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { useEdwardPresence } from "@/lib/presenceHooks";
import { usePresenceHero, usePresenceSheet } from "@/lib/presenceHero";
import { useGreeting } from "@/lib/useGreeting";
import { cn } from "@/lib/utils";
import { dateOverline, greetingWithout, heroBeat, heroSubline, isContinuation, keepTogether } from "./heroModel";
import { needTitle, type TodayDid, type TodayNeed, type TodayPresence } from "./todayModel";

/** After the owner resolves something, wait this long (collapsing a run of
 *  taps) before asking for a fresh greeting. */
const GREETING_REFRESH_DELAY_MS = 800;

const asText = (n: TodayNeed) => ({ kind: n.kind, title: needTitle(n), detail: n.detail });

/** A minute clock, so "next look in 38 min" stays true while Today is open. */
export function useMinuteClock(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(id);
  }, []);
  return now;
}

/** The greeting, word by word: words already on screen stay put, new ones
 *  fade in (reduced motion: the global rule turns the fade into ≤120 ms). */
function StreamedLine({ text }: { text: string }) {
  const prev = useRef("");
  const generation = useRef(0);
  if (!isContinuation(prev.current, text)) generation.current += 1;
  prev.current = text;
  const words = text.split(/(\s+)/);
  return (
    <span key={generation.current}>
      {words.map((w, i) =>
        /^\s+$/.test(w) ? (
          w
        ) : (
          <span key={i} className="animate-fade-in">
            {w}
          </span>
        ),
      )}
    </span>
  );
}

export function TodayHero({
  presence: todayPresence,
  wakes,
  actions,
  tz,
  gone = [],
  live = [],
}: {
  presence: TodayPresence | null;
  wakes: number | null;
  /** "What I did" rows: an acted look is named by its action. */
  actions: TodayDid[];
  tz: string | undefined;
  /** Needs that were on Today and are not any more (the owner resolved them). */
  gone?: TodayNeed[];
  /** Needs still waiting. */
  live?: TodayNeed[];
}) {
  const ref = useRef<HTMLElement>(null);
  const heroLive = usePresenceHero(ref);
  const sheet = usePresenceSheet();
  const presence = useEdwardPresence();
  const [mountedAt] = useState(() => Date.now());
  const now = useMinuteClock();
  const { text, loading, refresh } = useGreeting(true, { tz });

  // Review 2026-09-26: once the owner resolves something the greeting names, it
  // stops saying so at once (the line on screen at that moment loses the
  // clause), and a fresh line is asked for.
  const textRef = useRef(text);
  textRef.current = text;
  const [staleLine, setStaleLine] = useState<string | null>(null);
  const goneKey = gone.map((n) => n.id).sort().join("|");
  useEffect(() => {
    if (!goneKey || !textRef.current) return;
    setStaleLine(textRef.current);
    const t = setTimeout(() => void refresh(), GREETING_REFRESH_DELAY_MS);
    return () => clearTimeout(t);
  }, [goneKey, refresh]);
  const shown =
    text && staleLine && staleLine.startsWith(text) ? greetingWithout(text, gone.map(asText), live.map(asText)) : text;

  const beat = heroBeat(presence.beat, mountedAt);
  const parts = heroSubline({
    presence: todayPresence,
    mindNow: presence.mindNow,
    wakes,
    mood: presence.mood,
    status: presence.status,
    actions,
    now,
    tz,
  });

  return (
    <section ref={ref} aria-label="Edward" className="relative">
      {/* A faint wash of his light behind him, stronger while he works. */}
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute -left-10 -top-10 h-44 w-44 rounded-full bg-terminal/[0.07] blur-3xl transition-opacity duration-slow",
          presence.mood === "think" || presence.mood === "work" ? "opacity-100" : "opacity-60",
        )}
      />
      <p className="relative text-overline text-text-muted" suppressHydrationWarning>
        {dateOverline(now, tz)}
      </p>
      <div className="relative mt-3 flex items-start gap-3.5">
        <button
          type="button"
          onClick={sheet.open}
          aria-haspopup="dialog"
          aria-label={`${presence.label}. What I did and am doing`}
          title={presence.label}
          className="-m-1 flex-none rounded-full p-1 transition-transform duration-press ease-out active:scale-[.94] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60 focus-visible:ring-offset-2 focus-visible:ring-offset-primary-bg"
        >
          <EdwardAvatar
            size={56}
            variant="full"
            live={heroLive}
            mood={presence.mood}
            beat={beat}
            beatKey={presence.beat?.key}
          />
        </button>
        <div className="min-w-0 flex-1 pt-0.5">
          {/* Not a live region: it streams word by word, and a screen reader
              would read each fragment (review 2026-09-26). It is the first
              line of the page; the finished line is announced once below. */}
          <p data-greeting="" className="text-title3 font-semibold text-text-primary text-pretty">
            {shown ? (
              <StreamedLine text={shown} />
            ) : (
              <span className="block space-y-2 pt-1" aria-label="Edward is saying hello">
                <span className="block h-3.5 w-11/12 rounded-full bg-surface-3 motion-safe:animate-pulse" />
                <span className="block h-3.5 w-3/5 rounded-full bg-surface-3 motion-safe:animate-pulse" />
              </span>
            )}
          </p>
          <span data-greeting-live="" className="sr-only" aria-live="polite" aria-atomic="true">
            {loading ? "" : shown}
          </span>
          {parts.length ? (
            <p className="mt-1.5 text-footnote text-text-muted [overflow-wrap:anywhere]" suppressHydrationWarning>
              {parts.map(keepTogether).join(" · ")}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}
