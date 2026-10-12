"use client";

// Large-format Edward for /screen and the hub. Wraps the shared Pane
// character. The screen passes his real `mood` (lib/screenPresence.ts, from
// the display signals and /api/mind/now) and his status as `ariaLabel`;
// the legacy `state` is still read when no mood is given (its looking-*
// values become a gaze). The act-* class is kept for CSS that still reads it.

import React, { type MutableRefObject } from "react";

import { PaneSvg, type PaneVariant } from "@/components/pane/PaneSvg";
import {
  stateToActivityClass,
  stateToGaze,
  stateToMood,
} from "@/components/pane/paneActivity";
import { moodToActivity, type Beat, type Gaze, type Mood } from "@/lib/edwardMood";
import type { EdwardLife } from "@/lib/useEdwardLife";
import type { EdwardState } from "../lib/types";

interface Props {
  size?: number;
  /** Legacy: the old display state. Ignored for the mood when `mood` is set. */
  state?: EdwardState;
  /** What he is doing, from real signals. Wins over `state`. */
  mood?: Mood;
  /** Where he looks, held. Prefer a one-off glance through `lifeRef`. */
  gaze?: Gaze | null;
  accent?: string;
  className?: string;
  onClick?: () => void;
  /** His status, for screen readers and the tooltip. */
  ariaLabel?: string;
  /** A one-shot reaction (e.g. a nod when poked). */
  beat?: Beat | null;
  /** Change to replay the same beat. */
  beatKey?: string | number;
  /** He is the one Edward on /screen, so he breathes. Default true. */
  live?: boolean;
  /** Receives the life handle (glance / fire / blink). */
  lifeRef?: MutableRefObject<EdwardLife | null>;
  /** "full" (default) or "bust" (head and shoulders). The stage always draws him whole. */
  variant?: PaneVariant;
}

export function DisplayAvatar({
  size = 340,
  state = "idle",
  mood,
  gaze,
  accent,
  className,
  onClick,
  ariaLabel,
  beat,
  beatKey,
  live = true,
  lifeRef,
  variant,
}: Props) {
  const resolvedMood = mood ?? stateToMood(state);
  return (
    <PaneSvg
      size={size}
      mood={resolvedMood}
      activity={mood ? moodToActivity(mood) : stateToActivityClass(state)}
      gaze={gaze !== undefined ? gaze : mood ? null : stateToGaze(state)}
      beat={beat}
      beatKey={beatKey}
      live={live}
      accent={accent}
      className={className}
      onClick={onClick}
      ariaLabel={ariaLabel}
      lifeRef={lifeRef}
      variant={variant}
    />
  );
}
