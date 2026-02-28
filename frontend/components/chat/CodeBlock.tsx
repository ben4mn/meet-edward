"use client";

import { useState, useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import { ChevronDown, ChevronRight, Check, X, Loader2, Copy, CheckCheck } from "lucide-react";
import type { CodeBlock as CodeBlockType } from "@/lib/ChatContext";

interface CodeBlockProps {
  block: CodeBlockType;
}

function isElegantOutput(output: string | undefined, success: boolean | undefined): boolean {
  if (!output || success === false) return false;
  const lines = output.split("\n");
  return lines.length <= 5 && output.length <= 500;
}

function getOutputPreview(output: string): string {
  const firstLine = output.split("\n")[0] || "";
  const lineCount = output.split("\n").length;
  const truncated = firstLine.length > 60 ? firstLine.slice(0, 60) + "..." : firstLine;
  const badge = lineCount > 1 ? ` (${lineCount} lines)` : "";
  return truncated + badge;
}

export function CodeBlock({ block }: CodeBlockProps) {
  const [isOutputExpanded, setIsOutputExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const hasAutoExpanded = useRef(false);

  // Auto-expand once when execution completes, only if output is short + successful
  useEffect(() => {
    if (!block.isExecuting && block.output && !hasAutoExpanded.current) {
      hasAutoExpanded.current = true;
      if (isElegantOutput(block.output, block.success)) {
        setIsOutputExpanded(true);
      }
    }
  }, [block.isExecuting, block.output, block.success]);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(block.code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="my-3 rounded-lg border border-border overflow-hidden bg-surface-elevated">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-surface border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-text-secondary uppercase">
            {block.language}
          </span>
          {block.isExecuting && (
            <div className="flex items-center gap-1 text-xs text-amber-500">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span>Running...</span>
            </div>
          )}
          {!block.isExecuting && block.success !== undefined && (
            <div className={cn(
              "flex items-center gap-1 text-xs",
              block.success ? "text-green-500" : "text-red-500"
            )}>
              {block.success ? (
                <>
                  <Check className="w-3 h-3" />
                  <span>Success</span>
                </>
              ) : (
                <>
                  <X className="w-3 h-3" />
                  <span>Error</span>
                </>
              )}
              {block.duration_ms !== undefined && (
                <span className="text-text-tertiary ml-1">
                  ({block.duration_ms}ms)
                </span>
              )}
            </div>
          )}
        </div>
        <button
          onClick={handleCopy}
          className="p-1 hover:bg-surface rounded text-text-secondary hover:text-text-primary transition-colors"
          title="Copy code"
        >
          {copied ? (
            <CheckCheck className="w-4 h-4 text-green-500" />
          ) : (
            <Copy className="w-4 h-4" />
          )}
        </button>
      </div>

      {/* Code */}
      <pre className="p-3 overflow-x-auto text-sm bg-gray-900 text-gray-100">
        <code className="font-mono">{block.code}</code>
      </pre>

      {/* Output Section */}
      {(block.output || block.isExecuting) && (
        <div className="border-t border-border">
          <button
            onClick={() => setIsOutputExpanded(!isOutputExpanded)}
            className="w-full flex items-center gap-2 px-3 py-2 hover:bg-surface transition-colors text-left"
          >
            {isOutputExpanded ? (
              <ChevronDown className="w-4 h-4 text-text-secondary" />
            ) : (
              <ChevronRight className="w-4 h-4 text-text-secondary" />
            )}
            <span className="text-xs font-medium text-text-secondary">Output</span>
            {!isOutputExpanded && block.output && (
              <span className="text-xs text-text-tertiary font-mono truncate ml-1">
                {getOutputPreview(block.output)}
              </span>
            )}
          </button>

          {isOutputExpanded && (
            <div className="px-3 pb-3">
              <pre className={cn(
                "p-2 rounded text-xs font-mono overflow-x-auto max-h-64 overflow-y-auto",
                block.success === false ? "bg-red-950/30 text-red-300" : "bg-gray-800 text-gray-200"
              )}>
                {block.output || (block.isExecuting ? "Waiting for output..." : "No output")}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
