"use client";

// Edward's screen on the host Mac (/screen; /display and /fullscreen are
// aliases). Since 2026-09-28 a stage he writes on (Stage.tsx): the scene he
// composes with compose_screen, the lines he says with say_on_screen, and
// his thinking while a turn really runs — plus whatever he puts on top with
// show_on_screen (a picture, a movie, a note). The owner's preferences (accent,
// avatar size, thinking on screen, speak aloud) sit in the gear panel;
// what shows and where is Edward's call. Night is the page's own (the hour
// in the owner's zone or the gear panel's time of day) — no model call. The
// owner at the host Mac gets the normal stage (round 2): they often watch it.

import { usePresenceBeacon } from "@/lib/usePresenceBeacon";
import { useUserTimezone } from "@/lib/useTimezone";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useDisplaySettings } from "../hooks/useDisplaySettings";
import { useEdwardState } from "../hooks/useEdwardState";
import { usePoll } from "../hooks/usePoll";
import { useSharedPoll } from "../hooks/useSharedPoll";
import { useSpeakAloud } from "../hooks/useSpeakAloud";
import { useWakeLock } from "../hooks/useWakeLock";
import { LIVE_POLL_MS, LIVE_URL, dismissShow, fetchLive, fetchScreen } from "../lib/screenApi";
import { isShowExpired, showKey, type ShowContent } from "../lib/screenModel";
import { liveOnStage } from "../lib/thinkingModel";
import { coveredCells, nightDrift, speechView, stageFromSnapshot, stageMode, type StageGrid, type StageRect } from "../lib/stageModel";
import { ACCENT_HEX, AVATAR_PX, type TimeOfDay } from "../lib/types";
import { ComposeBar } from "./ComposeBar";
import { DisplaySettingsPanel } from "./DisplaySettingsPanel";
import { KeyboardHints } from "./KeyboardHints";
import { ScreenOverlay } from "./ScreenOverlay";
import { Stage } from "./Stage";

const SCREEN_POLL_MS = 4_000;
/** The stage's own clock: lines expire, night starts, the drift moves. */
const TICK_MS = 5_000;
/** The compose bar and the key hints show for this long after the mouse moves. */
const AWAKE_MS = 6_000;

function hourIn(tz: string): number {
  try {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "numeric", hour12: false }).formatToParts(new Date());
    const h = parts.find((p) => p.type === "hour")?.value;
    return h ? parseInt(h, 10) % 24 : new Date().getHours();
  } catch {
    return new Date().getHours();
  }
}

function resolveTimeOfDay(setting: TimeOfDay, hour: number): TimeOfDay {
  if (setting !== "auto") return setting;
  if (hour < 6) return "night";
  if (hour < 12) return "morning";
  if (hour < 18) return "day";
  if (hour < 21) return "dusk";
  return "night";
}

function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), everyMs);
    return () => window.clearInterval(id);
  }, [everyMs]);
  return now;
}

/** The mouse moved in the last few seconds (the owner is on a mouse; nothing else wakes the chrome). */
function useAwake(ms: number): boolean {
  const [awake, setAwake] = useState(false);
  useEffect(() => {
    let timer = 0;
    const wake = () => {
      setAwake(true);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setAwake(false), ms);
    };
    window.addEventListener("mousemove", wake);
    window.addEventListener("keydown", wake);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("mousemove", wake);
      window.removeEventListener("keydown", wake);
    };
  }, [ms]);
  return awake;
}

/** While the compose bar is up: what it and its reply card cover, in stage cells (the card grows as he answers). */
const COMPOSE_MEASURE_MS = 1_000;

function useComposeCells(
  up: boolean,
  rootRef: React.RefObject<HTMLDivElement | null>,
  barRef: React.RefObject<HTMLDivElement | null>,
  stage: StageGrid,
): StageRect | null {
  const [cells, setCells] = useState<StageRect | null>(null);
  const { cols, rows } = stage;
  useEffect(() => {
    if (!up) {
      setCells(null);
      return;
    }
    const measure = () => {
      const root = rootRef.current;
      const bar = barRef.current;
      if (!root || !bar) return;
      const card = bar.querySelector('[data-testid="compose-reply-card"]');
      const boxes = [bar, card].filter((el): el is Element => !!el).map((el) => el.getBoundingClientRect());
      const r = root.getBoundingClientRect();
      const next = coveredCells(
        {
          left: Math.min(...boxes.map((b) => b.left)),
          top: Math.min(...boxes.map((b) => b.top)),
          right: Math.max(...boxes.map((b) => b.right)),
          bottom: Math.max(...boxes.map((b) => b.bottom)),
        },
        { left: r.left, top: r.top, width: r.width, height: r.height },
        { cols, rows },
      );
      setCells((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
    };
    measure();
    const id = window.setInterval(measure, COMPOSE_MEASURE_MS);
    return () => window.clearInterval(id);
  }, [up, rootRef, barRef, cols, rows]);
  return cells;
}

export function ScreenLayout() {
  usePresenceBeacon({ kind: "display", alwaysActive: true });
  const { settings, update } = useDisplaySettings();
  const tz = useUserTimezone();
  const edward = useEdwardState(tz);
  const wakeLock = useWakeLock();
  const { data: screen, refetch } = usePoll(fetchScreen, SCREEN_POLL_MS);
  const livePoll = useSharedPoll(LIVE_URL, fetchLive, LIVE_POLL_MS);
  // The poll keeps its last good answer through a failure; past two failed
  // reads (or while he is offline) that answer is stale, and the lane must
  // not say "Thinking" beside an "Offline" line.
  const live = liveOnStage(livePoll, { offline: edward.presence.mood === "offline" });
  const nowMs = useNow(TICK_MS);
  const awake = useAwake(AWAKE_MS);
  // The compose bar is up while the mouse moved, or while a reply card is on it or the owner types.
  const [composeActive, setComposeActive] = useState(false);
  const composeUp = awake || composeActive;
  const rootRef = useRef<HTMLDivElement>(null);
  const composeRef = useRef<HTMLDivElement>(null);

  const accent = ACCENT_HEX[settings.accent] ?? ACCENT_HEX.green;
  const avatarPx = AVATAR_PX[settings.avatar_size] ?? AVATAR_PX.lg;
  const hour = hourIn(tz);
  const tod = useMemo(() => resolveTimeOfDay(settings.time_of_day, hour), [settings.time_of_day, hour]);
  const rootStyle = { "--ed-accent": accent } as React.CSSProperties;

  // The show layer, keyed so a playing movie does not restart on every poll,
  // with a local expiry so a 30s alert really leaves at 30s.
  const [show, setShow] = useState<ShowContent | null>(null);
  const keyRef = useRef("");
  useEffect(() => {
    const next = screen?.show && !isShowExpired(screen.show, Date.now()) ? screen.show : null;
    const k = showKey(next);
    if (k !== keyRef.current) {
      keyRef.current = k;
      setShow(next);
    }
  }, [screen]);
  useEffect(() => {
    if (!show?.expires_at) return;
    const ms = Date.parse(show.expires_at) - Date.now();
    if (ms <= 0) return;
    const id = window.setTimeout(() => {
      keyRef.current = "";
      setShow(null);
    }, ms);
    return () => window.clearTimeout(id);
  }, [show]);

  const dismiss = useCallback(() => {
    keyRef.current = "";
    setShow(null);
    dismissShow("tap").then(refetch).catch(() => {});
  }, [refetch]);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // The stage: his scene (or the stock one while the first read is out).
  const view = useMemo(() => stageFromSnapshot(screen), [screen]);
  const mode = stageMode({ ownerAtMini: view.ownerAtMini, hour, show, timeOfDay: settings.time_of_day });
  const current = speechView(view.lines, nowMs).current;
  const { speaking, played } = useSpeakAloud(current, settings.speak_aloud !== false);
  const composeCells = useComposeCells(composeUp, rootRef, composeRef, view.stage);

  return (
    <div
      ref={rootRef}
      className="mc-root st-root"
      data-tod={tod}
      data-density={settings.density}
      data-mode={mode}
      data-awake={awake ? "true" : undefined}
      style={rootStyle}
    >
      <Stage
        view={view}
        mode={mode}
        presence={edward.presence}
        cue={edward.cue}
        beat={edward.beat}
        live={live}
        showThinking={settings.show_thinking !== false}
        speaking={speaking}
        playedAloud={played}
        accent={accent}
        avatarPx={avatarPx}
        tz={tz}
        nowMs={nowMs}
        drift={mode === "night" ? nightDrift(nowMs) : undefined}
        composeUp={composeUp}
        composeRect={composeCells}
        dreaming={mode === "night" || hour >= 23 || hour < 6}
      />

      <div className="st-compose" ref={composeRef}>
        <ComposeBar onActiveChange={setComposeActive} />
      </div>

      <ScreenOverlay show={show} onDismiss={dismiss} />
      <DisplaySettingsPanel settings={settings} update={update} wakeLock={wakeLock} />
      {awake && <KeyboardHints />}
    </div>
  );
}
