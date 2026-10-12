"use client";

// Edward's breathing pip (docs/design-system-2026-09-25.md §3): loading is
// Edward, not a spinner. On its own (2026-09-26 perf pass) so the auth wait,
// the login door and Today draw it without the rest of ./Feedback, which
// re-exports it.

import React from "react";
import { cn } from "./cn";

const PULSE_SIZE = { sm: "h-1.5 w-1.5", md: "h-2 w-2", lg: "h-3 w-3" } as const;

/** Edward's breathing pip: "I'm on it". Replaces spinners. `center` makes
 *  it a padded, centred block for a panel that is loading. */
export function Pulse({
  label = "Loading",
  showLabel = false,
  size = "md",
  center = false,
  className,
}: {
  label?: string;
  showLabel?: boolean;
  size?: keyof typeof PULSE_SIZE;
  center?: boolean;
  className?: string;
}) {
  return (
    <span
      role="status"
      aria-label={label}
      className={cn(
        "inline-flex items-center gap-2 text-footnote text-text-muted",
        center && "flex w-full justify-center py-8",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block flex-none rounded-full bg-terminal shadow-[0_0_0_3px_rgb(var(--terminal-rgb)/0.14),0_0_10px_rgb(var(--terminal-rgb)/0.45)]",
          "motion-safe:animate-breathe motion-safe:[animation-duration:2.4s]",
          PULSE_SIZE[size],
        )}
      />
      {showLabel ? <span aria-hidden>{label}</span> : null}
    </span>
  );
}

/** The spec's name for the same thing. */
export const EdwardPulse = Pulse;
