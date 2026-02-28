"use client";

import { useState, useRef, useEffect } from "react";
import { cn } from "@/lib/utils";
import {
  ChevronDown,
  ChevronRight,
  Terminal,
  CheckCircle2,
  XCircle,
  Loader2,
} from "lucide-react";
import type { CCSession, CCSessionEvent } from "@/lib/ChatContext";

interface CCSessionBlockProps {
  session: CCSession;
}

function CCEventLine({ event }: { event: CCSessionEvent }) {
  switch (event.type) {
    case "text":
      return (
        <div className="text-green-300 text-xs font-mono whitespace-pre-wrap break-words">
          {event.text}
        </div>
      );
    case "tool_use":
      return (
        <div className="flex items-center gap-1.5 py-0.5">
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-500/20 text-blue-400 border border-blue-500/30">
            {event.toolName}
          </span>
          {event.toolInput && (
            <span className="text-zinc-500 text-[10px] font-mono truncate max-w-[300px]">
              {event.toolInput}
            </span>
          )}
        </div>
      );
    case "tool_result":
      return (
        <div className="text-zinc-500 text-[10px] font-mono truncate">
          {event.text}
        </div>
      );
    default:
      return null;
  }
}

function StatusIndicator({ status }: { status: CCSession["status"] }) {
  switch (status) {
    case "running":
      return <Loader2 className="w-3.5 h-3.5 text-amber-500 animate-spin flex-shrink-0" />;
    case "completed":
      return <CheckCircle2 className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />;
    case "failed":
      return <XCircle className="w-3.5 h-3.5 text-red-500 flex-shrink-0" />;
  }
}

export function CCSessionBlock({ session }: CCSessionBlockProps) {
  const [isExpanded, setIsExpanded] = useState(true);
  const logRef = useRef<HTMLDivElement>(null);

  // Auto-scroll while running
  useEffect(() => {
    if (session.status === "running" && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [session.events.length, session.status]);

  return (
    <div className="my-3 rounded-lg border border-border overflow-hidden bg-surface-elevated">
      {/* Header */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between px-3 py-2 bg-surface border-b border-border hover:bg-surface-elevated transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          {isExpanded ? (
            <ChevronDown className="w-4 h-4 text-text-secondary flex-shrink-0" />
          ) : (
            <ChevronRight className="w-4 h-4 text-text-secondary flex-shrink-0" />
          )}
          <Terminal className="w-4 h-4 text-text-secondary flex-shrink-0" />
          <span className="text-xs font-medium text-text-secondary uppercase flex-shrink-0">
            Claude Code
          </span>
          <span className="text-xs text-text-tertiary truncate">
            {session.description}
          </span>
        </div>
        <StatusIndicator status={session.status} />
      </button>

      {isExpanded && (
        <>
          {/* Event log */}
          {session.events.length > 0 && (
            <div
              ref={logRef}
              className="max-h-64 overflow-y-auto px-3 py-2 bg-zinc-950 space-y-0.5"
            >
              {session.events.map((event) => (
                <CCEventLine key={event.id} event={event} />
              ))}
            </div>
          )}

          {/* Running indicator when no events yet */}
          {session.events.length === 0 && session.status === "running" && (
            <div className="px-3 py-3 bg-zinc-950 flex items-center gap-2 text-xs text-zinc-500">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span>Starting Claude Code session...</span>
            </div>
          )}

          {/* Result summary */}
          {session.status !== "running" && session.resultSummary && (
            <div className="px-3 py-2 border-t border-border">
              <div className="text-xs text-text-secondary">
                {session.resultSummary}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
