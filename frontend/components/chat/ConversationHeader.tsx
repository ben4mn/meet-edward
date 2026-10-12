"use client";

// The slim bar above the transcript (2026-09-26 chat chrome): the title —
// provisional from the owner's first line until the conversation has a row, so
// nothing jumps when the real one lands — where it came from (a neutral
// dot and a word; emerald only for what Edward started) and how long ago.
// 44px on the phone, where it sits under the shell's presence bar.
//
// On desktop, with the sidebar hidden, the controls to bring it back and to
// start a new chat live here, in flow at the left — never a floating button
// over the title. A thought entry (a wake that acted) adds the "Edward acted
// · … · Right call?" bar under it.

import React from "react";
import { useRouter } from "next/navigation";
import { PanelLeftOpen, SquarePen } from "lucide-react";

import type { Conversation } from "@/lib/api";
import { useChat } from "@/lib/ChatContext";
import { isMindAction, withThought } from "@/lib/thoughtEntry";
import { headerSource, headerTitle } from "@/lib/conversationHeader";
import { formatRelative } from "@/lib/time";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui";
import { ThoughtEntryHeader } from "./ThoughtEntryHeader";

function SidebarControls() {
  const { toggleSidebar, startNewChat } = useChat();
  const router = useRouter();
  return (
    <div className="-ml-1.5 mr-1 hidden flex-none items-center gap-0.5 md:flex">
      <Button variant="ghost" size="icon" onClick={toggleSidebar} aria-label="Show conversations" title="Show conversations (⌘\)">
        <PanelLeftOpen className="h-4 w-4" aria-hidden />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => {
          startNewChat();
          router.push("/chat");
        }}
        aria-label="New chat"
        title="New chat (⌘⇧O)"
      >
        <SquarePen className="h-4 w-4" aria-hidden />
      </Button>
    </div>
  );
}

export function ConversationHeader({ conversation }: { conversation: Conversation | null }) {
  const { activeConversation, conversationId, messages, sidebarCollapsed } = useChat();
  const conv = withThought(conversation, activeConversation, conversationId);
  const hasTurns = messages.length > 0;

  // An empty new chat: nothing to title. The desktop controls still need a
  // home while the sidebar is hidden.
  if (!hasTurns && !conv) {
    if (!sidebarCollapsed) return null;
    return (
      <div className="hidden h-12 flex-none items-center px-4 md:flex">
        <SidebarControls />
      </div>
    );
  }

  const title = headerTitle(conv, messages);
  const source = headerSource(conv);
  const stamp = conv ? conv.last_activity_at || conv.updated_at : null;
  const when = stamp ? formatRelative(stamp) : null;

  return (
    <div className="flex-none">
      <div className="flex h-11 items-center gap-2 border-b border-border/70 bg-primary-bg/95 px-4 backdrop-blur md:h-12 md:px-4">
        {sidebarCollapsed ? <SidebarControls /> : null}
        <h1 className="min-w-0 flex-1 truncate text-[15px] font-semibold leading-5 text-text-primary" title={title}>
          {title}
        </h1>
        {source || when ? (
          <p className="flex flex-none items-center gap-1.5 text-caption font-normal text-text-muted">
            {source ? (
              <>
                <span
                  aria-hidden
                  className={cn("h-1.5 w-1.5 rounded-full", source.edward ? "bg-terminal/80" : "bg-text-muted")}
                />
                <span className={cn(source.edward && "text-terminal-strong")}>{source.label}</span>
              </>
            ) : null}
            {source && when ? <span aria-hidden>·</span> : null}
            {when ? <span className="tabular-nums">{when}</span> : null}
          </p>
        ) : null}
      </div>
      {isMindAction(conv) && conv?.thought ? (
        // Keyed by the backend's verdict too: a reload that brings a newer
        // one starts the control from it rather than from a stale row.
        <ThoughtEntryHeader
          key={`${conv.id}:${conv.thought.ledger_id}:${conv.thought.verdict ?? "none"}`}
          thought={conv.thought}
        />
      ) : null}
    </div>
  );
}
