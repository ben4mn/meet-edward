"use client";

// An on/off switch with a spring knob (docs/design-system-2026-09-25.md §3).
// 30x50 drawn, a 44px hit area around it. `label` is the accessible name;
// put the visible label beside it in a Row or Field.

import React from "react";
import { LazyMotion, m } from "framer-motion";
import { MOTION_FEATURES } from "./motionFeatures";
import { cn } from "./cn";
import { SPRING_PILL, usePrefersReducedMotion } from "./motion";

export interface SwitchProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** Accessible name ("Heartbeat"). */
  label: string;
  disabled?: boolean;
  id?: string;
  className?: string;
}

/** How far the knob travels, in px (track 50 - knob 24 - 2 x 3 padding). */
export const SWITCH_TRAVEL = 20;

export function Switch({ checked, onChange, label, disabled = false, id, className }: SwitchProps) {
  const reduced = usePrefersReducedMotion();
  return (
    <LazyMotion features={MOTION_FEATURES} strict>
      <button
        type="button"
        role="switch"
        id={id}
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => {
          if (!disabled) onChange(!checked);
        }}
        className={cn(
          "relative inline-flex h-[30px] w-[50px] flex-none items-center rounded-full p-[3px]",
          "transition-colors duration-fast ease-out after:absolute after:-inset-[7px] after:content-['']",
          "disabled:cursor-not-allowed disabled:opacity-50",
          checked ? "bg-terminal" : "bg-surface-3 shadow-[inset_0_0_0_1px_rgb(var(--border-rgb)/var(--border-alpha))]",
          className,
        )}
      >
        <m.span
          aria-hidden
          className="block h-6 w-6 rounded-full bg-white shadow-e1"
          initial={false}
          animate={{ x: checked ? SWITCH_TRAVEL : 0 }}
          transition={reduced ? { duration: 0 } : SPRING_PILL}
        />
      </button>
    </LazyMotion>
  );
}
