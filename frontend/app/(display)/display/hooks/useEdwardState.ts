"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { Beat } from "@/lib/edwardMood";
import type { MindNow } from "@/lib/mindNowApi";
import { mindBeat } from "@/lib/presenceModel";

import {
  glanceCue,
  screenPresence,
  type GlanceTarget,
  type PresenceAudience,
  type ScreenPresence,
} from "../lib/screenPresence";
import type { SignalsResponse } from "../lib/types";
import { useMindNow, useSignals } from "./useSharedPoll";

/**
 * Edward on /screen and the hub, from real signals only (2026-09-26).
 *
 *   presence — his mood, his one line and its tone (lib/screenPresence.ts):
 *              voice, chat turns, a running wake ("Thinking · morning
 *              wake"), what waits on the owner, workers, the quota, his hands.
 *   cue      — something new arrived (a fresh alert, an event about to
 *              start): the screen turns it into a glance. Motion only; it
 *              never changes the line.
 *   beat     — a wake that reached outward since the last read: `acted`.
 *
 * The signals come from the one shared poll (the alert strip reads the same
 * one), /api/mind/now from its own. The old once-a-minute "consulting ·
 * long-term memory" timer is gone: a glance is not a claim.
 *
 * `audience: "household"` (the hub) leaves out what is the owner's alone — his
 * unread thoughts, held approvals and Chrome link (lib/screenPresence.ts).
 */

export interface Cue<T> {
  target: T;
  /** Changes on every new cue, so the same target can glance again later. */
  key: number;
}

export interface EdwardScreenState {
  presence: ScreenPresence;
  signals: SignalsResponse | null;
  mindNow: MindNow | null;
  cue: Cue<GlanceTarget> | null;
  beat: { beat: Beat; key: number } | null;
}

function hourIn(tz: string | undefined, now: Date): number {
  try {
    const h = new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "numeric", hourCycle: "h23" })
      .formatToParts(now)
      .find((p) => p.type === "hour")?.value;
    return h ? Number(h) % 24 : now.getHours();
  } catch {
    return now.getHours();
  }
}

export function useEdwardState(
  tz?: string,
  { audience = "owner" }: { audience?: PresenceAudience } = {},
): EdwardScreenState {
  const signalsPoll = useSignals();
  const mindPoll = useMindNow();
  const signals = signalsPoll.data;
  const mindNow = mindPoll.data;

  const now = new Date();
  const hour = hourIn(tz, now);
  const presence = useMemo(
    () =>
      screenPresence(
        { signals, signalFailures: signalsPoll.failures, mindNow, hour, audience },
        { now, tz },
      ),
    // `now` only matters for a quota reset's day word; the polls re-render often.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [signals, signalsPoll.failures, mindNow, hour, tz, audience],
  );

  // News between two reads: a glance cue and the acted beat.
  const [cue, setCue] = useState<Cue<GlanceTarget> | null>(null);
  const [beat, setBeat] = useState<{ beat: Beat; key: number } | null>(null);
  const prevSignals = useRef<SignalsResponse | null>(null);
  const prevMind = useRef<MindNow | null>(null);

  useEffect(() => {
    if (!signals) return;
    const target = glanceCue(prevSignals.current, signals, Date.now());
    prevSignals.current = signals;
    if (target) setCue((c) => ({ target, key: (c?.key ?? 0) + 1 }));
  }, [signals]);

  useEffect(() => {
    if (!mindNow) return;
    const b = mindBeat(prevMind.current, mindNow);
    prevMind.current = mindNow;
    if (b) setBeat((p) => ({ beat: b, key: (p?.key ?? 0) + 1 }));
  }, [mindNow]);

  return { presence, signals, mindNow, cue, beat };
}
