"use client";

// What Edward was shown when he woke, folded (2026-09-25; restyled
// 2026-09-26). The mind's and the pulse's "user" messages are the raw pack —
// 10–20K chars that made `edward-mind` unreadable as a chat. Folded, the
// thread reads as what he was shown (one card, in his words: "What I woke up
// to · morning · thu 7:00am") and what he did (his answer and its work
// rail). The whole card toggles the raw text, which stays in mono because it
// is data. What counts as a pack: lib/packMessage.ts.

import React, { useId, useState } from "react";
import { ChevronRight, ScrollText } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatChars, packSummary, type PackSummary } from "@/lib/packMessage";

function titleFor(s: PackSummary): string {
  if (s.kind === "wake") return "What I woke up to";
  if (s.kind === "pulse") return "What my screens showed me";
  return "What I was given";
}

function metaFor(s: PackSummary): string {
  return [s.reason, s.when, formatChars(s.chars)].filter(Boolean).join(" · ");
}

/** Preview lines without the header and markdown heading marks. */
function previewLines(s: PackSummary): string[] {
  return s.firstLines
    .slice(1, 3)
    .map((l) => l.replace(/^#+\s*/, "").trim())
    .filter(Boolean);
}

export function PackCard({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const rawId = useId();
  const summary = packSummary(text);
  const preview = previewLines(summary);

  return (
    <div className="min-w-0 overflow-hidden rounded-lg border border-border bg-surface shadow-e1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={open ? rawId : undefined}
        className="flex w-full items-start gap-3 px-3.5 py-3 text-left transition-colors duration-fast hover:bg-surface-3/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-terminal/60"
      >
        <span aria-hidden className="mt-px flex h-7 w-7 flex-none items-center justify-center rounded-sm bg-surface-3 text-text-secondary">
          <ScrollText className="h-3.5 w-3.5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-callout text-text-primary">{titleFor(summary)}</span>
          <span className="block text-caption font-normal tabular-nums text-text-muted">{metaFor(summary)}</span>
          {!open && preview.length > 0 ? (
            <span className="mt-1.5 block space-y-0.5">
              {preview.map((line, i) => (
                <span key={i} className="block truncate text-footnote text-text-secondary">
                  {line}
                </span>
              ))}
            </span>
          ) : null}
        </span>
        <ChevronRight
          aria-hidden
          className={cn("mt-1.5 h-4 w-4 flex-none text-text-muted transition-transform duration-fast", open && "rotate-90")}
        />
      </button>
      {open && (
        <pre
          id={rawId}
          data-testid="pack-raw"
          className="max-h-[60vh] overflow-auto border-t border-border bg-surface-3/40 px-3.5 py-3 font-mono text-[11.5px] leading-[18px] text-text-secondary whitespace-pre-wrap break-words [overflow-wrap:anywhere]"
        >
          {text}
        </pre>
      )}
    </div>
  );
}
