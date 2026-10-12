"use client";

// A new chat (2026-09-26; docs/design-system-2026-09-25.md §4 "chat empty
// state"): Edward himself — large, the page's one live Edward (the presence
// bar steps back while he is on screen, lib/presenceHero.tsx), in his real
// mood; he waves on the day's first open and listens (looking down at the
// composer) while the owner types. His greeting streams in word by word, softly.
// Underneath, a few quiet ways in from Today (lib/chatSuggestions.ts): each
// fills the composer through /chat?prompt= and never sends; and "Resume"
// for the owner's last thread.
//
// The presence bar steps back while the hero is on screen, so the hero
// carries Edward's status line as a caption under him — "Waiting on your
// approval", "Thinking · morning wake" — tappable to the presence sheet,
// whenever there is something to say (2026-09-26 review: a held approval
// had no words on the screen the owner lands on most).

import React, { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight, Bell, Calendar, CornerDownRight, MessageSquareText, PawPrint, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { Skeleton } from "@/components/ui/Feedback";
import { getToday } from "@/lib/api";
import type { TodayFeed } from "@/components/today/todayModel";
import { chatSuggestions, resumeTarget, type ChatSuggestion } from "@/lib/chatSuggestions";
import { useGreeting, type GreetingOptions } from "@/lib/useGreeting";
import { useComposing } from "@/lib/useComposing";
import { LAST_WAVE_KEY, useEdwardPresence } from "@/lib/presence";
import { usePresenceHero, usePresenceSheet } from "@/lib/presenceHero";
import { shouldWave, type Beat, type Mood } from "@/lib/edwardMood";

const HERO_PX = 120;
/** A presence beat older than this when the hero mounts is not replayed. */
const FRESH_BEAT_MS = 1500;

const NEED_ICONS: Record<string, LucideIcon> = {
  dose: PawPrint,
  calendar: Calendar,
  heartbeat: MessageSquareText,
  note: Bell,
  approval: Bell,
};

function iconFor(s: ChatSuggestion): LucideIcon {
  return NEED_ICONS[s.needKind ?? ""] ?? ArrowRight;
}

/** The greeting, each word fading in as it lands. */
function Greeting({ text }: { text: string }) {
  const tokens = useMemo(() => text.split(/(\s+)/).filter(Boolean), [text]);
  return (
    <h1 className="max-w-md text-balance text-title2 text-text-primary md:text-[24px] md:leading-[30px]">
      {tokens.map((t, i) =>
        /^\s+$/.test(t) ? (
          <React.Fragment key={i}>{t}</React.Fragment>
        ) : (
          <span key={`${i}-${t}`} className="motion-safe:animate-fade-in">
            {t}
          </span>
        ),
      )}
    </h1>
  );
}

function SuggestionChip({ s }: { s: ChatSuggestion }) {
  const Icon = iconFor(s);
  return (
    <Link
      href={`/chat?prompt=${encodeURIComponent(s.prompt)}`}
      replace
      scroll={false}
      className={cn(
        "inline-flex h-11 max-w-full items-center gap-2 rounded-full border border-border bg-surface px-4 text-footnote text-text-secondary shadow-e1 md:h-9",
        "transition-[transform,background-color,color] duration-press active:scale-[.97] hover:bg-surface-3 hover:text-text-primary",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60 focus-visible:ring-offset-2 focus-visible:ring-offset-primary-bg",
      )}
    >
      <Icon className="h-3.5 w-3.5 flex-none text-text-muted" aria-hidden />
      <span className="truncate">{s.label}</span>
    </Link>
  );
}

const QUIET_MOODS: ReadonlySet<Mood> = new Set<Mood>(["idle", "sleepy", "listen", "speak"]);

/** The status line to show under the hero, or null when he is simply here
 *  — but a resting Edward whose hands in Chrome are down says so. */
export function heroCaption(p: {
  available: boolean;
  mood: Mood;
  status: string;
  signals?: { handsDown?: boolean };
}): string | null {
  if (!p.available || !p.status.trim()) return null;
  if (QUIET_MOODS.has(p.mood) && !(p.signals?.handsDown && (p.mood === "idle" || p.mood === "sleepy"))) return null;
  return p.status;
}

export interface ChatEmptyStateProps {
  /** Today's feed (tests). Default: GET /api/today. */
  loadFeed?: () => Promise<TodayFeed>;
  /** The greeting's sources (tests). */
  greeting?: GreetingOptions;
}

export function ChatEmptyState({ loadFeed = getToday, greeting: greetingOpts }: ChatEmptyStateProps) {
  const heroRef = useRef<HTMLDivElement>(null);
  const live = usePresenceHero(heroRef);
  const sheet = usePresenceSheet();
  const presence = useEdwardPresence();
  const composing = useComposing();
  const { text, loading } = useGreeting(true, greetingOpts);
  const [feed, setFeed] = useState<TodayFeed | null>(null);
  const loadRef = useRef(loadFeed);
  loadRef.current = loadFeed;

  useEffect(() => {
    let alive = true;
    loadRef
      .current()
      .then((f) => {
        if (alive) setFeed(f);
      })
      .catch(() => {
        /* Today unreadable: no suggestions, nothing to apologise for */
      });
    return () => {
      alive = false;
    };
  }, []);

  // Beats: the presence's (wave on the day's first open, nods, acted) as
  // they happen — never one that fired before he was on screen. Outside a
  // presence provider he waves on his own, once a day.
  const mountedAt = useRef(Date.now());
  const [ownBeat, setOwnBeat] = useState<{ beat: Beat; key: number } | null>(null);
  useEffect(() => {
    if (presence.available) return;
    try {
      const last = window.localStorage.getItem(LAST_WAVE_KEY);
      const now = new Date();
      if (!shouldWave(last, now)) return;
      window.localStorage.setItem(LAST_WAVE_KEY, now.toISOString());
      setOwnBeat({ beat: "wave", key: 1 });
    } catch {
      /* no storage: no way to wave only once */
    }
  }, [presence.available]);
  const pb = presence.beat;
  const beat = pb && pb.at >= mountedAt.current - FRESH_BEAT_MS ? { beat: pb.beat, key: pb.key } : ownBeat;

  const suggestions = useMemo(() => chatSuggestions(feed), [feed]);
  const resume = useMemo(() => resumeTarget(feed), [feed]);
  const mood = composing ? "listen" : presence.mood;
  const caption = heroCaption(presence);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain bg-primary-bg">
      <div className="mx-auto flex min-h-full w-full max-w-xl flex-col items-center justify-center px-6 pb-10 pt-8 text-center">
        <div ref={heroRef} className="mb-5">
          <EdwardAvatar
            size={HERO_PX}
            variant="full"
            live={live}
            mood={mood}
            gaze={composing ? "down" : null}
            beat={beat?.beat ?? null}
            beatKey={beat?.key}
            aria-label={presence.label}
            onClick={sheet.open}
          />
        </div>

        {caption ? (
          <button
            type="button"
            onClick={sheet.open}
            className="relative -mt-2 mb-4 inline-flex max-w-full items-center gap-1.5 rounded-xs text-footnote text-text-secondary transition-colors duration-fast after:absolute after:-inset-2.5 after:content-[''] hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60 motion-safe:animate-fade-in"
          >
            <span className="truncate">{caption}</span>
            <ArrowRight className="h-3.5 w-3.5 flex-none" aria-hidden />
          </button>
        ) : null}

        {text ? (
          <Greeting text={text} />
        ) : loading ? (
          <div className="flex w-full max-w-xs flex-col items-center" role="status" aria-label="Edward is saying hello">
            <Skeleton lines={2} className="w-full" lineClassName="h-4 mx-auto" />
          </div>
        ) : (
          <h1 className="text-title2 text-text-primary">Hey.</h1>
        )}

        {suggestions.length > 0 ? (
          <nav aria-label="Ways to start" className="mt-7 flex w-full flex-wrap justify-center gap-2 motion-safe:animate-fade-rise">
            {suggestions.map((s) => (
              <SuggestionChip key={s.id} s={s} />
            ))}
          </nav>
        ) : null}

        {resume ? (
          <Link
            href={resume.href}
            className="relative mt-5 inline-flex max-w-full items-center gap-1.5 rounded-xs text-footnote text-text-muted transition-colors duration-fast after:absolute after:-inset-3 after:content-[''] hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60 motion-safe:animate-fade-in"
          >
            <CornerDownRight className="h-3.5 w-3.5 flex-none" aria-hidden />
            <span className="truncate">Resume: {resume.title}</span>
          </Link>
        ) : null}
      </div>
    </div>
  );
}
