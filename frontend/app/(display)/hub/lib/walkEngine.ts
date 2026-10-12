// Walk behaviour for the screensaver companion.
//
// Ported from ben4mn/companion (docs/app.js `walk()`), which drives the
// same PaneSvg rigging this repo already uses — robot-leg-left/right,
// robot-arm-l/r, robot-shadow. Kept pure so pacing and bounds are testable
// without a DOM: the React layer owns only rAF and class toggling.

/** Below this, he'd shuffle on the spot rather than take a walk. */
export const MIN_WALK_DISTANCE = 8;

/** Milliseconds per pixel — constant pace, so distance sets duration.
 *  16 ms/px ≈ 62 px/s at 96 px: his legs swing a foot about 30 px/s, and at
 *  11 ms/px (91 px/s, twice that at the old ease's peak) he skated. */
export const WALK_MS_PER_PX = 16;

/** A short hop still gets a real walk cycle rather than a twitch. */
export const MIN_WALK_MS = 600;

/**
 * Somewhere new to walk to, or null if it isn't worth it — either the
 * frame is too narrow or he's already about there.
 */
export function pickTarget(
  x: number,
  maxX: number,
  rand: () => number = Math.random,
  minX = 0,
): number | null {
  // minX keeps him off the clock. The clock sits bottom-left and he walks
  // the bottom edge, so an exclusion zone is the only thing that works —
  // he'd otherwise stroll straight through it in transit even if he never
  // stopped there.
  const span = maxX - minX;
  if (span < 20) return null;
  const target = Math.round(minX + rand() * span);
  if (Math.abs(target - x) < MIN_WALK_DISTANCE) return null;
  return target;
}

export function walkDurationMs(distance: number): number {
  return Math.max(MIN_WALK_MS, Math.abs(distance) * WALK_MS_PER_PX);
}

/** Ease in, ease out (quadratic). Kept for reference; the walk uses easeWalk. */
export function easeInOutQuad(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}

/** Fraction of the walk spent speeding up, and again slowing down. */
export const WALK_RAMP = 0.15;

/**
 * A steady walk with short ramps: he speeds up over the first 15% of the
 * walk, keeps one pace, and slows over the last 15%. The quadratic ease
 * peaked at twice his average speed mid-walk, which is where his feet
 * skated worst; this peaks at 1/(1 − ramp) ≈ 1.18×.
 */
export function easeWalk(t: number, ramp: number = WALK_RAMP): number {
  const x = Math.min(1, Math.max(0, t));
  const vmax = 1 / (1 - ramp);
  const accel = vmax / ramp;
  if (x < ramp) return 0.5 * accel * x * x;
  if (x > 1 - ramp) return 1 - 0.5 * accel * (1 - x) * (1 - x);
  return 0.5 * vmax * ramp + vmax * (x - ramp);
}

/**
 * Where he is at `progress` (0..1) between two points. Progress is clamped
 * so a delayed frame can't put him past the target and snap back.
 */
export function positionAt(from: number, to: number, progress: number): number {
  const t = Math.min(1, Math.max(0, progress));
  return from + (to - from) * easeWalk(t);
}

// ---------------------------------------------------------------------------
// Moving without React
// ---------------------------------------------------------------------------
//
// The walk runs on requestAnimationFrame. Setting React state every frame
// re-rendered the whole companion ~60 times a second on a Raspberry Pi; the
// component now writes `trackTransform(x)` to its track element directly and
// commits x to state only on arrival. Under prefers-reduced-motion he never
// glides across the frame: he fades out, moves, and fades back in.

/** Reduced motion: an opacity fade, never longer than the design's 120ms. */
export const FADE_MOVE_MS = 120;

export type MovePlan =
  | { kind: "walk"; durationMs: number }
  | { kind: "fade"; fadeMs: number };

export function planMove(distance: number, reducedMotion: boolean): MovePlan {
  return reducedMotion
    ? { kind: "fade", fadeMs: FADE_MOVE_MS }
    : { kind: "walk", durationMs: walkDurationMs(distance) };
}

/** His horizontal position as a compositor-only transform. */
export function trackTransform(x: number): string {
  return `translate3d(${Math.round(x * 100) / 100}px, 0, 0)`;
}

// ---------------------------------------------------------------------------
// Standing on the floor
// ---------------------------------------------------------------------------
//
// PaneSvg draws into viewBox "0 0 36 38" inside a *square* box, so the
// default `xMidYMid meet` scales the artwork by height: one viewBox unit
// is size/38 tall. His foot pads bottom out at y=34, which leaves the last
// four units of the box as empty air.
//
// Positioning that box against the floor therefore parks his feet ~10% of
// his own height above it — with a drop-shadow underneath, that doesn't
// read as "standing over there", it reads as "hovering". These two
// constants come straight off the rig so a change to PaneSvg's geometry
// shows up here as a failing test rather than as a floating robot.

/** Height of PaneSvg's viewBox. */
export const PANE_VIEWBOX_HEIGHT = 38;

/** Where the bottom of his foot pads sits in that viewBox. */
export const PANE_FOOT_Y = 34;

/** Empty space between his soles and the bottom of a `size`-px avatar box. */
export function feetGapPx(size: number): number {
  return (size * (PANE_VIEWBOX_HEIGHT - PANE_FOOT_Y)) / PANE_VIEWBOX_HEIGHT;
}

/**
 * CSS `bottom` for the avatar box that puts his soles on `floorPx` above
 * the frame's bottom edge. Negative by design: the box hangs off the
 * bottom of the screen so that *he* doesn't.
 */
export function groundedBottomPx(size: number, floorPx = 0): number {
  return floorPx - feetGapPx(size);
}
