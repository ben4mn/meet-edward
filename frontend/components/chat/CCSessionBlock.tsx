"use client";

// A helper's coding session, as a node of the work rail's timeline
// (2026-09-26): what it was asked, its status, and its log — prose in Inter,
// tool names and inputs in mono — following the tail while it runs.

import React, { useEffect, useRef } from "react";
import { Code2 } from "lucide-react";
import type { CCSession, CCSessionEvent } from "@/lib/ChatContext";
import { NodeRow, NodeStatus, RailNode } from "./ToolActivityBlock";

function EventLine({ event }: { event: CCSessionEvent }) {
  switch (event.type) {
    case "text":
      return <p className="text-footnote text-text-secondary whitespace-pre-wrap [overflow-wrap:anywhere]">{event.text}</p>;
    case "tool_use":
      return (
        <p className="flex min-w-0 items-center gap-1.5">
          <span className="flex-none rounded-xs bg-surface-elevated px-1.5 py-px font-mono text-[11px] text-text-primary">{event.toolName}</span>
          {event.toolInput ? <span className="min-w-0 truncate font-mono text-[11px] text-text-muted">{event.toolInput}</span> : null}
        </p>
      );
    case "tool_result":
      return <p className="truncate text-caption font-normal text-text-muted">{event.text}</p>;
    default:
      return null;
  }
}

export function SessionNode({ session, last }: { session: CCSession; last?: boolean }) {
  const logRef = useRef<HTMLDivElement>(null);
  const state = session.status === "running" ? "running" : session.status === "failed" ? "error" : "done";

  // Follow the log's tail while the helper works.
  useEffect(() => {
    if (session.status === "running" && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [session.events.length, session.status]);

  const title = session.description ? `A helper is coding: ${session.description}` : "A helper is writing code";
  return (
    <RailNode chip={<Code2 />} tone={state === "running" ? "running" : state === "error" ? "error" : "neutral"} last={last} testId="rail-session">
      <NodeRow
        title={state === "running" ? title : session.description ? `A helper coded: ${session.description}` : "A helper wrote code"}
        status={<NodeStatus state={state} />}
      />
      {session.events.length > 0 ? (
        <div ref={logRef} className="mt-1.5 max-h-48 space-y-1 overflow-y-auto rounded-sm bg-surface-3/70 px-2.5 py-2">
          {session.events.map((e) => (
            <EventLine key={e.id} event={e} />
          ))}
        </div>
      ) : state === "running" ? (
        <p className="mt-0.5 text-caption font-normal text-text-muted">Starting the session…</p>
      ) : null}
      {session.status !== "running" && session.resultSummary ? (
        <p className="mt-1 text-footnote text-text-secondary">{session.resultSummary}</p>
      ) : null}
    </RailNode>
  );
}
