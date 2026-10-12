"use client";

import React, { useEffect, useRef, useState } from "react";

import { PaneSvg } from "@/components/pane/PaneSvg";
import type { PaneActivityClass } from "@/components/pane/paneActivity";

import {
  groundedBottomPx,
  pickTarget,
  planMove,
  positionAt,
  trackTransform,
} from "../lib/walkEngine";

// Edward pottering about along the bottom of the screensaver.
//
// Behaviour ported from ben4mn/companion (docs/app.js): pause, then either
// wander somewhere new or play an idle. The walk has three beats —
// anticipate (a small crouch), the walk cycle, then a settle on arrival —
// which is what stops it reading as a sprite sliding on ice.
//
// The pure pacing/bounds live in ../lib/walkEngine.ts; this component owns
// only rAF and class toggling. Two elements (2026-09-26):
//   * the track (.hub-companion) is positioned by a translate3d. While he
//     walks, rAF writes that transform straight to the DOM; x is committed
//     to React state only on arrival — no render per frame on the Pi;
//   * the body (.hub-companion-body) carries the shove choreography
//     (data-pushing), whose keyframed transform would otherwise fight the
//     track's translate.
// Under prefers-reduced-motion he never glides: he fades out (≤120ms),
// moves, and fades back in. When a slide changes mid-walk he stops, shoves
// it, and walks on from where he stood.

const SIZE = 96;

// Where his soles go, measured from the bottom of the screen. Six pixels
// rather than zero so the contact shadow he casts (which reaches ~6px
// below his feet at this size) isn't sliced off by the screen edge —
// clipping it is what makes a grounded character look cut out and pasted.
const FLOOR_PX = 6;

// The avatar box is taller than Edward is (see groundedBottomPx), so it
// hangs off the bottom edge to put *him* on the floor rather than the box.
const BOX_BOTTOM_PX = groundedBottomPx(SIZE, FLOOR_PX);

// Breathing room between the clock's right edge and Edward's left edge.
const CLOCK_CLEARANCE = 28;

const START_X = 320;

/**
 * Left edge of the floor Edward is allowed to use.
 *
 * Measured from the live clock rather than hard-coded: the clock's width
 * changes with the time ("9:41" vs "12:34") and with the date string
 * underneath it, so a fixed number would be wrong twice a day.
 */
function floorLeftBound(): number {
  if (typeof document === "undefined") return 0;
  const clock = document.querySelector(".hub-screensaver-clock");
  if (!clock) return 0;
  const r = clock.getBoundingClientRect();
  if (r.width === 0) return 0;
  return Math.round(r.right + CLOCK_CLEARANCE);
}

function prefersReducedMotion(): boolean {
  try {
    return (
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  } catch {
    return false;
  }
}

// Only the idles this repo's globals.css actually defines. The companion
// app has more (wave, dance, stretch); borrowing those class names without
// the keyframes would just freeze him mid-pose.
//
// Never "act-think": the rig reads it as the real thinking mood (visor
// scan, antenna pulse) — the look /screen and the hub use for a running
// turn. Idle play is motion, never a claim (design system §1.2).
const IDLES: PaneActivityClass[] = ["act-stand", "act-look"];

type Phase = "idle" | "anticipate" | "walking" | "arrive";

function rand(min: number, max: number) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

export type PushDirection = "left" | "right";

/**
 * Which way he shoves a photo (L7, 2026-09-27 follow-ups): in slide mode
 * the photo leaves to the left (hub.css .hub-slide[data-exiting] →
 * translateX(-100%)), and he used to face right and shove right — pushing
 * it the wrong way. Crossfade and Ken Burns move nothing sideways, so he
 * keeps the right-hand shove there.
 */
export function pushDirection(transition: string | null | undefined): PushDirection {
  return transition === "slide" ? "left" : "right";
}

export function PhotoCompanion({
  presenting = false,
  pushToward = "right",
}: {
  presenting?: boolean;
  /** The way the photo leaves while he presents it (pushDirection). */
  pushToward?: PushDirection;
}) {
  // x is where he *is* between walks; mid-walk the DOM leads it.
  const [x, setX] = useState(START_X);
  const [facingLeft, setFacingLeft] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [activity, setActivity] = useState<PaneActivityClass>("act-stand");
  const [hidden, setHidden] = useState(false);

  const trackRef = useRef<HTMLDivElement>(null);
  const xRef = useRef(x);
  xRef.current = x;
  // Read by the walk loop: while he shoves a photo he stands still.
  const presentingRef = useRef(presenting);
  presentingRef.current = presenting;
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const raf = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    const later = (fn: () => void, ms: number) => {
      const id = setTimeout(() => {
        if (!cancelled) fn();
      }, ms);
      timers.current.push(id);
    };

    const maxX = () => Math.max(0, window.innerWidth - SIZE);

    // Re-measured every decision so a clock that changes width (or a
    // resized window) is picked up without a reload.
    const minX = () => Math.min(floorLeftBound(), maxX());

    const scheduleNext = () => later(decide, rand(2600, 5200));

    const decide = () => {
      // Mid-shove is no time to wander off.
      if (presentingRef.current) {
        scheduleNext();
        return;
      }
      if (Math.random() < 0.6) startWalk();
      else playIdle();
    };

    const playIdle = () => {
      setPhase("idle");
      setActivity(IDLES[Math.floor(Math.random() * IDLES.length)]);
      later(() => {
        setActivity("act-stand");
        scheduleNext();
      }, rand(2500, 4200));
    };

    const arrive = (target: number) => {
      xRef.current = target;
      setX(target);
    };

    const startWalk = () => {
      const from = xRef.current;
      const left = minX();
      const target = pickTarget(from, maxX(), Math.random, left);
      if (target === null) {
        // Nowhere worth going — but if he's somehow standing on the clock
        // (first paint, or the window shrank), step clear before idling.
        if (from < left) arrive(left);
        scheduleNext();
        return;
      }

      const plan = planMove(target - from, prefersReducedMotion());

      if (plan.kind === "fade") {
        // Reduced motion: out, across, back in. No glide, no walk cycle.
        setHidden(true);
        later(() => {
          setFacingLeft(target < from);
          arrive(target);
          setHidden(false);
          scheduleNext();
        }, plan.fadeMs);
        return;
      }

      setFacingLeft(target < from);
      setActivity("act-stand");
      setPhase("anticipate");

      later(() => {
        setPhase("walking");
        const t0 = performance.now();
        // Time spent standing still to shove a photo doesn't count as walking.
        let paused = 0;
        let lastFrame = t0;

        const tick = (now: number) => {
          if (cancelled) return;
          if (presentingRef.current) {
            paused += now - lastFrame;
            lastFrame = now;
            raf.current = requestAnimationFrame(tick);
            return;
          }
          lastFrame = now;
          const progress = (now - t0 - paused) / plan.durationMs;
          if (progress < 1) {
            // The one write per frame: straight to the compositor.
            const el = trackRef.current;
            if (el) el.style.transform = trackTransform(positionAt(from, target, progress));
            raf.current = requestAnimationFrame(tick);
          } else {
            raf.current = null;
            arrive(target);
            setPhase("arrive");
            later(() => {
              setPhase("idle");
              scheduleNext();
            }, 280);
          }
        };
        raf.current = requestAnimationFrame(tick);
      }, 180);
    };

    const keepClear = () => {
      // Never yank him mid-walk; the next decision re-measures anyway.
      if (raf.current !== null) return;
      const left = minX();
      if (xRef.current < left) arrive(left);
    };
    keepClear();
    window.addEventListener("resize", keepClear);

    scheduleNext();

    return () => {
      window.removeEventListener("resize", keepClear);
      cancelled = true;
      timers.current.forEach(clearTimeout);
      timers.current = [];
      if (raf.current !== null) cancelAnimationFrame(raf.current);
    };
  }, []);

  const phaseClass = presenting
    ? "hub-pushing"
    : phase === "walking"
      ? "walking"
      : phase === "anticipate"
        ? "walk-anticipate"
        : phase === "arrive"
          ? "walk-arrive"
          : "";

  return (
    <div
      ref={trackRef}
      className="hub-companion"
      style={{
        left: 0,
        bottom: `${BOX_BOTTOM_PX}px`,
        width: SIZE,
        height: SIZE,
        transform: trackTransform(x),
        opacity: hidden ? 0 : 1,
      }}
      aria-hidden="true"
    >
      <div className="hub-companion-body" data-pushing={presenting || undefined} data-push={pushToward}>
        <PaneSvg
          size={SIZE}
          activity={presenting ? "act-stand" : activity}
          facingLeft={presenting ? pushToward === "left" : facingLeft}
          className={`pane-animated ${phaseClass}`}
        />
      </div>
    </div>
  );
}
