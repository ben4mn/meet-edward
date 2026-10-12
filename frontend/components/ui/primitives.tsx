"use client";

// Shared primitives (docs/design-system-2026-09-25.md §3 "Recipes"). The
// point is that a status chip, a tappable row and a section label look the
// same on Today, in chat and in every Edward panel. Compose with these
// before hand-rolling a new card.
//
// Thumb first: anything tappable is at least 44px tall and presses
// (`active:`), and whole rows and cards are the target, not a word inside.

import React from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ReactNode, MouseEventHandler } from "react";
import { cn } from "./cn";

export type Tone = "neutral" | "ok" | "warn" | "bad" | "info" | "edward";

/** Chip fill + text for each tone. Status tones never borrow emerald;
 *  `edward` is his and only his. */
export const TONE_CHIP: Record<Tone, string> = {
  neutral: "bg-surface-3 text-text-secondary",
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
  info: "bg-info-soft text-info",
  edward: "bg-terminal-soft text-terminal-strong",
};

export function Pill({
  tone = "neutral",
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-caption tabular-nums",
        TONE_CHIP[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const CARD = "rounded-lg border border-border bg-surface p-3 shadow-e1";
const CARD_PRESSABLE =
  "block text-left transition-[transform,background-color] duration-press ease-out hover:bg-surface-elevated active:scale-[.985]";

/** A hairlined surface. With `href` or `onClick` the whole card is the
 *  target and presses. */
export function Card({
  className,
  children,
  as: Tag = "div",
  href,
  onClick,
}: {
  className?: string;
  children: ReactNode;
  as?: "div" | "section" | "article";
  href?: string;
  onClick?: MouseEventHandler<HTMLElement>;
}) {
  if (href) {
    return (
      <Link
        href={href}
        className={cn(CARD, CARD_PRESSABLE, className)}
        onClick={onClick as MouseEventHandler<HTMLAnchorElement> | undefined}
      >
        {children}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button type="button" className={cn(CARD, CARD_PRESSABLE, "w-full", className)} onClick={onClick}>
        {children}
      </button>
    );
  }
  return <Tag className={cn(CARD, className)}>{children}</Tag>;
}

const LABEL_ACTION =
  "-my-[15px] -mr-2 inline-flex min-h-[44px] items-center rounded-md px-2 text-footnote font-medium normal-case tracking-normal text-text-secondary transition-colors duration-fast hover:text-text-primary";

/** A section's overline ("For you", "What I did"). `action` is either a
 *  quiet note on the right (a count) or, with `onAction` / `actionHref`, a
 *  control with a padded 44px hit area that does not grow the label. A link
 *  or button passed as the action node gets the same hit area. The label is
 *  a heading (h2 by default, `as` for another level) so a screen reader's
 *  headings rotor can jump between sections; it looks the same. */
export function SectionLabel({
  children,
  action,
  onAction,
  actionHref,
  className,
  as: Heading = "h2",
}: {
  children: ReactNode;
  action?: ReactNode;
  onAction?: () => void;
  actionHref?: string;
  className?: string;
  as?: "h2" | "h3" | "h4" | "div";
}) {
  let right: ReactNode = null;
  if (action != null && action !== false) {
    if (actionHref) {
      right = (
        <Link href={actionHref} className={LABEL_ACTION}>
          {action}
        </Link>
      );
    } else if (onAction) {
      right = (
        <button type="button" onClick={onAction} className={LABEL_ACTION}>
          {action}
        </button>
      );
    } else {
      right = (
        <span
          className={cn(
            "text-caption normal-case tracking-normal tabular-nums text-text-muted",
            "[&>a]:-my-[15px] [&>a]:-mr-2 [&>a]:inline-flex [&>a]:min-h-[44px] [&>a]:items-center [&>a]:px-2 [&>a]:text-text-secondary hover:[&>a]:text-text-primary",
            "[&>button]:-my-[15px] [&>button]:-mr-2 [&>button]:inline-flex [&>button]:min-h-[44px] [&>button]:items-center [&>button]:px-2",
          )}
        >
          {action}
        </span>
      );
    }
  }
  return (
    <div className={cn("flex items-center justify-between gap-3 px-0.5 pt-1 text-overline text-text-muted", className)}>
      <Heading className="min-w-0 truncate">{children}</Heading>
      {right}
    </div>
  );
}

export function EmptyState({
  children,
  title,
  icon,
  action,
  className,
}: {
  children?: ReactNode;
  title?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center px-4 py-8 text-center text-footnote text-text-muted", className)}>
      {icon ? <div className="mb-3 text-text-muted [&_svg]:h-6 [&_svg]:w-6">{icon}</div> : null}
      {title ? <p className="mb-1 text-callout text-text-primary">{title}</p> : null}
      {children ? <div className="max-w-sm text-balance">{children}</div> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

/** A segmented choice: ARIA radios with one tab stop (the chosen option);
 *  arrows, Home and End move the choice. Each option is `relative`, so an
 *  sr-only phrase inside a label stays inside it (an absolutely placed one
 *  anchored to <body> stretched the whole document), and 44px on touch. */
export function Segment<T extends string>({
  value,
  onChange,
  options,
  className,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode; count?: number }[];
  className?: string;
  label?: string;
}) {
  const refs = React.useRef<(HTMLButtonElement | null)[]>([]);
  const chosen = options.findIndex((o) => o.value === value);
  const stop = chosen >= 0 ? chosen : 0;

  const onKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, i: number) => {
    const n = options.length;
    let next: number | null = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % n;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (i - 1 + n) % n;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = n - 1;
    if (next === null) return;
    e.preventDefault();
    refs.current[next]?.focus();
    if (options[next].value !== value) onChange(options[next].value);
  };

  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn(
        "flex rounded-md border border-border bg-surface-3 p-0.5 text-footnote dark:bg-primary-bg/60",
        className,
      )}
    >
      {options.map((o, i) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={i === stop ? 0 : -1}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={cn(
              "relative min-h-[44px] flex-1 rounded-[10px] px-2 py-1.5 font-medium transition-[background-color,color,box-shadow,transform] duration-fast ease-out active:scale-[.97] md:min-h-[32px]",
              on
                ? "bg-surface text-text-primary shadow-e1 dark:bg-surface-3"
                : "text-text-muted hover:text-text-primary",
            )}
          >
            {o.label}
            {o.count != null && o.count > 0 ? (
              <span className={cn("ml-1 tabular-nums", on ? "opacity-70" : "opacity-60")}>{o.count}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** One line in a list: optional leading icon, title + detail, optional
 *  trailing text. The whole row is the target (44px+). A link row shows a
 *  chevron; an expander (`expanded` given) shows a chevron-down that turns
 *  and unclamps its detail; a plain action shows none. Rows stack inside a
 *  Card, separated by hairlines. */
export function Row({
  href,
  onClick,
  icon,
  title,
  detail,
  trailing,
  tone,
  expanded,
  className,
  children,
}: {
  href?: string;
  onClick?: MouseEventHandler<HTMLElement>;
  icon?: ReactNode;
  title: ReactNode;
  detail?: ReactNode;
  trailing?: ReactNode;
  tone?: Tone;
  /** Makes the row an expander: `aria-expanded`, a turning chevron, and the
   *  detail shown in full while true. */
  expanded?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  const isExpander = expanded !== undefined;
  const open = expanded === true;
  const interactive = Boolean(href || onClick);
  const body = (
    <>
      {icon ? (
        <span
          className={cn(
            "flex h-8 w-8 flex-none items-center justify-center self-start rounded-sm [&_svg]:h-4 [&_svg]:w-4",
            tone && tone !== "neutral" ? TONE_CHIP[tone] : "bg-surface-3 text-text-secondary",
          )}
        >
          {icon}
        </span>
      ) : null}
      <span className="min-w-0 flex-1">
        <span className={cn("block text-callout text-text-primary", open ? "whitespace-pre-wrap" : "line-clamp-2")}>
          {title}
        </span>
        {detail ? (
          <span className={cn("mt-0.5 block text-footnote text-text-muted", open ? "whitespace-pre-wrap" : "line-clamp-2")}>
            {detail}
          </span>
        ) : null}
        {children}
      </span>
      {trailing ? (
        <span className="flex-none text-caption tabular-nums text-text-muted">{trailing}</span>
      ) : null}
      {href && !isExpander ? (
        <ChevronRight className="h-4 w-4 flex-none text-text-muted" aria-hidden />
      ) : null}
      {isExpander ? (
        <ChevronDown
          className={cn(
            "h-4 w-4 flex-none text-text-muted transition-transform duration-base ease-in-out",
            open && "rotate-180",
          )}
          aria-hidden
        />
      ) : null}
    </>
  );
  const cls = cn(
    "flex min-h-[44px] w-full items-center gap-3 border-b border-border px-1 py-2.5 text-left last:border-b-0",
    // No radius: a rounded row bends its own bottom hairline at both ends.
    interactive && "transition-colors duration-fast hover:bg-surface-3/50 active:bg-surface-3/80",
    className,
  );
  if (href) {
    return (
      <Link href={href} className={cls} onClick={onClick as MouseEventHandler<HTMLAnchorElement> | undefined}>
        {body}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button type="button" className={cls} onClick={onClick} aria-expanded={isExpander ? open : undefined}>
        {body}
      </button>
    );
  }
  return <div className={cls}>{body}</div>;
}
