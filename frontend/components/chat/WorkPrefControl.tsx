"use client";

import React from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { useWorkPref } from "@/lib/useWorkPref";
import { WORK_PREF_OPTIONS } from "@/lib/workRail";

/** "Edward's work in chat" — how much of the thinking/tool trace shows. A
 *  choice of the owner's, so the selection is neutral (emerald means Edward). */
export function WorkPrefControl({ className }: { className?: string }) {
  const [pref, setPref] = useWorkPref();
  return (
    <div className={cn("space-y-3", className)} role="radiogroup" aria-label="Edward's work in chat">
      <div>
        <h3 className="text-callout text-text-primary">Edward&apos;s work in chat</h3>
        <p className="text-footnote text-text-muted">Thinking, memory search, tool calls and code on every turn. This device only.</p>
      </div>
      <div className="grid gap-2">
        {WORK_PREF_OPTIONS.map((o) => {
          const on = o.value === pref;
          return (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => setPref(o.value)}
              className={cn(
                "flex min-h-[44px] items-start gap-3 rounded-md border px-3 py-2.5 text-left outline-none",
                "transition-[background-color,border-color,transform] duration-press ease-out active:scale-[.99]",
                "focus-visible:ring-2 focus-visible:ring-terminal/60 focus-visible:ring-offset-2 focus-visible:ring-offset-primary-bg",
                on ? "border-text-primary/40 bg-surface-3" : "border-border bg-surface hover:bg-surface-3/60",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "mt-0.5 inline-flex h-5 w-5 flex-none items-center justify-center rounded-full border transition-colors duration-fast",
                  on ? "border-text-primary bg-text-primary text-primary-bg" : "border-text-muted/60",
                )}
              >
                {on ? <Check className="h-3 w-3" strokeWidth={3} /> : null}
              </span>
              <span className="min-w-0">
                <span className="block text-body font-medium text-text-primary">{o.label}</span>
                <span className="block text-footnote text-text-muted">{o.detail}</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
