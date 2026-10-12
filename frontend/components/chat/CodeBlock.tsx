"use client";

// Code Edward ran, as a node of the work rail's timeline (2026-09-26): the
// label ("Ran Python", live "Running Python…") and status on the hairline,
// the code in mono right under it — it is the work, so it is not hidden —
// and the output one tap away (open by itself when it is short and fine).

import React, { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronRight, Code2, Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CodeBlock as CodeBlockType } from "@/lib/ChatContext";
import { describeTool } from "@/lib/toolLabels";
import { codeToolName } from "@/lib/workRail";
import { NodeRow, NodeStatus, RailNode, type NodeState } from "./ToolActivityBlock";

function isShortOutput(output: string | undefined, success: boolean | undefined): boolean {
  if (!output || success === false) return false;
  return output.split("\n").length <= 6 && output.length <= 500;
}

function outputPreview(output: string): string {
  const lines = output.replace(/\n+$/, "").split("\n");
  const first = lines[0] || "";
  const cut = first.length > 56 ? `${first.slice(0, 56)}…` : first;
  return lines.length > 1 ? `${cut} · ${lines.length} lines` : cut;
}

export function codeState(block: CodeBlockType): NodeState {
  if (block.isExecuting) return "running";
  if (block.success === false) return "error";
  return "done";
}

export function CodeNode({ block, last }: { block: CodeBlockType; last?: boolean }) {
  const d = describeTool(codeToolName(block.language));
  const state = codeState(block);
  const [outputOpen, setOutputOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const autoOpened = useRef(false);
  const outputId = useId();

  // Open the output once, when the run finishes short and clean.
  useEffect(() => {
    if (!block.isExecuting && block.output && !autoOpened.current) {
      autoOpened.current = true;
      if (isShortOutput(block.output, block.success)) setOutputOpen(true);
    }
  }, [block.isExecuting, block.output, block.success]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(block.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  const output = block.output ?? "";
  return (
    <RailNode
      chip={<Code2 />}
      tone={state === "running" ? "running" : state === "error" ? "error" : "neutral"}
      last={last}
      testId="rail-code"
    >
      <NodeRow
        title={state === "running" ? d.live : state === "error" ? d.failed : d.title}
        status={<NodeStatus state={state} ms={block.duration_ms} />}
      />
      <div className="mt-1.5 overflow-hidden rounded-sm border border-border bg-surface-3/70">
        <div className="relative">
          <pre className="max-h-56 overflow-auto px-3 py-2.5 pr-10 font-mono text-[12px] leading-[18px] text-text-primary">
            <code>{block.code || (state === "running" ? "…" : "")}</code>
          </pre>
          {block.code ? (
            <button
              type="button"
              onClick={copy}
              aria-label={copied ? "Copied" : "Copy code"}
              className="absolute right-1 top-1 flex h-8 w-8 items-center justify-center rounded-xs text-text-muted transition-colors duration-fast hover:bg-surface-elevated hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-terminal-strong" /> : <Copy className="h-3.5 w-3.5" />}
            </button>
          ) : null}
        </div>
        {output || block.isExecuting ? (
          <div className="border-t border-border">
            <button
              type="button"
              onClick={() => setOutputOpen((o) => !o)}
              aria-expanded={outputOpen}
              aria-controls={outputOpen ? outputId : undefined}
              className="flex min-h-[36px] w-full items-center gap-2 px-3 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-terminal/60"
            >
              <ChevronRight className={cn("h-3.5 w-3.5 flex-none text-text-muted transition-transform duration-fast", outputOpen && "rotate-90")} />
              <span className="text-caption text-text-secondary">Output</span>
              {!outputOpen && output ? (
                <span className="min-w-0 truncate font-mono text-[11.5px] text-text-muted">{outputPreview(output)}</span>
              ) : null}
            </button>
            {outputOpen ? (
              <pre
                id={outputId}
                className={cn(
                  "max-h-64 overflow-auto px-3 pb-2.5 font-mono text-[12px] leading-[18px] whitespace-pre-wrap break-words",
                  block.success === false ? "text-bad" : "text-text-secondary",
                )}
              >
                {output || "Waiting for output…"}
              </pre>
            ) : null}
          </div>
        ) : null}
      </div>
    </RailNode>
  );
}
