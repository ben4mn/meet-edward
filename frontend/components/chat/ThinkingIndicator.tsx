"use client";

import { useState } from "react";
import { Loader2, Check, AlertCircle, ChevronRight, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ProgressStep } from "@/lib/ChatContext";

interface ThinkingIndicatorProps {
  content?: string;
  progressSteps?: ProgressStep[];
  isStreaming?: boolean;
}

export function ThinkingIndicator({ content, progressSteps, isStreaming = true }: ThinkingIndicatorProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  // Completed mode: collapsed summary with expand
  if (!isStreaming && progressSteps && progressSteps.length > 0) {
    // Filter to only tool_execution steps for display
    const toolSteps = progressSteps.filter(s => s.step === "tool_execution");
    const memoryStep = progressSteps.find(s => s.step === "memory_search" && s.count && s.count > 0);

    // Don't render if no tool execution steps
    if (toolSteps.length === 0) return null;

    // Build summary parts
    const summaryParts: string[] = [];
    summaryParts.push(`Used ${toolSteps.length} tool${toolSteps.length !== 1 ? "s" : ""}`);
    if (memoryStep && memoryStep.count) {
      summaryParts.push(`${memoryStep.count} memor${memoryStep.count !== 1 ? "ies" : "y"}`);
    }
    const summaryText = summaryParts.join(" · ");

    return (
      <div className="mb-2 border-l-2 border-terminal/30 pl-2">
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center gap-1 text-xs text-text-muted hover:text-text-secondary transition-colors"
        >
          {isExpanded ? (
            <ChevronDown className="w-3 h-3" />
          ) : (
            <ChevronRight className="w-3 h-3" />
          )}
          <span>{summaryText}</span>
        </button>
        {isExpanded && (
          <div className="space-y-1 pt-1 pl-4">
            {progressSteps
              .filter(s => s.step === "tool_execution" || (s.step === "memory_search" && s.count && s.count > 0))
              .map((step) => (
                <div key={step.id} className="flex items-center gap-2 text-xs">
                  {step.status === "error" ? (
                    <AlertCircle className="w-3 h-3 text-red-400" />
                  ) : (
                    <Check className="w-3 h-3 text-text-muted" />
                  )}
                  <span className={cn(
                    step.status === "error" ? "text-red-400" : "text-text-muted"
                  )}>
                    {step.message}
                  </span>
                </div>
              ))}
          </div>
        )}
      </div>
    );
  }

  // Active mode: step list with spinners
  if (progressSteps && progressSteps.length > 0) {
    return (
      <div className="space-y-1 py-2">
        {progressSteps.map((step) => (
          <div key={step.id} className="flex items-center gap-2 text-sm animate-fade-in-step">
            {step.status === "started" && (
              <Loader2 className="w-3 h-3 animate-spin text-terminal" />
            )}
            {step.status === "completed" && (
              <Check className="w-3 h-3 text-text-muted" />
            )}
            {step.status === "error" && (
              <AlertCircle className="w-3 h-3 text-red-400" />
            )}
            <span
              className={cn(
                step.status === "completed" && "text-text-muted",
                step.status === "started" && "text-text-secondary",
                step.status === "error" && "text-red-400"
              )}
            >
              {step.message}
            </span>
          </div>
        ))}
      </div>
    );
  }

  // Fallback to simple spinner
  return (
    <div className="flex items-center gap-2 text-text-secondary text-sm py-2">
      <Loader2 className="w-4 h-4 animate-spin" />
      <span>{content || "Thinking..."}</span>
    </div>
  );
}
