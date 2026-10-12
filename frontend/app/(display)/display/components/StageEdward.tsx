"use client";

// Edward on the stage (2026-09-28). He is the one live Edward on /screen.
//
// Where he stands is the scene's (`me`: on the floor, on a piece, in a
// corner, peeking in from an edge — services/screen_service.py places him);
// what he looks like is the truth:
//   * his mood and his one line come from real signals (lib/screenPresence.ts),
//     plus "Speaking" / "Thinking" only while his voice plays or a turn runs
//     (lib/stageModel.ts::stageStatusLine) — the scene never sets his mood;
//   * news (a fresh alert, an event about to start, a piece that just
//     appeared or changed) is a glance toward the piece it concerns — motion,
//     never a claim; a wake that reached outward plays the acted beat;
//   * poked, he says what is true (presence.poke);
//   * his hands down turns his antenna tip amber (data-hands, display.css).
// Between scenes the page keeps him alive at no quota (lib/stageLife.ts):
// a glance every 8–24 s, or a one- or two-cell step along his floor, away
// from his lane and the why receipt — a one-shot transform, never a loop.
// While he thinks or speaks he is back on his spot and looks at his lane.
// Round 2: the stage may move his spot (a wander, Stage.tsx) — a one-shot
// walk over the time it takes, or a fade across when a piece is in the way
// or motion is reduced; thought bubbles rising from his head earn a glance
// up. The owner at the desk sees him as he is on the stage (no desk bust).

import React, { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import { pickBuddyGreeting } from "@/components/pane/buddyGreeting";
import type { Beat, Gaze } from "@/lib/edwardMood";
import type { EdwardLife } from "@/lib/useEdwardLife";

import type { Cue } from "../hooks/useEdwardState";
import type { GlanceTarget, ScreenPresence } from "../lib/screenPresence";
import {
  GLANCE_HOLD_MS,
  gazeBetween,
  glanceForChange,
  nextLifeDelayMs,
  pickLifeAction,
  type Rand,
} from "../lib/stageLife";
import { rectStyle, type StageGrid, type StageMe, type StageMode, type StagePiece } from "../lib/stageModel";
import { DisplayAvatar } from "./DisplayAvatar";
import { StatusDot } from "./primitives";

const BUDDY_SPEECH_MS = 2_400;
/** His line under him, with its gap. */
const STATUS_H = 34;
/** Until his box is measured. */
const UNMEASURED_PX = 240;
const MIN_PX = 64;

/** A wander to another spot (Stage.tsx): a walk over `ms`, or a fade across. `n` counts arrivals. */
export interface Walk {
  kind: "walk" | "fade";
  ms: number;
  n: number;
}

/** Long enough to read what he said: short small talk 2.4 s, a sentence up to 6 s. */
function speechMs(text: string): number {
  return Math.min(6_000, Math.max(BUDDY_SPEECH_MS, 1_200 + text.length * 55));
}

interface Props {
  me: StageMe;
  stage: StageGrid;
  pieces: StagePiece[];
  presence: ScreenPresence;
  /** The one line under him (stageStatusLine), and its dot (stageStatusTone). */
  statusLine: string;
  statusTone?: ScreenPresence["tone"];
  mode: StageMode;
  /** The side his words and thoughts go. */
  laneSide: "left" | "right" | null;
  /** Thinking or speaking: he looks at his lane and stays put. */
  busy: boolean;
  accent: string;
  /** The owner's size setting: never bigger than this. */
  avatarPx: number;
  cue?: Cue<GlanceTarget> | null;
  beat?: { beat: Beat; key: number } | null;
  /** Standing on a piece, the side of him his line sits on (stageModel.statusSideFor). */
  statusSide?: "left" | "right";
  /** False while the stock scene stands in before the first read: his first
   *  real scene replacing it is arriving, not news. Default true. */
  sceneRead?: boolean;
  /** Idle glances and steps (off in tests). Default true. */
  lifeEnabled?: boolean;
  rand?: Rand;
  /** How he got to where `me` puts him, when the stage moved him. */
  walk?: Walk | null;
  /** Thought bubbles just rose (a counter): he glances up at them. */
  glanceUp?: number;
}

/** The box, measured (0×0 until measured, or without ResizeObserver). */
function useBoxSize<T extends HTMLElement>(ref: React.RefObject<T | null>): { w: number; h: number } {
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (!r) return;
      const w = Math.round(r.width);
      const h = Math.round(r.height);
      setSize((s) => (s.w === w && s.h === h ? s : { w, h }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}

/** His size in a box: the box's width, or its height less his line (his feet are 34/38 down), capped by the owner's setting.
 *  On a piece his line sits beside him, so it takes none of his height. */
function fitPx(box: { w: number; h: number }, at: StageMe["at"], cap: number): number {
  if (!(box.w > 0 && box.h > 0)) return Math.min(cap, UNMEASURED_PX);
  const tall = ((box.h - (at === "on" ? 0 : STATUS_H)) * 36) / 38;
  // Peeking in from an edge he may be wider than his sliver of stage.
  const wide = at === "edge" ? box.w * 2 : box.w;
  return Math.max(MIN_PX, Math.floor(Math.min(cap, tall, wide)));
}

export function StageEdward({
  me,
  stage,
  pieces,
  presence,
  statusLine,
  statusTone,
  mode,
  laneSide,
  busy,
  accent,
  avatarPx,
  cue = null,
  beat = null,
  lifeEnabled = true,
  sceneRead = true,
  statusSide = "right",
  rand = Math.random,
  walk = null,
  glanceUp = 0,
}: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const lifeRef = useRef<EdwardLife | null>(null);
  const box = useBoxSize(boxRef);
  const px = fitPx(box, me.at, avatarPx);

  // Where he stands along his floor: a step moves him from his spot; a new
  // spot (a new scene, or a wander) starts him there again. Busy, he is back
  // on his spot: the lane, the joint, the speech box and the receipt are laid
  // out from there. Derived, not synced by an effect, so a wander never
  // renders a stale step offset against the new spot.
  const spot = `${me.at}|${me.rect.x}|${me.rect.y}`;
  const [stepped, setStepped] = useState<{ spot: string; x: number } | null>(null);
  const x = !busy && stepped && stepped.spot === spot ? stepped.x : me.rect.x;
  const here = useMemo(() => ({ ...me.rect, x }), [me.rect, x]);

  // Poked.
  const [speech, setSpeech] = useState<string | null>(null);
  const speechTimerRef = useRef<number | null>(null);
  const [localBeat, setLocalBeat] = useState<{ beat: Beat; key: string } | null>(
    beat ? { beat: beat.beat, key: `in-${beat.key}` } : null,
  );
  useEffect(() => {
    if (beat) setLocalBeat({ beat: beat.beat, key: `in-${beat.key}` });
  }, [beat?.beat, beat?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  const pokeBeat = presence.poke.beat;
  const pokeSay = presence.poke.say;
  const handlePoke = useCallback(() => {
    if (speechTimerRef.current) window.clearTimeout(speechTimerRef.current);
    const said = pokeSay ?? pickBuddyGreeting();
    setSpeech(said);
    if (pokeBeat) setLocalBeat((b) => ({ beat: pokeBeat, key: `poke-${Date.now()}-${b?.key ?? ""}` }));
    speechTimerRef.current = window.setTimeout(() => setSpeech(null), speechMs(said));
  }, [pokeBeat, pokeSay]);
  useEffect(
    () => () => {
      if (speechTimerRef.current) window.clearTimeout(speechTimerRef.current);
    },
    [],
  );

  // What he looks at, held: his lane while busy, else what the scene says.
  const heldGaze: Gaze | null = useMemo(() => {
    if (busy) return laneSide;
    if (!me.look || me.look === "owner") return null;
    const target = pieces.find((p) => p.id === me.look);
    return target ? gazeBetween(here, target.rect) : null;
  }, [busy, laneSide, me.look, pieces, here]);

  // Thought bubbles rising from his head: he looks up at them, once.
  useEffect(() => {
    if (glanceUp) lifeRef.current?.glance("up", GLANCE_HOLD_MS);
  }, [glanceUp]);

  // News from the signals: glance toward the piece it concerns, once.
  useEffect(() => {
    if (!cue) return;
    const target = pieces.find((p) => p.kind === cue.target);
    const gaze = target ? gazeBetween(here, target.rect) : null;
    if (gaze) lifeRef.current?.glance(gaze, GLANCE_HOLD_MS);
  }, [cue?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  // A piece that just appeared or changed: glance at it.
  // The stock scene drawn before the first read is not a baseline.
  const prevPieces = useRef<StagePiece[] | null>(null);
  useEffect(() => {
    if (!sceneRead) return;
    const g = glanceForChange(here, prevPieces.current, pieces);
    prevPieces.current = pieces;
    if (g?.gaze) lifeRef.current?.glance(g.gaze, GLANCE_HOLD_MS);
  }, [pieces, sceneRead]); // eslint-disable-line react-hooks/exhaustive-deps

  // Idle life: a glance or a step every 8–24 s; nothing while busy.
  const lifeInputs = useRef({ me, x, pieces, stage, busy, laneSide, spot });
  lifeInputs.current = { me, x, pieces, stage, busy, laneSide, spot };
  useEffect(() => {
    if (!lifeEnabled) return;
    let timer = 0;
    const tick = () => {
      const inp = lifeInputs.current;
      if (!inp.busy && (typeof document === "undefined" || document.visibilityState !== "hidden")) {
        const act = pickLifeAction(inp, rand);
        if (act.kind === "step") setStepped({ spot: inp.spot, x: act.x });
        else if (act.gaze) lifeRef.current?.glance(act.gaze, GLANCE_HOLD_MS);
        else lifeRef.current?.blink();
      }
      timer = window.setTimeout(tick, nextLifeDelayMs(rand));
    };
    timer = window.setTimeout(tick, nextLifeDelayMs(rand));
    return () => window.clearTimeout(timer);
  }, [lifeEnabled, rand]);

  const showStatus = presence.known && !!statusLine;
  const status = showStatus ? (
    <div className="st-status" data-edward-status aria-live="polite">
      <StatusDot tone={statusTone ?? presence.tone} size={6} />
      <span>{statusLine}</span>
    </div>
  ) : null;

  // On a piece his feet touch its card: the rig's feet end 34/38 down (4/38
  // of him is air under them), and the card sits inside the piece's 0.6u padding.
  const onPiece = me.at === "on" ? `translateY(calc(${Math.round((px * 4) / 38)}px + var(--u) * 0.6))` : undefined;
  const figure = (
    <div
      data-edward-anchor
      data-hands={presence.handsDown ? "down" : undefined}
      className="st-edward-anchor"
      style={{ width: px, height: px, transform: onPiece }}
    >
      <div className="st-floorglow" aria-hidden />
      <DisplayAvatar
        size={px}
        mood={presence.mood}
        accent={accent}
        gaze={heldGaze}
        ariaLabel={presence.known ? presence.label : "Edward"}
        onClick={handlePoke}
        beat={localBeat?.beat ?? null}
        beatKey={localBeat?.key}
        lifeRef={lifeRef}
      />
      {speech && (
        <div data-testid="buddy-speech-bubble" className="mc-edward-bubble st-bubble" role="status" style={{ top: Math.round(px * 0.04), left: `calc(50% + ${Math.round(px * 0.3)}px)` }}>
          {speech}
          <span className="mc-edward-bubble-tail" />
        </div>
      )}
    </div>
  );

  const cellW = me.rect.w || 1;
  const style = {
    ...rectStyle(me.rect, stage),
    transform: x !== me.rect.x ? `translateX(${((x - me.rect.x) / cellW) * 100}%)` : undefined,
    ...(walk?.kind === "walk" ? { "--st-walk-ms": `${walk.ms}ms` } : {}),
  } as CSSProperties;
  return (
    <div
      ref={boxRef}
      data-stage-edward
      data-at={me.at}
      data-side={me.side}
      data-status-side={me.at === "on" ? statusSide : undefined}
      data-mode={mode}
      data-walk={walk?.kind}
      data-arrive={walk ? (walk.n % 2 ? "a" : "b") : undefined}
      className="st-edward"
      style={style}
    >
      {figure}
      {status}
    </div>
  );
}
