"use client";

// "Your conversations" (UX pass 2026-09-26): the owner's recent threads, a dot
// only on the ones that came from outside, the time on the right; the line
// about Edward's own thoughts only when there were some.

import React from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Card, EmptyState, Row, SectionLabel } from "@/components/ui";
import { formatRelative } from "@/lib/time";
import { describeThoughts, isInbound, type TodayConversation } from "./todayModel";

export function ConversationsSection({
  conversations,
  thoughts,
  now,
}: {
  conversations: TodayConversation[];
  thoughts: number;
  now: Date;
}) {
  const thoughtLine = describeThoughts(thoughts);
  return (
    <section aria-labelledby="today-conversations">
      <SectionLabel action="Open chat" actionHref="/chat">
        <span id="today-conversations">Your conversations</span>
      </SectionLabel>
      <Card className="mt-2 px-0 py-1">
        {conversations.length === 0 ? (
          <EmptyState className="py-6">No conversations yet. Say hi whenever you like.</EmptyState>
        ) : (
          conversations.map((c) => (
            <Row
              key={c.id}
              href={c.href}
              className="px-3"
              title={
                <span className="inline-flex max-w-full items-center gap-2">
                  {isInbound(c) ? (
                    <span className="h-2 w-2 flex-none rounded-full bg-info" role="img" aria-label="From outside" title="Came in from outside" />
                  ) : null}
                  <span className="min-w-0 truncate">{c.title}</span>
                </span>
              }
              trailing={<span suppressHydrationWarning>{formatRelative(c.updated_at, { now })}</span>}
            />
          ))
        )}
      </Card>
      {thoughtLine ? (
        <Link
          href="/settings/thoughts"
          className="mt-1 inline-flex min-h-[44px] items-center gap-1.5 px-1 text-footnote text-text-muted transition-colors duration-fast hover:text-text-primary"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-terminal" aria-hidden />
          {thoughtLine}
          <ArrowRight className="h-3.5 w-3.5" aria-hidden />
        </Link>
      ) : null}
    </section>
  );
}
