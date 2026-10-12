"use client";

// Small pieces shared by the Memory and House panels (2026-09-26): a chip
// radio group for filter sheets, a status dot, a label/value list, and the
// search field (16px so iOS does not zoom, its own clear button).

import React, { forwardRef } from "react";
import Link from "next/link";
import type { ReactNode } from "react";
import { Search, X } from "lucide-react";
import { cn, Input } from "@/components/ui";
import { STATUS_DOT, type StatusTone } from "@/lib/skillView";

// ------------------------------------------------------------ ChoiceChips
export function ChoiceChips<T extends string>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div className={cn("space-y-2", className)}>
      <p className="text-footnote font-medium text-text-secondary">{label}</p>
      <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-2">
        {options.map((o) => {
          const on = o.value === value;
          return (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => onChange(o.value)}
              className={cn(
                "min-h-[40px] rounded-full border px-3.5 text-footnote font-medium transition-[background-color,color,border-color,transform] duration-fast ease-out active:scale-[.97]",
                on
                  ? "border-transparent bg-text-primary text-primary-bg"
                  : "border-border bg-surface text-text-secondary hover:text-text-primary",
              )}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------ StatusDot
export function StatusDot({ tone, pulse = false, className }: { tone: StatusTone; pulse?: boolean; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block h-2 w-2 flex-none rounded-full",
        STATUS_DOT[tone],
        pulse && "motion-safe:animate-pulse",
        className,
      )}
    />
  );
}

// ------------------------------------------------------------ DetailList
export interface DetailRow {
  label: string;
  value: ReactNode;
  href?: string;
  mono?: boolean;
}

export function DetailList({ rows, className }: { rows: DetailRow[]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(84px,auto)_1fr] gap-x-4 gap-y-2 text-footnote", className)}>
      {rows.map((row) => (
        <div key={row.label} className="contents">
          <dt className="text-text-muted">{row.label}</dt>
          <dd className={cn("min-w-0 text-text-primary [overflow-wrap:anywhere]", row.mono && "font-mono text-caption")}>
            {row.href ? (
              row.href.startsWith("/") ? (
                <Link href={row.href} className="text-terminal-strong underline decoration-terminal/35 underline-offset-[3px]">
                  {row.value}
                </Link>
              ) : (
                <a
                  href={row.href}
                  target="_blank"
                  rel="noreferrer"
                  className="text-terminal-strong underline decoration-terminal/35 underline-offset-[3px]"
                >
                  {row.value}
                </a>
              )
            ) : (
              row.value
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

// ------------------------------------------------------------ SearchField
export const SearchField = forwardRef<
  HTMLInputElement,
  { value: string; onChange: (v: string) => void; label: string; placeholder?: string; className?: string }
>(function SearchField({ value, onChange, label, placeholder, className }, ref) {
  return (
    <div className={cn("relative min-w-0 flex-1", className)}>
      <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
      <Input
        ref={ref}
        type="search"
        inputMode="search"
        enterKeyHint="search"
        autoComplete="off"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="pl-9 pr-10 [&::-webkit-search-cancel-button]:hidden"
      />
      {value ? (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => onChange("")}
          className="absolute right-0 top-1/2 inline-flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-md text-text-muted transition-colors duration-fast hover:text-text-primary"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      ) : null}
    </div>
  );
});
