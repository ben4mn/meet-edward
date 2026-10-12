"use client";

// His own page — the "Edward: state" document every wake opens with — as
// an excerpt in his voice, and in full in a sheet. The excerpt is decided
// by edwardHomeModel.stateExcerpt.

import React, { Suspense, lazy, useRef, useState } from "react";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import type { DocumentItem } from "@/lib/api";
import { formatRelative } from "@/lib/time";
import { Sheet, Skeleton, buttonClasses } from "@/components/ui";
import { stateExcerpt } from "./edwardHomeModel";
import { cn } from "@/lib/utils";

// The chat's markdown renderer (react-markdown, remark, every Prism language:
// ~266 kB gzipped) loads only when the sheet opens — the landing and the rooms
// show a two-sentence excerpt and must not pay for it up front
// (__tests__/settingsBundle.test.ts walks the static import graph).
const MarkdownContent = lazy(() =>
  import("@/components/chat/MarkdownContent").then((m) => ({ default: m.MarkdownContent })),
);

export function StateDocSheet({ doc, open, onClose }: { doc: DocumentItem | null; open: boolean; onClose: () => void }) {
  // The body mounts on the first open (that is when the renderer loads) and
  // stays, so the sheet does not empty while it animates closed.
  const opened = useRef(false);
  if (open) opened.current = true;
  return (
    <Sheet
      open={open && Boolean(doc)}
      onClose={onClose}
      title="Who he is right now"
      description={doc?.updated_at ? `His own page · rewritten ${formatRelative(doc.updated_at)}` : "His own page"}
      footer={
        doc ? (
          <Link
            href={`/settings/documents?id=${encodeURIComponent(doc.id)}`}
            className={buttonClasses({ variant: "secondary", className: "w-full" })}
            onClick={onClose}
          >
            Open in Documents <ArrowUpRight className="h-4 w-4" aria-hidden />
          </Link>
        ) : null
      }
    >
      {doc && opened.current ? (
        <div className="text-body text-text-primary">
          <Suspense fallback={<Skeleton lines={4} />}>
            <MarkdownContent content={doc.content} />
          </Suspense>
        </div>
      ) : null}
    </Sheet>
  );
}

/** The opening of his page, set off by a thin emerald rule (his voice),
 *  with "Read all" opening the sheet. */
export function StateExcerpt({
  doc,
  loading,
  failed,
  sentences = 2,
  className,
}: {
  doc: DocumentItem | null;
  loading: boolean;
  failed?: boolean;
  sentences?: number;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  if (loading) {
    return (
      <div className={cn("border-l-2 border-terminal/40 pl-3", className)}>
        <Skeleton lines={2} />
      </div>
    );
  }
  const text = doc ? stateExcerpt(doc.content, { sentences }) : "";
  if (!doc || !text) {
    return (
      <p className={cn("border-l-2 border-border pl-3 text-footnote text-text-muted", className)}>
        {failed ? "I can't open my own page right now." : "I haven't written my own page yet."}
      </p>
    );
  }
  return (
    <div className={className}>
      <blockquote className="border-l-2 border-terminal/60 pl-3 text-body text-text-primary">{text}</blockquote>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="-mb-2 -ml-2 mt-0.5 inline-flex min-h-[44px] items-center rounded-md px-2 text-footnote font-medium text-terminal-strong transition-opacity duration-fast hover:opacity-80 active:opacity-60"
      >
        Read all →
      </button>
      <StateDocSheet doc={doc} open={open} onClose={() => setOpen(false)} />
    </div>
  );
}
