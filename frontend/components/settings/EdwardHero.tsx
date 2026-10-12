"use client";

// The Edward tab's hero: Edward himself, large and — while he is in view —
// the one live (breathing) Edward on the screen (lib/presenceHero hands the
// presence bar's Edward back when he scrolls out). His mood and status come
// from real signals only (lib/presence); tapping him opens what he did and
// is doing. Under him, the opening of his own page in his voice.

import React, { useMemo, useRef } from "react";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { useEdwardPresence, useFreshBeat } from "@/lib/presence";
import { presenceSignals } from "@/lib/presenceModel";
import { deriveMood, statusLabel, statusLine } from "@/lib/edwardMood";
import { usePresenceHero, usePresenceSheet } from "@/lib/presenceHero";
import { formatRelative } from "@/lib/time";
import { StateExcerpt } from "./StateDoc";
import { useEdwardTabStatus } from "./useEdwardTabStatus";

export function EdwardHero() {
  const presence = useEdwardPresence();
  const { data, mindNow } = useEdwardTabStatus();
  const heroRef = useRef<HTMLDivElement>(null);
  const live = usePresenceHero(heroRef);
  const sheet = usePresenceSheet();

  // Outside a presence provider, read his mood from this page's own mind/now.
  const own = useMemo(() => {
    const signals = presenceSignals({ chat: null, online: true, mindNow, hour: new Date().getHours() });
    return { mood: deriveMood(signals), status: statusLine(signals), label: statusLabel(signals) };
  }, [mindNow]);
  const mood = presence.available ? presence.mood : own.mood;
  // Inside the provider its line is the truth — "" before the first answer
  // about his mind, and then no line at all (never a guessed "Resting").
  const status = presence.available ? presence.status : own.status;
  const label = presence.available ? presence.label : own.label;
  // Only beats that arrive while the tab is open: the morning wave, a wince
  // or the "acted" sonar is not replayed on every /settings visit.
  const heroBeatNow = useFreshBeat(presence.beat, true);

  const resting = mood === "idle" || mood === "sleepy";
  const nextWake =
    resting && mindNow?.next_wake_at && Date.parse(mindNow.next_wake_at) > Date.now()
      ? `next wake ${formatRelative(mindNow.next_wake_at)}`
      : null;

  return (
    <section
      aria-label="Edward now"
      className="relative flex flex-col overflow-hidden rounded-xl border border-border bg-surface p-4 shadow-e1 md:p-5 lg:justify-center lg:p-6"
    >
      <div
        aria-hidden
        className="pointer-events-none absolute -left-16 -top-20 h-56 w-56 rounded-full bg-terminal/[0.07] blur-3xl"
      />
      <div className="relative flex items-center gap-4">
        <div ref={heroRef} className="flex-none">
          <EdwardAvatar
            size={88}
            mood={mood}
            live={live}
            beat={heroBeatNow}
            beatKey={presence.beat?.key}
            aria-label={`${label} — open what he did and is doing`}
            onClick={sheet.open}
          />
        </div>
        <div className="min-w-0">
          <h1 className="font-sans text-title1 text-text-primary">Edward</h1>
          {/* Not a live region: the presence bar/header announce his line, and
              the relative "next wake in Nm" would be re-read every 20 s. */}
          {status || nextWake ? (
            <p className="mt-1 text-footnote text-text-secondary">
              {status}
              {nextWake ? <span className="text-text-muted">{status ? " · " : ""}{nextWake}</span> : null}
            </p>
          ) : null}
        </div>
      </div>
      <StateExcerpt
        className="relative mt-4 lg:mt-5"
        doc={data?.stateDoc ?? null}
        loading={!data}
        failed={data?.stateDocFailed}
      />
    </section>
  );
}
