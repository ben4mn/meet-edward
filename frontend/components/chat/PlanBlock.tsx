"use client";

// A plan Edward made, as a node of the work rail's timeline (2026-09-26):
// "Made a plan · 2 of 3 done", then its steps as a quiet checklist. The step
// he is on carries his emerald dot; a step's result is one tap away.

import React, { useState } from "react";
import { Check, ChevronRight, ListChecks, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PlanBlock as PlanBlockType, PlanStep } from "@/lib/ChatContext";
import { NodeRow, NodeStatus, RailNode } from "./ToolActivityBlock";

function StepMark({ status }: { status: PlanStep["status"] }) {
  switch (status) {
    case "completed":
      return <Check className="h-3.5 w-3.5 text-text-muted" aria-label="Done" />;
    case "in_progress":
      return (
        <span className="flex h-3.5 w-3.5 items-center justify-center" role="img" aria-label="In progress">
          <span className="h-1.5 w-1.5 rounded-full bg-terminal" />
        </span>
      );
    case "error":
      return <X className="h-3.5 w-3.5 text-bad" aria-label="Failed" />;
    default:
      return (
        <span className="flex h-3.5 w-3.5 items-center justify-center" role="img" aria-label="To do">
          <span className="h-2 w-2 rounded-full border border-text-muted/60" />
        </span>
      );
  }
}

export function PlanNode({ block, last }: { block: PlanBlockType; last?: boolean }) {
  const [openStep, setOpenStep] = useState<string | null>(null);
  const done = block.steps.filter((s) => s.status === "completed").length;
  const total = block.steps.length;
  const running = !block.isComplete && block.steps.some((s) => s.status === "in_progress");
  const failed = block.steps.some((s) => s.status === "error");

  return (
    <RailNode chip={<ListChecks />} tone={running ? "running" : failed ? "error" : "neutral"} last={last} testId="rail-plan">
      <NodeRow
        title={block.isComplete ? "Made a plan and finished it" : `Made a plan · ${done} of ${total} done`}
        status={<NodeStatus state={running ? "running" : failed ? "error" : block.isComplete ? "done" : "running"} />}
      />
      <ol className="mt-1 space-y-0.5">
        {block.steps.map((step) => {
          const canOpen = Boolean(step.result);
          const isOpen = openStep === step.id;
          const row = (
            <>
              <span className="mt-[3px] flex-none">
                <StepMark status={step.status} />
              </span>
              <span
                className={cn(
                  "min-w-0 flex-1 text-footnote",
                  step.status === "completed" && "text-text-muted",
                  step.status === "in_progress" && "text-text-primary",
                  step.status === "pending" && "text-text-secondary",
                  step.status === "error" && "text-bad",
                )}
              >
                {step.title}
              </span>
              {canOpen ? (
                <ChevronRight
                  aria-hidden
                  className={cn("mt-[3px] h-3.5 w-3.5 flex-none text-text-muted transition-transform duration-fast", isOpen && "rotate-90")}
                />
              ) : null}
            </>
          );
          return (
            <li key={step.id}>
              {canOpen ? (
                <button
                  type="button"
                  onClick={() => setOpenStep(isOpen ? null : step.id)}
                  aria-expanded={isOpen}
                  className="flex min-h-[28px] w-full items-start gap-2 rounded-xs py-0.5 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
                >
                  {row}
                </button>
              ) : (
                <div className="flex min-h-[28px] items-start gap-2 py-0.5">{row}</div>
              )}
              {isOpen && step.result ? (
                <p className="mb-1 ml-[22px] rounded-sm bg-surface-3 px-2.5 py-1.5 text-footnote text-text-secondary">{step.result}</p>
              ) : null}
            </li>
          );
        })}
      </ol>
      {block.isComplete && block.summary ? <p className="mt-1 text-footnote text-text-secondary">{block.summary}</p> : null}
    </RailNode>
  );
}
