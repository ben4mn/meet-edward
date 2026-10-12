"use client";

// Buttons (docs/design-system-2026-09-25.md §3). Emerald means Edward:
// The owner's own primary action is a high-contrast neutral (`primary`), and the
// emerald fill (`edward`) is for Edward-initiated actions ("Try it",
// "Wake now"). 44px on touch, 36px on md+; every button presses.

import React, { forwardRef } from "react";
import Link from "next/link";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "./cn";

export type ButtonVariant = "primary" | "edward" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg" | "icon";

const BASE =
  "relative inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-md font-semibold " +
  "transition-[transform,background-color,border-color,color,box-shadow,opacity] duration-press ease-out " +
  "active:scale-[.97] disabled:pointer-events-none disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50 " +
  "[&_svg]:h-4 [&_svg]:w-4 [&_svg]:flex-none";

export const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-text-primary text-primary-bg shadow-e1 hover:bg-text-primary/90",
  edward: "bg-terminal text-terminal-ink shadow-e1 hover:bg-terminal/90",
  secondary: "border border-border bg-surface-3 text-text-primary hover:bg-surface-elevated",
  ghost: "bg-transparent text-text-secondary hover:bg-surface-3 hover:text-text-primary",
  // Dark ink on the dark theme's light orange, light ink on the light
  // theme's deep red: both clear 4.5:1.
  danger: "bg-bad text-primary-bg shadow-e1 hover:bg-bad/90",
};

export const BUTTON_SIZES: Record<ButtonSize, string> = {
  // 36px drawn, 44px hit area (the ::after), 32px on md+.
  sm: "h-9 px-3 text-footnote after:absolute after:inset-x-0 after:-inset-y-1 after:content-[''] md:h-8",
  md: "h-11 px-4 text-[15px] leading-5 md:h-9 md:text-[14px]",
  lg: "h-12 px-5 text-[16px] leading-6",
  icon: "h-11 w-11 md:h-9 md:w-9",
};

export function buttonClasses({
  variant = "primary",
  size = "md",
  className,
}: { variant?: ButtonVariant; size?: ButtonSize; className?: string } = {}): string {
  return cn(BASE, BUTTON_VARIANTS[variant], BUTTON_SIZES[size], className);
}

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Busy: disabled, `aria-busy`, a small pulse before the label. */
  loading?: boolean;
  /** Renders a Next link styled as a button. */
  href?: string;
  /** Leading icon (lucide), sized for you. */
  icon?: ReactNode;
  children?: ReactNode;
}

function BusyDot() {
  return (
    <span aria-hidden className="inline-block h-1.5 w-1.5 flex-none rounded-full bg-current motion-safe:animate-pulse" />
  );
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", loading = false, href, icon, className, children, type, disabled, ...rest },
  ref,
) {
  const cls = buttonClasses({ variant, size, className });
  const content = (
    <>
      {loading ? <BusyDot /> : icon}
      {children}
    </>
  );
  if (href && !disabled && !loading) {
    return (
      <Link href={href} className={cls} aria-label={rest["aria-label"]} title={rest.title}>
        {content}
      </Link>
    );
  }
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      className={cls}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {content}
    </button>
  );
});
