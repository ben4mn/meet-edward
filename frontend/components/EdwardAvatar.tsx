"use client";

// Edward, sized for the app: the presence bar, the dock, chat, login, the
// voice view. Internally the shared Pane character (components/pane).
//
// Defaults follow the design system (§4):
//   * ≤36px is a bust (head and shoulders) and still — a dock icon or a
//     message avatar never blinks at you from a scrolling list;
//   * bigger is the full figure, alive (blinks, glances, beats);
//   * only `live` breathes — one live Edward per screen, so `animated`
//     never makes a second one.
// Old call sites (`size`, `animated`, `className`) keep working.

import React, { type CSSProperties, type MutableRefObject } from "react";

import type { Beat, Gaze, Mood } from "@/lib/edwardMood";
import type { EdwardLife } from "@/lib/useEdwardLife";
import { usePresenceHandsDown, usePresenceProvided } from "@/lib/presenceContext";
import { cn } from "@/lib/utils";
import { PaneSvg, type PaneVariant } from "./pane/PaneSvg";

export type EdwardAvatarSize = "sm" | "md" | "lg" | "xl" | number;

export interface EdwardAvatarProps {
  /** A keyword (sm 32 · md 64 · lg 96 · xl 120) or px. Default md. */
  size?: EdwardAvatarSize;
  /** What he is doing (lib/edwardMood deriveMood). Default idle. */
  mood?: Mood;
  /** A one-shot reaction; plays when it (or beatKey) changes. */
  beat?: Beat | null;
  /** Change to replay the same beat. */
  beatKey?: string | number;
  /** Where he looks; suppresses idle glances while set. */
  gaze?: Gaze | null;
  /** "bust" (default ≤36px) or "full". */
  variant?: PaneVariant;
  /** The one Edward per screen who breathes and runs mood loops. */
  live?: boolean;
  /** false = fully still. Default: true when live or bigger than 36px. */
  animated?: boolean;
  /** Perk on his own when the app returns to the foreground. Default: when
   *  live and no presence provider is feeding beats (inside the app shell
   *  the provider sends the one perk as a beat; /login and the hub perk on
   *  their own). An avatar under the provider whose beats come from
   *  elsewhere (the voice view) passes it explicitly. */
  perkOnReturn?: boolean;
  /** Mouth follows the `--mouth` CSS variable (TTS level) while speaking. */
  lipsync?: boolean;
  /** His hands in Chrome are down: the antenna tip (his trust light) goes
   *  amber. Default: the presence provider's signal; false outside it. */
  handsDown?: boolean;
  className?: string;
  /** Tooltip; also the accessible name when `aria-label` is absent. */
  title?: string;
  "aria-label"?: string;
  onClick?: () => void;
  /** e.g. `{"--level": 0.4, "--mouth": 0.7}` from the voice view. */
  style?: CSSProperties;
  /** Imperative handle (fire / glance / blink). */
  lifeRef?: MutableRefObject<EdwardLife | null>;
}

export const EDWARD_AVATAR_PX: Readonly<Record<"sm" | "md" | "lg" | "xl", number>> = {
  sm: 32,
  md: 64,
  lg: 96,
  xl: 120,
};

/** At or below this he is a still bust by default. */
export const SMALL_AVATAR_PX = 36;

export function avatarPx(size: EdwardAvatarSize): number {
  return typeof size === "number" ? size : EDWARD_AVATAR_PX[size];
}

export function EdwardAvatar({
  size = "md",
  mood,
  beat,
  beatKey,
  gaze,
  variant,
  live = false,
  animated,
  perkOnReturn,
  lipsync,
  handsDown,
  className,
  title,
  "aria-label": ariaLabel,
  onClick,
  style,
  lifeRef,
}: EdwardAvatarProps) {
  const px = avatarPx(size);
  const small = px <= SMALL_AVATAR_PX;
  const provided = usePresenceProvided();
  const providedHandsDown = usePresenceHandsDown();

  return (
    <PaneSvg
      size={px}
      mood={mood ?? "idle"}
      beat={beat}
      beatKey={beatKey}
      gaze={gaze}
      variant={variant ?? (small ? "bust" : "full")}
      live={live}
      animated={animated ?? (live || !small)}
      perkOnReturn={perkOnReturn ?? (live && !provided)}
      lipsync={lipsync}
      handsDown={handsDown ?? providedHandsDown}
      className={cn(className)}
      title={title}
      ariaLabel={ariaLabel}
      onClick={onClick}
      style={style}
      lifeRef={lifeRef}
    />
  );
}
