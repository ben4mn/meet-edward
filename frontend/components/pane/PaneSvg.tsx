"use client";

// Edward — the one character every surface draws (chat, Today, /screen, the
// hub). The robot's silhouette comes from PaneStreet's footer mascot
// (github.com/ben4mn/PaneStreet); the palette is the app icon's: cream
// shell, dark visor, eyes and lights in his accent.
//
// This component only draws and labels him. Behaviour is split three ways:
//   * lib/edwardMood.ts    — pure: which mood/beat a signal means, status lines
//   * lib/useEdwardLife.ts — DOM attributes for blinks, glances and beats
//   * app/globals.css      — the Pane section: the CSS state machine that
//                            turns data-mood / data-beat / data-gaze /
//                            data-blink / .is-live into motion
//
// Rig contract (tests pin it; hub/lib/walkEngine.ts depends on it):
//   * viewBox "0 0 36 38", foot pads bottom out at y=34;
//   * the antenna grows from "M18 6.5 Q18 3 17 1.5";
//   * every pivot is written in viewBox px in globals.css — so the bust
//     variant crops with an outer viewBox and keeps the rig on its own
//     nested 0 0 36 38 canvas, and pivots mean the same thing in both;
//   * the classes hub.css and display.css drive (robot-body, robot-antenna,
//     robot-arm-l/r, robot-leg-left/right, robot-shadow, robot-eye-l/r,
//     pane-clickable, face-left) stay on the same parts.

import React, {
  useEffect,
  useId,
  useRef,
  type CSSProperties,
  type MutableRefObject,
} from "react";

import {
  moodFromActivity,
  moodToActivity,
  type Beat,
  type Gaze,
  type Mood,
} from "@/lib/edwardMood";
import { useEdwardLife, type EdwardLife } from "@/lib/useEdwardLife";
import type { PaneActivityClass } from "./paneActivity";

export type PaneVariant = "full" | "bust";

export interface PaneSvgProps {
  /** Box size in px (square). */
  size: number;
  /** What he is doing. Wins over `activity`. Default: from `activity`, else idle. */
  mood?: Mood;
  /** Legacy activity class (/screen, hub). Read as a mood when `mood` is absent. */
  activity?: PaneActivityClass;
  /** A one-shot reaction. Plays when it (or `beatKey`) changes. */
  beat?: Beat | null;
  /** Change this to replay the same beat. */
  beatKey?: string | number;
  /** Where he looks. Suppresses idle glances while set. */
  gaze?: Gaze | null;
  /** "full" (default) or "bust" — head and shoulders for 16–36px. */
  variant?: PaneVariant;
  /** The one Edward per screen who breathes and runs mood loops. The hub's
   *  legacy `className="pane-animated"` opt-in also means live. */
  live?: boolean;
  /** false = fully still (no blinks, glances, beats, transitions). Default true. */
  animated?: boolean;
  /** Perk on his own when the app comes back after a while away. Default:
   *  when live. Off where a presence provider already sends the perk as a
   *  beat (two perks 1.2 s apart otherwise). */
  perkOnReturn?: boolean;
  /** Idle glances allowed (default true). */
  glances?: boolean;
  /** The mouth follows `--mouth` (0..1, e.g. TTS level) instead of the talk loop. */
  lipsync?: boolean;
  /** Mirror him (the hub companion walking left). */
  facingLeft?: boolean;
  /** His hands in the owner's Chrome are down: `data-hands="down"` turns the
   *  antenna tip amber whatever the mood (offline keeps its grey). */
  handsDown?: boolean;
  className?: string;
  /** Accent for eyes and lights (defaults to Edward's emerald). */
  accent?: string;
  onClick?: () => void;
  /** Accessible name. Without one (and without onClick) he is decorative. */
  ariaLabel?: string;
  /** Tooltip; also the accessible name when `ariaLabel` is absent. */
  title?: string;
  /** Receives the life handle (fire / glance / blink) for imperative beats. */
  lifeRef?: MutableRefObject<EdwardLife | null>;
  /** Extra inline style, e.g. `{"--level": 0.4}` for the listening ring. */
  style?: CSSProperties;
}

/** The bust crop: head, antenna and shoulders, square. */
export const BUST_VIEWBOX = "4.5 -0.8 27 27";
export const FULL_VIEWBOX = "0 0 36 38";
/** Everything below this (viewBox y) is cropped from the bust. */
const BUST_BOTTOM_Y = 26.2;

function hasClass(className: string | undefined, name: string): boolean {
  return !!className && className.split(/\s+/).includes(name);
}

export function PaneSvg({
  size,
  mood,
  activity,
  beat = null,
  beatKey,
  gaze = null,
  variant = "full",
  live,
  animated = true,
  perkOnReturn,
  glances = true,
  lipsync = false,
  facingLeft = false,
  handsDown = false,
  className,
  accent,
  onClick,
  ariaLabel,
  title,
  lifeRef,
  style: extraStyle,
}: PaneSvgProps) {
  const rootRef = useRef<HTMLSpanElement>(null);
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const ids = {
    visor: `pane-${uid}-visor`,
    eyeGlow: `pane-${uid}-eyeglow`,
    tipGlow: `pane-${uid}-tipglow`,
    crop: `pane-${uid}-crop`,
    scan: `pane-${uid}-scan`,
  };

  const resolvedMood: Mood = mood ?? moodFromActivity(activity);
  const activityClass: PaneActivityClass = activity ?? moodToActivity(resolvedMood);
  const isLive = live ?? hasClass(className, "pane-animated");
  const bust = variant === "bust";

  const life = useEdwardLife(rootRef, {
    enabled: animated,
    mood: resolvedMood,
    glances: glances && !gaze,
    perkOnReturn: perkOnReturn ?? isLive,
  });
  if (lifeRef) lifeRef.current = life;

  useEffect(() => {
    if (beat) life.fire(beat);
    // beatKey is a deliberate dependency: bump it to replay the same beat.
  }, [beat, beatKey, life]);

  const rootClass = [
    "pane-avatar",
    activityClass,
    isLive ? "is-live" : "",
    animated ? "" : "pane-static",
    facingLeft ? "face-left" : "",
    onClick ? "pane-clickable" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");

  const style: CSSProperties = {
    width: size,
    height: size,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    cursor: onClick ? "pointer" : undefined,
    ...(accent ? ({ ["--pane-accent" as string]: accent } as CSSProperties) : {}),
    ...extraStyle,
  };

  const label = ariaLabel ?? title ?? (onClick ? "Edward" : undefined);
  const a11y = onClick
    ? { role: "button", tabIndex: 0, "aria-label": label }
    : label
      ? { role: "img", "aria-label": label }
      : { "aria-hidden": true as const };

  const rig = (
    <>
      <defs>
        <clipPath id={ids.visor}>
          <rect x="9.5" y="8.5" width="17" height="11" rx="5" />
        </clipPath>
        <radialGradient id={ids.eyeGlow}>
          <stop offset="0" style={{ stopColor: "var(--pane-light)", stopOpacity: 0.55 }} />
          <stop offset="0.55" style={{ stopColor: "var(--pane-light)", stopOpacity: 0.16 }} />
          <stop offset="1" style={{ stopColor: "var(--pane-light)", stopOpacity: 0 }} />
        </radialGradient>
        <radialGradient id={ids.tipGlow}>
          <stop offset="0.35" style={{ stopColor: "var(--pane-tip)", stopOpacity: 0.6 }} />
          <stop offset="1" style={{ stopColor: "var(--pane-tip)", stopOpacity: 0 }} />
        </radialGradient>
        <linearGradient id={ids.scan} x1="0" x2="1" y1="0" y2="0">
          <stop offset="0" style={{ stopColor: "var(--pane-light)", stopOpacity: 0 }} />
          <stop offset="0.25" style={{ stopColor: "var(--pane-light)", stopOpacity: 1 }} />
          <stop offset="0.75" style={{ stopColor: "var(--pane-light)", stopOpacity: 1 }} />
          <stop offset="1" style={{ stopColor: "var(--pane-light)", stopOpacity: 0 }} />
        </linearGradient>
        {bust && (
          <clipPath id={ids.crop}>
            <rect x="-20" y="-20" width="76" height={20 + BUST_BOTTOM_Y} />
          </clipPath>
        )}
      </defs>

      {!bust && (
        <ellipse className="robot-shadow" cx="18" cy="35" rx="9" ry="1.3" fill="var(--pane-floor)" />
      )}

      <g className="robot-body" clipPath={bust ? `url(#${ids.crop})` : undefined}>
        {/* Legs first: planted, and tall enough to stay tucked under the
            torso while the upper body breathes. */}
        {!bust && (
          <>
            <g className="robot-leg-left">
              <rect x="13.5" y="27.5" width="3.5" height="5" rx="1.2" fill="var(--pane-shade)" />
              <rect x="12.5" y="32" width="5.5" height="2" rx="1" fill="var(--pane-foot)" />
            </g>
            <g className="robot-leg-right">
              <rect x="19" y="27.5" width="3.5" height="5" rx="1.2" fill="var(--pane-shade)" />
              <rect x="18" y="32" width="5.5" height="2" rx="1" fill="var(--pane-foot)" />
            </g>
          </>
        )}

        {/* Everything that breathes. */}
        <g className="robot-upper">
          {/* Torso with a chest light. */}
          <rect x="12" y="22" width="12" height="7" rx="3" fill="var(--pane-shade)" stroke="var(--pane-outline)" strokeWidth="0.4" />
          <rect x="12" y="22" width="12" height="6" rx="3" fill="var(--pane-shell)" />
          <g className="robot-core">
            <circle cx="18" cy="25" r="1.8" fill="var(--pane-light)" opacity="0.16" />
            <circle cx="18" cy="25" r="0.9" fill="var(--pane-light)" />
          </g>

          <g className="robot-arm-l">
            <rect x="6.5" y="22.5" width="4.5" height="5" rx="2.2" fill="var(--pane-shade)" stroke="var(--pane-outline)" strokeWidth="0.4" />
            <rect x="6.5" y="22.5" width="4.5" height="4.3" rx="2.1" fill="var(--pane-shell)" />
          </g>

          {/* The neck carries the mood pose (a listening tilt, a sleepy
              droop); the head inside it plays the beats (nod, wince, perk).
              Two groups on one pivot, so a beat never snaps a pose away. */}
          <g className="robot-neck">
            <g className="robot-head">
              {/* The antenna rides the head (drawn first, behind the shell)
                  so a nod or a tilt carries it. Its tip is his trust light. */}
              <g className="robot-antenna">
                <path d="M18 6.5 Q18 3 17 1.5" stroke="var(--pane-outline)" strokeWidth="2" strokeLinecap="round" fill="none" />
                <path d="M18 6.5 Q18 3 17 1.5" stroke="var(--pane-shade)" strokeWidth="1.2" strokeLinecap="round" fill="none" />
                <circle className="robot-antenna-glow" cx="17" cy="1.2" r="3.2" fill={`url(#${ids.tipGlow})`} />
                <circle className="robot-antenna-ring" cx="17" cy="1.2" r="1.6" fill="none" stroke="var(--pane-tip)" strokeWidth="0.45" />
                <circle className="robot-antenna-tip" cx="17" cy="1.2" r="1.6" fill="var(--pane-tip)" stroke="var(--pane-outline)" strokeWidth="0.3" />
                <circle cx="16.45" cy="0.65" r="0.45" fill="#fff" opacity="0.6" />
              </g>

              {/* Ears. */}
              <ellipse cx="7" cy="13.5" rx="2.2" ry="2.5" fill="var(--pane-shade)" stroke="var(--pane-outline)" strokeWidth="0.4" />
              <ellipse cx="29" cy="13.5" rx="2.2" ry="2.5" fill="var(--pane-shade)" stroke="var(--pane-outline)" strokeWidth="0.4" />
              <ellipse cx="6.6" cy="13.5" rx="0.8" ry="1.2" fill="var(--pane-foot)" opacity="0.55" />
              <ellipse cx="29.4" cy="13.5" rx="0.8" ry="1.2" fill="var(--pane-foot)" opacity="0.55" />

              {/* Shell: underside, then the cream top, then a highlight. */}
              <rect x="6.5" y="5.5" width="23" height="16" rx="8" fill="var(--pane-shade)" stroke="var(--pane-outline)" strokeWidth="0.4" />
              <rect x="6.5" y="5.5" width="23" height="15" rx="7.5" fill="var(--pane-shell)" />
              <path d="M12.5 6.7 Q18 5.3 23.5 6.7" stroke="#fff" strokeWidth="0.6" strokeLinecap="round" opacity="0.5" fill="none" />

              {/* The visor: everything inside is clipped to its glass. */}
              <g clipPath={`url(#${ids.visor})`}>
                <rect className="robot-visor" x="9.5" y="8.5" width="17" height="11" rx="5" fill="var(--pane-visor)" />
                <ellipse cx="18" cy="9.1" rx="7.2" ry="1.5" fill="#fff" opacity="0.05" />
                <rect className="robot-scan" x="9.5" y="9.7" width="17" height="0.4" fill={`url(#${ids.scan})`} />

                {/* The face looks around (gaze); the eyes open and close as a
                    pair (mood); each eye blinks on its own. */}
                <g className="robot-face">
                  <g className="robot-eye-glow">
                    <ellipse cx="14.5" cy="11.2" rx="3.6" ry="3.4" fill={`url(#${ids.eyeGlow})`} />
                    <ellipse cx="21.5" cy="11.2" rx="3.6" ry="3.4" fill={`url(#${ids.eyeGlow})`} />
                  </g>
                  <g className="robot-eyes">
                    <g className="robot-eye-l">
                      <rect x="12.9" y="9.3" width="3.2" height="3.8" rx="1.6" fill="var(--pane-light)" />
                      <rect x="13.45" y="9.8" width="0.9" height="1.25" rx="0.45" fill="#fff" opacity="0.8" />
                    </g>
                    <g className="robot-eye-r">
                      <rect x="19.9" y="9.3" width="3.2" height="3.8" rx="1.6" fill="var(--pane-light)" />
                      <rect x="20.45" y="9.8" width="0.9" height="1.25" rx="0.45" fill="#fff" opacity="0.8" />
                    </g>
                    <path className="robot-eye-happy" d="M12.9 11.9 Q14.5 9.5 16.1 11.9 M19.9 11.9 Q21.5 9.5 23.1 11.9" stroke="var(--pane-light)" strokeWidth="1.05" strokeLinecap="round" fill="none" />
                    <path className="robot-eye-wince" d="M13.1 9.9 L15.8 11.2 L13.1 12.5 M22.9 9.9 L20.2 11.2 L22.9 12.5" stroke="var(--pane-light)" strokeWidth="0.95" strokeLinecap="round" strokeLinejoin="round" fill="none" />
                  </g>
                  <g className="robot-mouth">
                    <path className="robot-mouth-smile" d="M15.8 15.8 Q18 17.6 20.2 15.8" stroke="var(--pane-light)" strokeWidth="1" strokeLinecap="round" fill="none" />
                    <path className="robot-mouth-grin" d="M15.3 15.6 Q18 15.9 20.7 15.6 Q20.2 18.3 18 18.3 Q15.8 18.3 15.3 15.6 Z" fill="var(--pane-light)" />
                    <path className="robot-mouth-flat" d="M16.4 16.6 L19.6 16.6" stroke="var(--pane-light)" strokeWidth="0.9" strokeLinecap="round" fill="none" />
                    <ellipse className="robot-mouth-open" cx="18" cy="16.6" rx="1.7" ry="1.15" fill="var(--pane-light)" />
                  </g>
                </g>

                <path className="robot-glint" d="M6.4 7 L8.6 7 L5.2 21 L3 21 Z" fill="#fff" />
              </g>
            </g>
          </g>

          <g className="robot-arm-r">
            <rect x="25" y="22.5" width="4.5" height="5" rx="2.2" fill="var(--pane-shade)" stroke="var(--pane-outline)" strokeWidth="0.4" />
            <rect x="25" y="22.5" width="4.5" height="4.3" rx="2.1" fill="var(--pane-shell)" />
          </g>

          {/* The wave: a raised arm that grows from the shoulder, hidden
              until the beat swaps it in for the hanging hand. */}
          <g className="robot-wave-arm">
            <path d="M24.8 23.4 L30.9 18.2" stroke="var(--pane-outline)" strokeWidth="2.3" strokeLinecap="round" fill="none" />
            <path d="M24.8 23.4 L30.9 18.2" stroke="var(--pane-shade)" strokeWidth="1.6" strokeLinecap="round" fill="none" />
            <ellipse cx="31.9" cy="17.4" rx="2.3" ry="2.5" fill="var(--pane-shade)" stroke="var(--pane-outline)" strokeWidth="0.4" />
            <ellipse cx="31.9" cy="17.05" rx="2.2" ry="2.1" fill="var(--pane-shell)" />
          </g>
        </g>
      </g>
    </>
  );

  return (
    <span
      ref={rootRef}
      className={rootClass}
      style={style}
      data-mood={resolvedMood}
      data-variant={variant}
      data-gaze={gaze ?? undefined}
      data-lipsync={lipsync ? "" : undefined}
      data-hands={handsDown ? "down" : undefined}
      title={title}
      onClick={onClick}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick();
              }
            }
          : undefined
      }
      {...a11y}
    >
      {bust ? (
        <svg viewBox={BUST_VIEWBOX} width={size} height={size} fill="none" xmlns="http://www.w3.org/2000/svg">
          <svg x="0" y="0" width="36" height="38" viewBox={FULL_VIEWBOX} overflow="visible">
            {rig}
          </svg>
        </svg>
      ) : (
        <svg viewBox={FULL_VIEWBOX} width={size} height={size} fill="none" xmlns="http://www.w3.org/2000/svg">
          {rig}
        </svg>
      )}
    </span>
  );
}
