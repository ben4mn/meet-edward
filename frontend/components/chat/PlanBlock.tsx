"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import {
  ChevronDown,
  ChevronRight,
  Circle,
  CheckCircle2,
  XCircle,
  Loader2,
  ListChecks,
} from "lucide-react";
import type { PlanBlock as PlanBlockType, PlanStep } from "@/lib/ChatContext";

interface PlanBlockProps {
  block: PlanBlockType;
}

function StepIcon({ status }: { status: PlanStep["status"] }) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="w-4 h-4 text-green-500 flex-shrink-0" />;
    case "in_progress":
      return <Loader2 className="w-4 h-4 text-amber-500 animate-spin flex-shrink-0" />;
    case "error":
      return <XCircle className="w-4 h-4 text-red-500 flex-shrink-0" />;
    default:
      return <Circle className="w-4 h-4 text-text-tertiary flex-shrink-0" />;
  }
}

export function PlanBlock({ block }: PlanBlockProps) {
  const [isPlanExpanded, setIsPlanExpanded] = useState(true);
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  const completedCount = block.steps.filter(
    (s) => s.status === "completed"
  ).length;
  const totalCount = block.steps.length;
  const progressPercent =
    totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : 0;

  const toggleStepResult = (stepId: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepId)) {
        next.delete(stepId);
      } else {
        next.add(stepId);
      }
      return next;
    });
  };

  return (
    <div className="my-3 rounded-lg border border-border overflow-hidden bg-surface-elevated">
      {/* Header — clickable to collapse/expand */}
      <button
        onClick={() => setIsPlanExpanded(!isPlanExpanded)}
        className="w-full flex items-center justify-between px-3 py-2 bg-surface border-b border-border hover:bg-surface-elevated transition-colors"
      >
        <div className="flex items-center gap-2">
          {isPlanExpanded ? (
            <ChevronDown className="w-4 h-4 text-text-secondary" />
          ) : (
            <ChevronRight className="w-4 h-4 text-text-secondary" />
          )}
          <ListChecks className="w-4 h-4 text-text-secondary" />
          <span className="text-xs font-medium text-text-secondary uppercase">
            Plan
          </span>
          <span className="text-xs text-text-tertiary">
            ({completedCount}/{totalCount} complete)
          </span>
          {block.isComplete && (
            <span className="text-xs text-green-500 font-medium">Done</span>
          )}
        </div>
      </button>

      {isPlanExpanded && (
        <>
          {/* Steps */}
          <div className="px-3 py-2 space-y-1">
            {block.steps.map((step) => (
              <div key={step.id}>
                <div
                  className={cn(
                    "flex items-center gap-2 py-1 rounded px-1",
                    (step.result || step.status === "in_progress") && "cursor-pointer hover:bg-surface"
                  )}
                  onClick={() => {
                    if (step.result) {
                      toggleStepResult(step.id);
                    } else if (step.status === "in_progress") {
                      toggleStepResult(step.id);
                    }
                  }}
                >
                  <StepIcon status={step.status} />
                  <span
                    className={cn(
                      "text-sm flex-1",
                      step.status === "completed" && "text-text-tertiary",
                      step.status === "error" && "text-red-400",
                      step.status === "in_progress" && "text-text-primary font-medium",
                      step.status === "pending" && "text-text-secondary"
                    )}
                  >
                    {step.title}
                  </span>
                  {(step.result || step.status === "in_progress") && (
                    expandedSteps.has(step.id) ? (
                      <ChevronDown className="w-3 h-3 text-text-tertiary flex-shrink-0" />
                    ) : (
                      <ChevronRight className="w-3 h-3 text-text-tertiary flex-shrink-0" />
                    )
                  )}
                </div>
                {step.result && expandedSteps.has(step.id) && (
                  <div className="ml-7 mb-1 px-2 py-1 rounded bg-surface text-xs text-text-secondary">
                    {step.result}
                  </div>
                )}
                {step.status === "in_progress" && !step.result && expandedSteps.has(step.id) && (
                  <div className="ml-7 mb-1 px-2 py-1 rounded bg-surface text-xs text-text-muted flex items-center gap-1.5">
                    <Loader2 className="w-3 h-3 animate-spin text-amber-500" />
                    <span>Working on this step...</span>
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Progress bar */}
          {totalCount > 0 && (
            <div className="px-3 pb-2">
              <div className="h-1.5 bg-surface rounded-full overflow-hidden">
                <div
                  className={cn(
                    "h-full rounded-full transition-all duration-500 ease-out",
                    block.isComplete ? "bg-green-500" : "bg-amber-500"
                  )}
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
            </div>
          )}

          {/* Summary */}
          {block.isComplete && block.summary && (
            <div className="px-3 pb-2">
              <div className="text-xs text-text-secondary italic">
                {block.summary}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
