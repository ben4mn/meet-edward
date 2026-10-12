"use client";

// Edward's life: blinks, idle glances and one-shot beats.
//
// Everything here is a DOM attribute on the avatar root — data-blink,
// data-glance, data-beat — never React state, so a blink costs zero
// renders. The CSS state machine in app/globals.css (the Pane section)
// turns the attributes into motion. Scheduling decisions are pure and
// tested in lib/edwardMood.ts; this hook only owns timers and the DOM.
//
//   * Blinks at random (2.2–6s, doubles, long stares); glances left/right/
//     down every 8–20s while idle — unless an explicit data-gaze is set.
//   * Everything pauses while the tab is hidden (no work nobody sees), and
//     blinks/glances never run under prefers-reduced-motion.
//   * fire(beat) plays a beat now, or holds it behind the ≥1.2s throttle;
//     of two held beats the more important one plays (acted > wince > …).
//     A beat clears on its own animationend (`ed-beat-<beat>`) or, as a
//     backstop that also covers reduced motion, after BEAT_MS.
//   * perkOnReturn: the app came back after ≥15s away → perk.
//
// Timers are unref'd where the runtime supports it (Node, i.e. tests), so
// an avatar left mounted can never keep a process alive.

import { useEffect, useRef, type MutableRefObject, type RefObject } from "react";

import {
  BEAT_MS,
  blinkPattern,
  nextBlinkMs,
  nextGlanceMs,
  pickGlance,
  pickPendingBeat,
  throttleBeat,
  type Beat,
  type Gaze,
  type Mood,
} from "./edwardMood";

export interface EdwardLifeOptions {
  /** false = no blinks, glances or beats at all (a still avatar). Default true. */
  enabled?: boolean;
  /** Current mood: glances only while idle/sleepy, slow blinks when sleepy or
   *  resting, none offline. Read on each tick, so changing it never reschedules. */
  mood?: Mood;
  /** Idle glances allowed. Default true. */
  glances?: boolean;
  /** Perk when the app returns to the foreground after a while away. */
  perkOnReturn?: boolean;
  /** Randomness source (tests). */
  rand?: () => number;
}

export interface EdwardLife {
  /** Play a beat. true = playing now; false = held behind the throttle, or disabled. */
  fire(beat: Beat): boolean;
  /** Look somewhere for a moment (e.g. down at new content). */
  glance(gaze: Gaze, holdMs?: number): void;
  /** One blink now. */
  blink(): void;
}

/** Away at least this long before coming back earns a perk. */
export const PERK_AFTER_HIDDEN_MS = 15_000;

const GLANCE_MOODS: ReadonlySet<Mood> = new Set<Mood>(["idle", "sleepy"]);
const OFFLINE_RECHECK_MS = 5_000;

type Timer = ReturnType<typeof setTimeout>;

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

function isHidden(): boolean {
  return typeof document !== "undefined" && document.hidden === true;
}

/** The timers and DOM writes behind one avatar. Created once per mount. */
function createMachine(
  ref: RefObject<HTMLElement | null>,
  optsRef: MutableRefObject<EdwardLifeOptions>,
) {
  const timers = new Set<Timer>();
  let beatClear: Timer | null = null;
  let pendingTimer: Timer | null = null;
  let pending: Beat | null = null;
  let lastBeatAt: number | null = null;
  let currentBeat: Beat | null = null;

  const on = () => optsRef.current.enabled !== false;
  const rand = () => (optsRef.current.rand ?? Math.random)();

  const later = (fn: () => void, ms: number): Timer => {
    const t = setTimeout(() => {
      timers.delete(t);
      fn();
    }, ms);
    (t as { unref?: () => void }).unref?.();
    timers.add(t);
    return t;
  };
  const cancel = (t: Timer | null) => {
    if (t === null) return;
    clearTimeout(t);
    timers.delete(t);
  };

  const clearBeat = () => {
    cancel(beatClear);
    beatClear = null;
    currentBeat = null;
    ref.current?.removeAttribute("data-beat");
  };

  const play = (beat: Beat) => {
    const el = ref.current;
    if (!el) return;
    cancel(beatClear);
    // Restart the animation even if the same beat is showing: drop the
    // attribute, force a style flush, set it again.
    el.removeAttribute("data-beat");
    void el.getBoundingClientRect();
    el.setAttribute("data-beat", beat);
    currentBeat = beat;
    lastBeatAt = Date.now();
    beatClear = later(clearBeat, BEAT_MS[beat] + 80);
  };

  const runBlink = (pattern: number[]) => {
    const el = ref.current;
    if (!el) return;
    let at = 0;
    pattern.forEach((ms, i) => {
      const closing = i % 2 === 0;
      later(() => {
        if (closing) el.setAttribute("data-blink", "");
        else el.removeAttribute("data-blink");
      }, at);
      at += ms;
    });
    later(() => el.removeAttribute("data-blink"), at);
  };

  const handle: EdwardLife = {
    fire(beat) {
      if (!on() || !ref.current) return false;
      const gate = throttleBeat(lastBeatAt, Date.now());
      if (gate.ok && pending === null) {
        play(beat);
        return true;
      }
      pending = pickPendingBeat(pending, beat);
      if (pendingTimer === null) {
        pendingTimer = later(() => {
          pendingTimer = null;
          const next = pending;
          pending = null;
          if (next) play(next);
        }, gate.waitMs);
      }
      return false;
    },
    glance(gaze, holdMs = 1200) {
      const el = ref.current;
      if (!el || !on()) return;
      el.setAttribute("data-glance", gaze);
      later(() => {
        if (el.getAttribute("data-glance") === gaze) el.removeAttribute("data-glance");
      }, holdMs);
    },
    blink() {
      if (!on()) return;
      runBlink(blinkPattern(rand, optsRef.current.mood));
    },
  };

  /** Start blinks, glances and the visibility/motion listeners on `el`. */
  const start = (el: HTMLElement): (() => void) => {
    let blinkTimer: Timer | null = null;
    let glanceTimer: Timer | null = null;
    let hiddenAt: number | null = isHidden() ? Date.now() : null;
    let reduced = prefersReducedMotion();

    const scheduleBlink = () => {
      cancel(blinkTimer);
      const wait = nextBlinkMs(rand, optsRef.current.mood);
      blinkTimer = later(() => {
        blinkTimer = null;
        if (wait !== null) runBlink(blinkPattern(rand, optsRef.current.mood));
        scheduleBlink();
      }, wait ?? OFFLINE_RECHECK_MS);
    };

    const scheduleGlance = () => {
      cancel(glanceTimer);
      glanceTimer = later(() => {
        glanceTimer = null;
        const o = optsRef.current;
        if (
          o.glances !== false &&
          GLANCE_MOODS.has(o.mood ?? "idle") &&
          !el.hasAttribute("data-gaze") &&
          !el.hasAttribute("data-beat")
        ) {
          const g = pickGlance(rand);
          handle.glance(g.gaze, g.holdMs);
        }
        scheduleGlance();
      }, nextGlanceMs(rand));
    };

    const stopAmbient = () => {
      cancel(blinkTimer);
      cancel(glanceTimer);
      blinkTimer = null;
      glanceTimer = null;
      el.removeAttribute("data-blink");
      el.removeAttribute("data-glance");
    };

    const startAmbient = () => {
      stopAmbient();
      if (reduced || isHidden()) return;
      scheduleBlink();
      scheduleGlance();
    };

    const onVisibility = () => {
      if (isHidden()) {
        hiddenAt = Date.now();
        stopAmbient();
        return;
      }
      const away = hiddenAt === null ? 0 : Date.now() - hiddenAt;
      hiddenAt = null;
      startAmbient();
      if (optsRef.current.perkOnReturn && away >= PERK_AFTER_HIDDEN_MS) handle.fire("perk");
    };

    const onAnimationEnd = (e: Event) => {
      const name = (e as AnimationEvent).animationName;
      if (currentBeat && name === `ed-beat-${currentBeat}`) clearBeat();
    };

    let mql: MediaQueryList | null = null;
    const onMotionPref = () => {
      reduced = prefersReducedMotion();
      startAmbient();
    };
    try {
      if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
        mql = window.matchMedia("(prefers-reduced-motion: reduce)");
        mql.addEventListener?.("change", onMotionPref);
      }
    } catch {
      mql = null;
    }

    document.addEventListener("visibilitychange", onVisibility);
    el.addEventListener("animationend", onAnimationEnd);
    startAmbient();

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      el.removeEventListener("animationend", onAnimationEnd);
      mql?.removeEventListener?.("change", onMotionPref);
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
      beatClear = null;
      pendingTimer = null;
      pending = null;
      currentBeat = null;
      el.removeAttribute("data-blink");
      el.removeAttribute("data-glance");
      el.removeAttribute("data-beat");
    };
  };

  return { handle, start };
}

export function useEdwardLife(
  ref: RefObject<HTMLElement | null>,
  opts: EdwardLifeOptions = {},
): EdwardLife {
  const optsRef = useRef(opts);
  optsRef.current = opts;
  const machine = useRef<ReturnType<typeof createMachine> | null>(null);
  if (machine.current === null) machine.current = createMachine(ref, optsRef);

  const enabled = opts.enabled !== false;
  useEffect(() => {
    const el = ref.current;
    if (!enabled || !el) return;
    return machine.current!.start(el);
  }, [enabled, ref]);

  return machine.current.handle;
}
