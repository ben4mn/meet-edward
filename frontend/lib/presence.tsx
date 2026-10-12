"use client";

// Edward's live presence — the one answer every screen reads to draw the
// live Edward and his status line (2026-09-25, docs/design-system-2026-09-25.md
// §1 "Edward is the status bar", §4 moods and beats).
//
//   <ChatProvider>
//     <EdwardPresenceProvider>…app…</EdwardPresenceProvider>
//   </ChatProvider>
//
//   const { signals, mood, status, label, beat, mindNow, online } = useEdwardPresence();
//
// Signals are real only: the chat turn (ChatContext `turn` — phase and
// running tools), live voice (`isVoiceMode` + the voice view's phase), the
// network (navigator.onLine and the mind/now poll failing twice in a row),
// and GET /api/mind/now (a wake running now, what waits on the owner, the quota,
// his hands in Chrome). mood / status / label come from lib/edwardMood.ts;
// every decision (signals, backoff, beats, the beat gap) is pure in
// lib/presenceModel.ts.
//
// Polling: on mount, every 20 s while the tab is visible, at once when it
// comes back, never while hidden; a failed poll is retried after 5 s and
// then with exponential backoff (≤ 5 min).
//
// The first paint (2026-09-26): every answer is kept in localStorage
// (MIND_NOW_CACHE_KEY) and a cold open paints one under ten minutes old at
// once. With none, he makes no claim until the first answer (`known:
// false`: an empty line, "Edward" for the label, plain idle) — never a
// "Resting" guess while an approval may be waiting.
//
// Beats (`beat` = the last one, with a rising `key` to watch): nod — a tool
// in the chat turn went well, or the turn finished; wince — a tool or the
// turn failed; acted — a send/push/note went out in the turn, or mind/now's
// last wake changed to one that acted; perk — back in the foreground after
// ≥ 15 s away; wave — the first open of the local day (localStorage
// LAST_WAVE_KEY). Beats are ≥ 1.2 s apart; of two held the one that matters
// more plays. The avatar plays `beat.beat` whenever `beat.key` changes.

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useChatPresence } from "./ChatContext";
import { PresenceContext } from "./presenceContext";
import { deriveMood, shouldWave, statusLabel, statusLine, type Beat, type EdwardSignals, type Mood } from "./edwardMood";
import { fetchMindNow, type MindNow } from "./mindNowApi";
import {
  MIND_NOW_CACHE_KEY,
  NEW_GATE,
  admitBeat,
  encodeMindNowCache,
  hydrateMindNowCache,
  isOnline,
  mindBeat,
  pollDelayMs,
  presenceSignals,
  releaseHeld,
  shouldPerk,
  turnBeat,
  type BeatGate,
  type PollTiming,
} from "./presenceModel";
import type { Turn } from "./turnModel";

/** localStorage: when Edward last waved (ISO); one wave per local day. */
export const LAST_WAVE_KEY = "edward:lastWaveAt";

export interface PresenceBeat {
  beat: Beat;
  /** Rises on every beat played — watch it, not `beat` (two nods in a row are two beats). */
  key: number;
  /** When it started (ms since epoch). */
  at: number;
}

export interface EdwardPresence {
  /** The real signals behind the mood (lib/edwardMood.ts EdwardSignals). */
  signals: EdwardSignals;
  mood: Mood;
  /** The one honest line under his name ("Working · 2 tools", "Thinking · morning wake"). */
  status: string;
  /** The longer aria-label / title form ("Edward · Resting until Tue 3pm (week's quota spent)"). */
  label: string;
  /** The last one-shot beat, or null before the first. */
  beat: PresenceBeat | null;
  /** The last GET /api/mind/now answer; null before the first (or from an older backend). */
  mindNow: MindNow | null;
  online: boolean;
  /** False outside an EdwardPresenceProvider: a static resting fallback. */
  available: boolean;
  /** Poll mind/now now (after "Wake now", an approval, opening a thought). */
  refresh: () => void;
}

export interface EdwardPresenceProviderProps {
  children: ReactNode;
  /** false = no polling and no beats (signals from the chat only). Default true. */
  enabled?: boolean;
  /** Poll timings (tests; a slower screen). */
  timing?: PollTiming;
  /** The clock (tests). */
  clock?: () => number;
}

const noop = () => {};

function isHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

function navigatorOnline(): boolean {
  return typeof navigator === "undefined" || navigator.onLine !== false;
}

function readMindNowCache(now: number): MindNow | null {
  if (typeof window === "undefined") return null;
  try {
    return hydrateMindNowCache(window.localStorage.getItem(MIND_NOW_CACHE_KEY), now);
  } catch {
    return null;
  }
}

function writeMindNowCache(data: MindNow | null, now: number): void {
  if (typeof window === "undefined") return;
  try {
    if (data) window.localStorage.setItem(MIND_NOW_CACHE_KEY, encodeMindNowCache(data, now));
    else window.localStorage.removeItem(MIND_NOW_CACHE_KEY);
  } catch {
    /* storage full or blocked: the next open simply starts unknown */
  }
}

export function EdwardPresenceProvider({ children, enabled = true, timing, clock = Date.now }: EdwardPresenceProviderProps) {
  const chat = useChatPresence();
  // The provider mounts on the client only (under AuthGuard), so reading
  // storage in the initialiser cannot mismatch a server render.
  const [mindNow, setMindNow] = useState<MindNow | null>(() => (enabled ? readMindNowCache(clock()) : null));
  // A poll has answered (ok, 404 or 401): whatever he shows now is known.
  const [answered, setAnswered] = useState(false);
  const [failures, setFailures] = useState(0);
  const [navOnline, setNavOnline] = useState(true);
  const [hour, setHour] = useState(() => new Date(clock()).getHours());
  const [beat, setBeat] = useState<PresenceBeat | null>(null);

  const clockRef = useRef(clock);
  clockRef.current = clock;
  const timingRef = useRef(timing);
  timingRef.current = timing;

  // ---- beats: one at a time, ≥ 1.2 s apart --------------------------------
  const gateRef = useRef<BeatGate>(NEW_GATE);
  const beatKeyRef = useRef(0);
  const holdTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const play = useCallback((b: Beat, now: number) => {
    beatKeyRef.current += 1;
    setBeat({ beat: b, key: beatKeyRef.current, at: now });
  }, []);

  const release = useCallback(() => {
    holdTimerRef.current = null;
    const now = clockRef.current();
    const step = releaseHeld(gateRef.current, now);
    gateRef.current = step.gate;
    if (step.play) play(step.play, now);
    else if (step.gate.held) holdTimerRef.current = setTimeout(release, step.waitMs);
  }, [play]);

  const emit = useCallback(
    (b: Beat) => {
      if (!enabledRef.current) return;
      const now = clockRef.current();
      const step = admitBeat(gateRef.current, b, now);
      gateRef.current = step.gate;
      if (step.play) {
        play(step.play, now);
      } else if (step.gate.held && !holdTimerRef.current) {
        holdTimerRef.current = setTimeout(release, step.waitMs);
      }
    },
    [play, release],
  );

  useEffect(
    () => () => {
      if (holdTimerRef.current) clearTimeout(holdTimerRef.current);
      holdTimerRef.current = null;
    },
    [],
  );

  // ---- the chat turn's beats ------------------------------------------------
  const turn = chat?.turn ?? null;
  const prevTurnRef = useRef<Turn | null | undefined>(undefined);
  useEffect(() => {
    const prev = prevTurnRef.current;
    prevTurnRef.current = turn;
    const b = turnBeat(prev, turn);
    if (b) emit(b);
  }, [turn, emit]);

  // ---- the day's first open ---------------------------------------------------
  useEffect(() => {
    if (!enabled) return;
    let last: string | null = null;
    try {
      last = window.localStorage.getItem(LAST_WAVE_KEY);
    } catch {
      return; // no storage: no way to wave only once, so do not wave
    }
    const now = new Date(clockRef.current());
    if (!shouldWave(last, now)) return;
    try {
      window.localStorage.setItem(LAST_WAVE_KEY, now.toISOString());
    } catch {
      /* storage full or blocked */
    }
    emit("wave");
  }, [enabled, emit]);

  // ---- polling mind/now ---------------------------------------------------------
  const pollRef = useRef<() => void>(noop);
  const prevMindRef = useRef<MindNow | null>(null);

  useEffect(() => {
    setNavOnline(navigatorOnline());
    if (!enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;
    let generation = 0;
    let fails = 0;
    let hiddenAt: number | null = isHidden() ? clockRef.current() : null;

    const clearTimer = () => {
      if (timer) clearTimeout(timer);
      timer = null;
    };
    const schedule = (ms: number) => {
      clearTimer();
      if (cancelled || isHidden()) return;
      timer = setTimeout(() => void poll(), ms);
    };

    // The latest poll wins: a new one cancels one still in flight.
    const poll = async () => {
      if (cancelled || isHidden()) return;
      clearTimer();
      controller?.abort();
      const mine = ++generation;
      const c = new AbortController();
      controller = c;
      const result = await fetchMindNow({ signal: c.signal });
      if (cancelled || mine !== generation) return;
      controller = null;
      if (result.status === "aborted") return; // hidden mid-poll; the return polls again
      if (result.status === "ok") {
        fails = 0;
        const b = mindBeat(prevMindRef.current, result.data);
        prevMindRef.current = result.data;
        setMindNow(result.data);
        setAnswered(true);
        writeMindNowCache(result.data, clockRef.current());
        if (b) emit(b);
      } else if (result.status === "unreachable") {
        fails += 1;
      } else {
        // missing (older backend) / unauthorized: he answers, just not with
        // this — so a cached answer is no longer his.
        fails = 0;
        prevMindRef.current = null;
        setMindNow(null);
        setAnswered(true);
        writeMindNowCache(null, clockRef.current());
      }
      setFailures(fails);
      setHour(new Date(clockRef.current()).getHours());
      schedule(pollDelayMs(fails, timingRef.current));
    };
    pollRef.current = () => void poll();

    const onVisibility = () => {
      if (isHidden()) {
        hiddenAt = clockRef.current();
        clearTimer();
        controller?.abort();
        return;
      }
      if (shouldPerk(hiddenAt, clockRef.current())) emit("perk");
      hiddenAt = null;
      void poll();
    };
    const onOnline = () => {
      setNavOnline(true);
      void poll();
    };
    const onOffline = () => setNavOnline(false);

    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    void poll();

    return () => {
      cancelled = true;
      clearTimer();
      controller?.abort();
      pollRef.current = noop;
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, [enabled, emit]);

  const refresh = useCallback(() => pollRef.current(), []);

  // ---- the value: re-rendered for consumers only when a claim changes -----------
  const online = isOnline(navOnline, failures);
  const known = !enabled || answered || mindNow !== null;
  const signals = presenceSignals({ chat, online, known, mindNow, hour });
  const signalsKey = JSON.stringify(signals);

  const value = useMemo<EdwardPresence>(
    () => ({
      signals,
      mood: deriveMood(signals),
      status: statusLine(signals),
      label: statusLabel(signals),
      beat,
      mindNow,
      online,
      available: true,
      refresh,
    }),
    // signals is rebuilt every render; its content is what matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [signalsKey, beat, mindNow, online, refresh],
  );

  return <PresenceContext.Provider value={value}>{children}</PresenceContext.Provider>;
}

// useEdwardPresence and useFreshBeat live in ./presenceHooks (2026-09-26
// perf pass) so a page that only reads presence (Today's hero) does not
// statically pull in this provider and ChatContext with it. Re-exported
// here so every existing import keeps working.
export { useEdwardPresence, useFreshBeat } from "./presenceHooks";
