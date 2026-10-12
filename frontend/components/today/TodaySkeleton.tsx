"use client";

// Cold start only (UX pass 2026-09-26): shapes that match what will land,
// so nothing jumps when it does. Hidden from assistive tech; the page says
// "Loading" once.

import React from "react";
import { SectionLabel } from "@/components/ui/primitives";

function Bar({ className }: { className: string }) {
  return <span className={`block rounded-full bg-surface-3 motion-safe:animate-pulse ${className}`} />;
}

export function NeedsSkeleton() {
  return (
    <section aria-hidden>
      <SectionLabel>For you</SectionLabel>
      <div className="mt-2 space-y-2">
        {[0, 1].map((i) => (
          <div key={i} className="rounded-lg border border-border bg-surface p-3.5 shadow-e1">
            <div className="flex items-start gap-3">
              <span className="h-9 w-9 flex-none rounded-md bg-surface-3 motion-safe:animate-pulse" />
              <div className="min-w-0 flex-1 space-y-2 pt-1">
                <Bar className="h-3.5 w-3/5" />
                <Bar className="h-3 w-4/5" />
                <div className="flex gap-2 pt-2">
                  <span className="h-11 w-24 rounded-md bg-surface-3 motion-safe:animate-pulse md:h-9" />
                  <span className="h-11 w-20 rounded-md bg-surface-3/60 motion-safe:animate-pulse md:h-9" />
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export function ListSkeleton({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <section aria-hidden>
      <SectionLabel>{label}</SectionLabel>
      <div className="mt-2 rounded-lg border border-border bg-surface px-3 py-1 shadow-e1">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="flex items-center gap-3 border-b border-border py-3 last:border-b-0">
            <span className="h-8 w-8 flex-none rounded-sm bg-surface-3 motion-safe:animate-pulse" />
            <div className="min-w-0 flex-1 space-y-2">
              <Bar className="h-3 w-2/3" />
              <Bar className="h-2.5 w-2/5" />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

// Stand-ins for the sections below the fold while their chunk is still on
// its way (TodayView loads them with lib/lazyComponent, which draws these
// only when the chunk has not landed by the time the feed has). Same label,
// same card and row sizes, one card or row per item, so the swap does not
// move anything.

export function DidSkeleton({ did }: { did: readonly unknown[] }) {
  return <ListSkeleton label="What I did" rows={Math.max(1, did.length)} />;
}

export function ConversationsSkeleton({ conversations }: { conversations: readonly unknown[] }) {
  return <ListSkeleton label="Your conversations" rows={Math.max(1, conversations.length)} />;
}
