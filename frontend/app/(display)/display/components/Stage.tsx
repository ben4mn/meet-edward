"use client";

// The stage (2026-09-28): /screen is a stage Edward writes on, not a grid he
// fills. One scene of freely placed pieces (compose_screen), where he stands,
// the lines he says (say_on_screen), and his thinking while a mind or
// surfaces turn really runs (GET /api/screen/live).
//
//   * pieces sit where services/screen_service.py placed them on 24×12 cells;
//   * the lane is the side of him with more room (lib/stageModel.ts::laneFor):
//     his words, else his live thinking, else the one "Earlier · …" line of a
//     turn that just folded, else (round 2) his mind — one of his recent
//     thoughts at a time, labelled with its own time, drifting at random;
//     what his words or thoughts cross fades back; while the compose bar is
//     up the lane ends above it and he steps out of its way (aside along the
//     floor, else up onto the bar — lib/stageLife.ts::clearOfBand);
//   * the why receipt sits bottom-left: "I arranged this at 4:10 pm — …", or
//     "You asked at 6:10 pm · stays up until 8:10 pm" while the owner's ask holds;
//   * now and then (1–4 min, at random) he wanders to another free spot on
//     the floor and his lane is laid out from there (a new layout — his spot
//     or the pieces' places — puts him back where the scene says); thought bubbles rise
//     once from his head when a thought surfaces or a live turn starts;
//   * night (23:00–06:00): darker, dimmer, drifting for burn-in. The owner at the
//     host Mac gets the normal stage (round 2 removed the desk mode).
// Only his breath loops; everything else enters or moves once.

import React, { useEffect, useRef, useState, type CSSProperties } from "react";

import { formatClock } from "@/lib/time";
import type { Beat } from "@/lib/edwardMood";

import type { Cue } from "../hooks/useEdwardState";
import { useMindDrift } from "../hooks/useMindDrift";
import { useToolLabels } from "../hooks/useToolLabels";
import type { GlanceTarget, ScreenPresence } from "../lib/screenPresence";
import { clearOfBand, nextWanderDelayMs, pickWanderSpot, walkKind, walkMs } from "../lib/stageLife";
import {
  COMPOSE_RECT,
  askedReceipt,
  clipAbove,
  dimmed,
  foldFits,
  laneContent,
  laneFor,
  mindRect,
  receiptLeft,
  rectStyle,
  speechRect,
  speechView,
  stageStatusLine,
  stageStatusTone,
  statusSideFor,
  whyReceipt,
  type StageGrid,
  type StageMe,
  type StageMode,
  type StageRect,
  type StageScene,
  type StageView,
} from "../lib/stageModel";
import { justThoughtOf, mindKey, mindOverline, mindPool, thoughtView, type Rand, type RecentThought, type ScreenLive } from "../lib/thinkingModel";
import { SpeechLane } from "./SpeechLane";
import { StageEdward, type Walk } from "./StageEdward";
import { StagePiece } from "./StagePiece";
import { FoldLine, MindLane, ThoughtLane } from "./ThoughtLane";

interface Props {
  view: StageView;
  mode: StageMode;
  presence: ScreenPresence;
  cue: Cue<GlanceTarget> | null;
  beat: { beat: Beat; key: number } | null;
  live: ScreenLive | null;
  /** The owner's switch: his thinking in the lane. */
  showThinking: boolean;
  /** His voice is playing right now. */
  speaking: boolean;
  /** Line ids whose audio really played ("Said aloud" only for these). */
  playedAloud?: ReadonlySet<string>;
  accent: string;
  avatarPx: number;
  tz?: string;
  nowMs: number;
  /** Night's burn-in shift, in px. */
  drift?: { x: number; y: number };
  /** The compose bar is up: the pieces under it fade back and his lane ends above it. */
  composeUp?: boolean;
  /** What the bar and its reply card cover, measured (stageModel.coveredCells); else the bar's band. */
  composeRect?: StageRect | null;
  /** Night hours (or the night stage): a thought on his mind is "Dreaming". */
  dreaming?: boolean;
  /** Idle glances and steps (StageEdward). */
  lifeEnabled?: boolean;
  /** Wandering to another spot now and then. Default: lifeEnabled. */
  wanderEnabled?: boolean;
  /** Injected randomness for his mind's drift and his wandering (tests). */
  rand?: Rand;
}

/** How many cells the why receipt runs from the left edge (≈9 characters a cell at its 12 px, capped at its 62%). */
function receiptCells(text: string | null): number {
  if (!text) return 0;
  return Math.min(15, Math.ceil(text.length * 0.11 + 0.5));
}

function prefersReducedMotion(): boolean {
  try {
    return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/** Under a note: who wrote it and when. */
function signature(scene: StageScene, tz?: string): string | null {
  if (!scene.updated_at || scene.source === "default") return null;
  const when = formatClock(scene.updated_at, { tz });
  return scene.source === "owner" ? `Put up for you · ${when}` : `Written by me · ${when}`;
}

/** While the owner's ask holds, his words stay small beside him: a short box on his lane's side, about his head. */
function compactRect(me: StageRect, side: "left" | "right", stage: StageGrid): StageRect {
  const w = Math.min(9, stage.cols - 2);
  const h = 3;
  const x = side === "right" ? Math.min(stage.cols - 1 - w, me.x + me.w) : Math.max(1, me.x - w);
  const y = Math.max(1, Math.min(stage.rows - 1 - h, me.y + me.h - h - 1));
  return { x, y, w, h };
}

/** Where his words or thoughts leave his head, toward the lane. */
function connectorAt(me: StageRect, side: "left" | "right"): { x: number; y: number } {
  return { x: side === "right" ? me.x + me.w * 0.86 : me.x + me.w * 0.14, y: me.y + me.h * 0.26 };
}

export function Stage({
  view,
  mode,
  presence,
  cue,
  beat,
  live,
  showThinking,
  speaking,
  playedAloud,
  accent,
  avatarPx,
  tz,
  nowMs,
  drift,
  composeUp = false,
  composeRect = null,
  dreaming = false,
  lifeEnabled = true,
  wanderEnabled,
  rand = Math.random,
}: Props) {
  const { stage, scene } = view;
  const { pieces } = scene;
  // The owner's ask on the stage: Edward's words stay small and fade nothing.
  const compact = !!view.asked;
  const wander = wanderEnabled ?? lifeEnabled;

  // Where he stands: the scene's spot, until he wanders; a new layout puts him back. The
  // pieces' places are part of it: place_scene stands him first at the same default spot
  // in most scenes and lays the pieces out around that spot, not where he wandered to.
  const layout = pieces.map((x) => `${x.rect.x},${x.rect.y},${x.rect.w},${x.rect.h}`).join(";");
  const sceneSpot = `${scene.me.at}|${scene.me.rect.x}|${scene.me.rect.y}|${layout}`;
  const [wandered, setWandered] = useState<{ spot: string; x: number; walk: Walk; walking: boolean } | null>(null);
  const here = wandered && wandered.spot === sceneSpot ? wandered : null;
  const placed: StageMe = here ? { ...scene.me, rect: { ...scene.me.rect, x: here.x } } : scene.me;
  // The compose bar (and a reply card on it) covers this; his lane lays out around it, and
  // he steps out of its way — aside along the floor, or up onto the bar (stageLife.clearOfBand).
  const band = composeUp ? composeRect ?? COMPOSE_RECT : null;
  const aside = band ? clearOfBand(placed, band, pieces, stage) : null;
  const me: StageMe = aside ? { ...placed, rect: aside } : placed;
  // Stepping aside and back is one move each way: a walk along a clear floor, else a fade.
  const asideKey = aside ? `${aside.x}|${aside.y}` : "";
  const wanderN = here?.walk.n ?? 0;
  const [moved, setMoved] = useState<{ key: string; rect: StageRect; n: number; wanderN: number; spot: string; walk: Walk | null }>(
    () => ({ key: asideKey, rect: me.rect, n: 0, wanderN, spot: sceneSpot, walk: null }),
  );
  if (moved.key !== asideKey) {
    const from = moved.key ? moved.rect : placed.rect;
    const to = me.rect;
    const kind =
      from.y === to.y && !prefersReducedMotion() ? walkKind({ ...placed, rect: { ...placed.rect, y: to.y } }, from.x, to.x, pieces) : "fade";
    setMoved({ key: asideKey, rect: to, n: moved.n + 1, wanderN, spot: sceneSpot, walk: { kind, ms: kind === "walk" ? walkMs(from.x, to.x) : 0, n: 0 } });
  }
  // Whichever move came last shows (a new layout forgets both); `n` counts both so each arrival replays its fade.
  const lastWalk: Walk | null =
    moved.walk && moved.wanderN === wanderN && moved.spot === sceneSpot ? moved.walk : here?.walk ?? null;
  const walkShown: Walk | null = lastWalk ? { ...lastWalk, n: wanderN + moved.n } : null;
  // While he walks there his mind waits; it re-enters beside him when he arrives.
  const walking = !!here?.walking;
  useEffect(() => {
    if (!here?.walking) return;
    const n = here.walk.n;
    const id = window.setTimeout(() => setWandered((w) => (w && w.walk.n === n ? { ...w, walking: false } : w)), here.walk.ms);
    return () => window.clearTimeout(id);
  }, [here?.walk.n, here?.walking]); // eslint-disable-line react-hooks/exhaustive-deps

  const whole = laneFor(me.rect, pieces, stage);
  const laneRect = band ? clipAbove(whole.rect, band) : whole.rect;
  const lane = { side: whole.side, rect: laneRect, style: rectStyle(laneRect, stage) };
  const speech = speechView(view.lines, nowMs);
  const describe = useToolLabels(!!live?.thinking);
  const thoughts = thoughtView(live, nowMs, { tz, describe });
  const thinkingShown = thoughts.active && showThinking;
  const foldShown = !!thoughts.fold && showThinking && !compact;
  const fold = foldShown && foldFits(lane.rect, pieces);
  const drifting = useMindDrift(mindPool(live, nowMs), { enabled: showThinking && !compact, rand });
  // A mind turn that just folded surfaces large for its moment ("Just thought"), then his mind drifts on.
  const justThought: RecentThought | null = foldShown ? justThoughtOf(thoughts.fold, nowMs) : null;
  const onMind = justThought ?? drifting;
  const content = laneContent({ current: speech.current, thinking: thinkingShown, fold, mind: !!onMind && !walking, nowMs });

  let dimRect: StageRect | null = null;
  let speechBox: CSSProperties | null = null;
  let mindBox: StageRect | null = null;
  const small = compact ? rectStyle(compactRect(me.rect, lane.side, stage), stage) : null;
  if (compact) {
    speechBox = small;
  } else if (content === "speech" && speech.current) {
    const r = speechRect(lane.rect, me.rect, speech.current.text.length, speech.previous?.text.length ?? 0);
    dimRect = r;
    speechBox = rectStyle({ x: lane.rect.x, y: lane.rect.y, w: lane.rect.w, h: r.y + r.h - lane.rect.y }, stage);
  } else if (content === "thinking") {
    dimRect = lane.rect;
  } else if (content === "mind" && onMind) {
    // Around the bar and its card too: the thought finds room above or beside them, never under.
    mindBox = mindRect(whole.rect, me.rect, band ? [...pieces, { rect: band }] : pieces, onMind.text.length);
    dimRect = mindBox;
  }

  const statusLine = stageStatusLine(presence, { thinking: thoughts.active, speaking });
  const statusTone = stageStatusTone(presence, { thinking: thoughts.active, speaking });
  const busy = content === "speech" || content === "thinking";
  // While his thinking fills the lane, the lane is the receipt.
  const receipt = view.asked
    ? askedReceipt(view.asked, { tz })
    : content === "thinking"
      ? null
      : whyReceipt(scene, { tz });
  const signed = signature(scene, tz);

  // Now and then he wanders somewhere else on the floor (never while he thinks or speaks).
  // While the bar is up it is in the way too.
  const obstacles = band ? [...pieces, { rect: band }] : pieces;
  const wanderNow = { me: placed, pieces: obstacles, stage, busy, cells: receiptCells(whyReceipt(scene, { tz })), spot: sceneSpot, rand };
  const wanderRef = useRef(wanderNow);
  wanderRef.current = wanderNow;
  useEffect(() => {
    if (!wander || scene.me.at !== "floor") return;
    let timer = 0;
    const tick = () => {
      const w = wanderRef.current;
      if (!w.busy && (typeof document === "undefined" || document.visibilityState !== "hidden")) {
        const from = w.me.rect.x;
        const to = pickWanderSpot({ me: w.me, x: from, pieces: w.pieces, stage: w.stage, receiptCells: w.cells }, w.rand);
        if (to !== null) {
          const kind = prefersReducedMotion() ? "fade" : walkKind(w.me, from, to, w.pieces);
          const ms = kind === "walk" ? walkMs(from, to) : 0;
          setWandered((prev) => ({ spot: w.spot, x: to, walk: { kind, ms, n: (prev?.walk.n ?? 0) + 1 }, walking: true }));
        }
      }
      timer = window.setTimeout(tick, nextWanderDelayMs(wanderRef.current.rand));
    };
    timer = window.setTimeout(tick, nextWanderDelayMs(wanderRef.current.rand));
    return () => window.clearTimeout(timer);
  }, [wander, scene.me.at, sceneSpot]);

  // Thought bubbles rise once from his head: a thought surfacing on his mind, or a live turn starting.
  const mindShownKey = content === "mind" && onMind ? `${justThought ? "just|" : ""}${mindKey(onMind)}` : null;
  const turnShown = thinkingShown ? live?.thinking?.turn ?? null : null;
  const [bubbles, setBubbles] = useState(0);
  useEffect(() => {
    if (mindShownKey) setBubbles((n) => n + 1);
  }, [mindShownKey]);
  useEffect(() => {
    if (turnShown) setBubbles((n) => n + 1);
  }, [turnShown]);

  const rootStyle: CSSProperties | undefined =
    mode === "night" && drift ? { transform: `translate(${drift.x}px, ${drift.y}px)` } : undefined;
  const head = connectorAt(me.rect, lane.side);
  const headStyle = rectStyle({ x: head.x, y: head.y, w: 0, h: 0 }, stage);
  const jointStyle = busy && !compact ? headStyle : null;
  const over = justThought
    ? { label: "Just thought", time: formatClock(justThought.at, { tz }) }
    : drifting
      ? mindOverline(drifting, { tz, dreaming })
      : null;

  return (
    <div
      className="st-stage"
      data-stage
      data-mode={mode}
      data-compact={compact ? "true" : undefined}
      data-lane-side={lane.side}
      style={rootStyle}
    >
      {pieces.map((piece) => (
        <StagePiece
          key={`${piece.id}:${piece.kind}`}
          piece={piece}
          stage={stage}
          dimmed={(!compact && dimmed(piece.rect, dimRect)) || (composeUp && dimmed(piece.rect, COMPOSE_RECT))}
          nowMs={nowMs}
          tz={tz}
          signed={signed}
        />
      ))}

      {jointStyle && (
        <svg
          className="st-joint"
          data-kind={content ?? undefined}
          data-side={lane.side}
          style={{ left: jointStyle.left, top: jointStyle.top }}
          viewBox="0 0 100 100"
          aria-hidden
        >
          {content === "thinking" ? (
            <>
              <circle cx="10" cy="88" r="4" />
              <circle cx="36" cy="62" r="7" />
              <circle cx="72" cy="26" r="11" />
            </>
          ) : (
            <>
              <path d="M 6 92 C 34 80, 44 34, 94 16" />
              <circle className="st-joint-dot" cx="6" cy="92" r="3" />
            </>
          )}
        </svg>
      )}

      {bubbles > 0 && !compact && (
        <svg
          key={bubbles}
          className="st-bubbles"
          data-n={bubbles}
          data-side={lane.side}
          style={{ left: headStyle.left, top: headStyle.top }}
          viewBox="0 0 100 100"
          aria-hidden
        >
          <circle className="st-bubble-1" cx="10" cy="88" r="4" />
          <circle className="st-bubble-2" cx="36" cy="62" r="7" />
          <circle className="st-bubble-3" cx="72" cy="26" r="11" />
        </svg>
      )}

      {content === "speech" && speechBox && (
        <SpeechLane
          speech={speech}
          playedAloud={!!speech.current?.aloud && !!playedAloud?.has(speech.current.id)}
          tz={tz}
          side={lane.side}
          style={speechBox}
        />
      )}
      {content === "thinking" && <ThoughtLane view={thoughts} side={lane.side} style={small ?? lane.style} />}
      {content === "fold" && thoughts.fold && (
        <FoldLine text={thoughts.fold.line} style={rectStyle({ ...lane.rect, y: lane.rect.y + lane.rect.h - 1, h: 1 }, stage)} />
      )}
      {content === "mind" && onMind && over && mindBox && (
        <MindLane
          item={{ key: mindShownKey ?? mindKey(onMind), label: over.label, time: over.time, text: onMind.text }}
          side={lane.side}
          style={rectStyle(mindBox, stage)}
        />
      )}

      <StageEdward
        me={me}
        stage={stage}
        pieces={pieces}
        presence={presence}
        statusLine={statusLine}
        statusTone={statusTone}
        mode={mode}
        laneSide={busy ? lane.side : null}
        busy={busy}
        accent={accent}
        avatarPx={avatarPx}
        cue={cue}
        beat={beat}
        lifeEnabled={lifeEnabled}
        sceneRead={!view.stock}
        statusSide={statusSideFor(me.rect, lane.side, stage)}
        walk={walkShown}
        glanceUp={bubbles}
      />

      {receipt && (
        <div className="st-receipt-why" style={{ left: rectStyle({ x: receiptLeft(me), y: 0, w: 0, h: 0 }, stage).left }}>
          <span className="st-receipt-dot" aria-hidden />
          <span>{receipt}</span>
        </div>
      )}
    </div>
  );
}
