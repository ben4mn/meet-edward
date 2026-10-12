"use client";

import React from "react";
import { Coffee, Eye, Moon, MoonStar, RotateCcw, Sun, Sunrise } from "lucide-react";
import { describeWake, wakeLabel, WakeInfo } from "./proactiveWakes";

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  nightly: MoonStar,
  speak: Eye,
  morning: Sunrise,
  evening: Moon,
  reflect: RotateCcw,
  reflect_lite: RotateCcw,
  free: Coffee,
};

/** What replaced the five job toggles (mind rebuild Phase 7): the wakes
 * Edward takes on his own — since the proactivity rebuild (2026-10-11) just
 * the 03:00 nightly. Nothing here is a switch — he decides. */
export function WakesNote({ wakes }: { wakes: WakeInfo[] }) {
  if (!wakes.length) {
    return <p className="py-3 text-footnote text-text-muted">No wakes scheduled yet — they&apos;re set up when I start.</p>;
  }
  return (
    <div>
      <ul>
        {wakes.map((w) => {
          const Icon = ICONS[w.kind] ?? Sun;
          return (
            <li key={w.kind} className="flex min-h-[48px] items-center gap-3 border-b border-border py-2">
              <span className="flex h-8 w-8 flex-none items-center justify-center rounded-sm bg-terminal-soft text-terminal-strong">
                <Icon className="h-4 w-4" />
              </span>
              <span className="flex-1 text-callout text-text-primary">{wakeLabel(w.kind)}</span>
              <span className="text-footnote tabular-nums text-text-muted">{describeWake(w.cron)}</span>
            </li>
          );
        })}
      </ul>
      <p className="py-3 text-footnote text-text-muted">
        This is the only wake I keep on a clock: a quiet look back at the day, at night. Everything else I do when
        something happens — a message, a promise coming due, a dose running late. There&apos;s nothing here to toggle:
        my notes land on Today and my thinking in Thoughts.
      </p>
    </div>
  );
}
