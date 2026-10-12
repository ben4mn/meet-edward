"use client";

// Everything Edward did during a turn, as one surface above the answer
// (2026-09-26; docs/design-system-2026-09-25.md §1.5 "Show the work, then
// fold it into a receipt"). This stays visible by design — it is never
// behind a debug flag.
//
// Live: a 16px Edward mirroring the turn (thinking, working, speaking; a nod
// for each tool that went well, a wince for one that failed), the latest
// reasoning heading as the status (each new status fades in once — nothing
// loops inside the transcript), the seconds so far, and the timeline open
// (lib/workRail.ts buildTimeline: memory, each reasoning step, tools, code,
// plans, helpers — in arrival order). Beside the open timeline the header
// never repeats the running step's own label.
// The answer starts: the timeline folds (a smooth height animation) above
// empty space, before there is text to read, and stays folded for the rest
// of the turn; at done the header becomes the receipt at the same height —
// "Thought for 14s · texted Parker · searched memory · opened 2 pages" — with
// outward acts in Edward's emerald; a tap reopens it. (It used to fold at
// done and yank the answer the owner was reading up by the timeline's height.)
// The edward:workDisplay preference (collapse | open | tap) and a manual
// toggle decide open/closed. A turn rebuilt from history renders through
// exactly the same path.

import React, { memo, useEffect, useId, useMemo, useRef, useState } from "react";
import { AnimatePresence, LazyMotion, m } from "framer-motion";
import { MOTION_FEATURES } from "@/components/ui/motionFeatures";
import { Check, ChevronRight, Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { DURATION, EASE_OUT, REDUCED_FADE, usePrefersReducedMotion } from "@/components/ui/motion";
import type { Message } from "@/lib/ChatContext";
import type { Turn } from "@/lib/turnModel";
import { beatForToolTransition, type Beat, type Mood, type ToolTally } from "@/lib/edwardMood";
import {
  buildTimeline,
  formatElapsed,
  isRailOpen,
  liveStatus,
  receiptText,
  summarizeWork,
  workReceipt,
  type ReceiptPart,
  type ReceiptTone,
  type TimelineNode,
} from "@/lib/workRail";
import { describeTool } from "@/lib/toolLabels";
import { useWorkPref } from "@/lib/useWorkPref";
import { CodeNode } from "./CodeBlock";
import { PlanNode } from "./PlanBlock";
import { SessionNode } from "./CCSessionBlock";
import { ExhaustedNode, MemoryNode, PendingNode, ReasoningNode, ToolNode } from "./ToolActivityBlock";

// How long each finished turn took, by message id, so an answer keeps its
// "Thought for 14s" after the next turn starts (a reload shows "Thought").
const ELAPSED = new Map<string, number>();
const ELAPSED_MAX = 100;

function rememberElapsed(id: string, ms: number) {
  ELAPSED.set(id, ms);
  if (ELAPSED.size > ELAPSED_MAX) ELAPSED.delete(ELAPSED.keys().next().value as string);
}

function moodFor(turn: Turn | null, message: Message): Mood {
  switch (turn?.phase) {
    case "working":
      return "work";
    case "writing":
      return "speak";
    case "recalling":
    case "thinking":
      return "think";
    default:
      return message.content ? "speak" : "think";
  }
}

/** Seconds since the turn started, ticking while live. */
function useElapsed(startedAt: number | null, live: boolean): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live || startedAt == null) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [live, startedAt]);
  return startedAt == null ? null : Math.max(0, now - startedAt);
}

/** nod / wince / acted as the turn's tools finish (the avatar throttles them). */
function useToolBeat(turn: Turn | null): { beat: Beat | null; key: number } {
  const [state, setState] = useState<{ beat: Beat | null; key: number }>({ beat: null, key: 0 });
  const prev = useRef<ToolTally | null>(null);
  const ok = turn?.tools.ok ?? 0;
  const failed = turn?.tools.failed ?? 0;
  const outward = turn?.outward ?? 0;
  useEffect(() => {
    const next: ToolTally = { ok, failed, outward };
    const b = prev.current ? beatForToolTransition(prev.current, next) : null;
    prev.current = next;
    if (b) setState((s) => ({ beat: b, key: s.key + 1 }));
  }, [ok, failed, outward]);
  return state;
}

const RECEIPT_TONE: Record<ReceiptTone, string> = {
  lead: "text-text-secondary",
  outward: "font-medium text-terminal-strong",
  plain: "text-text-muted",
  muted: "text-text-muted",
  warn: "text-warn",
  bad: "text-bad",
};

function Receipt({ parts }: { parts: ReceiptPart[] }) {
  return (
    <span className="min-w-0 flex-1 text-footnote leading-[20px]">
      {parts.map((p, i) => (
        <React.Fragment key={i}>
          {i > 0 ? (
            <span aria-hidden className="mx-1.5 text-text-muted/60">
              ·
            </span>
          ) : null}
          <span className={RECEIPT_TONE[p.tone]}>{p.text}</span>
        </React.Fragment>
      ))}
    </span>
  );
}

function NodeView({ node, last, live }: { node: TimelineNode; last: boolean; live: boolean }) {
  switch (node.kind) {
    case "memory":
      return <MemoryNode status={node.status} count={node.count} last={last} />;
    case "reasoning":
      return <ReasoningNode title={node.title} body={node.body} active={live && node.latest} last={last} />;
    case "tool":
      return <ToolNode activity={node.activity} last={last} />;
    case "code":
      return <CodeNode block={node.block} last={last} />;
    case "session":
      return <SessionNode session={node.session} last={last} />;
    case "plan":
      return <PlanNode block={node.plan} last={last} />;
    case "pending":
      return <PendingNode name={node.name} args={node.args} last={last} />;
    case "exhausted":
      return <ExhaustedNode message={node.message} last={last} />;
  }
}

function traceAsText(message: Message, nodes: TimelineNode[]): string {
  const lines: string[] = [];
  for (const n of nodes) {
    if (n.kind === "memory") lines.push(`[memory] ${n.count ?? 0} found`);
    else if (n.kind === "reasoning") lines.push(`[thinking]${n.title ? ` ${n.title}` : ""}\n${n.body}`);
    else if (n.kind === "tool") {
      const a = n.activity;
      lines.push(`[${a.status}] ${describeTool(a.tool_name, a.args_summary).title} (${a.tool_name}${a.args_summary ? ` ${a.args_summary}` : ""})${a.result ? `\n${a.result}` : ""}`);
    } else if (n.kind === "code") lines.push(`[${n.block.language}]\n${n.block.code}${n.block.output ? `\n--- output\n${n.block.output}` : ""}`);
    else if (n.kind === "plan") lines.push(`[plan]\n${n.plan.steps.map((s) => `- [${s.status}] ${s.title}`).join("\n")}`);
    else if (n.kind === "session") lines.push(`[helper] ${n.session.description} (${n.session.status})`);
  }
  if (message.content) lines.push(`[answer]\n${message.content}`);
  return lines.join("\n\n");
}

function CopyTrace({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard unavailable */
        }
      }}
      className="relative ml-[34px] mt-1 inline-flex items-center gap-1.5 rounded-xs text-caption font-normal text-text-muted transition-colors duration-fast after:absolute after:-inset-2.5 after:content-[''] hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
    >
      {copied ? <Check className="h-3 w-3 text-terminal-strong" /> : <Copy className="h-3 w-3" />}
      {copied ? "Copied" : "Copy what I did"}
    </button>
  );
}

interface WorkRailProps {
  message: Message;
  /** The turn streams into this message (ChatContext isLiveMessage). */
  live: boolean;
  /** ChatContext's `turn` when it belongs to this message (live or just ended), else null. */
  turn: Turn | null;
}

function WorkRailImpl({ message, live, turn: turnProp }: WorkRailProps) {
  const turn = turnProp && turnProp.id === message.id ? turnProp : null;
  const [pref] = useWorkPref();
  const [userToggled, setUserToggled] = useState<boolean | null>(null);
  const reduced = usePrefersReducedMotion();
  const timelineId = useId();
  const nodes = useMemo(() => buildTimeline(message), [message]);
  const summary = summarizeWork(message);
  const elapsedLive = useElapsed(live && turn ? turn.startedAt : null, live);
  const toolBeat = useToolBeat(live ? turn : null);

  if (turn && turn.endedAt != null && !live) rememberElapsed(message.id, turn.endedAt - turn.startedAt);
  const elapsedDone = ELAPSED.get(message.id) ?? null;

  if (!summary.hasWork && !live && nodes.length === 0) return null;

  const answering = live && Boolean(message.content.trim());
  const open = isRailOpen(pref, live, userToggled, answering) && nodes.length > 0;
  const toggle = () => setUserToggled(!open);
  const receipt = live ? [] : workReceipt(message, { elapsedMs: elapsedDone });
  const status = live ? liveStatus(message, turn, { timelineOpen: open }) : "";
  const transition = reduced ? REDUCED_FADE : { duration: DURATION.base, ease: EASE_OUT };
  const exitTransition = reduced ? REDUCED_FADE : { duration: DURATION.base * 0.7, ease: EASE_OUT };
  // The answer has begun: fold at once, so the first words appear right
  // under the header instead of sliding up the timeline's height under
  // the owner's eyes. (AnimatePresence `custom`: an exiting child keeps its old
  // props, so the choice travels through the variants.)
  const instantFold = live && answering && userToggled === null;
  const timelineVariants = {
    open: reduced ? { opacity: 1, transition } : { height: "auto", opacity: 1, transition },
    closed: (instant: boolean) =>
      instant
        ? { height: 0, opacity: 0, transition: { duration: 0 } }
        : reduced
          ? { opacity: 0, transition: exitTransition }
          : { height: 0, opacity: 0, transition: exitTransition },
  };

  return (
    <div
      className="mb-4"
      data-testid="work-rail"
      data-live={live ? "true" : "false"}
      role="group"
      aria-label="What Edward did"
    >
      {live ? (
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={open ? timelineId : undefined}
          className="group relative flex min-h-[28px] w-full items-center gap-3 rounded-xs text-left after:absolute after:-inset-y-2 after:inset-x-0 after:content-[''] focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
        >
          <span className="flex w-[22px] flex-none justify-center">
            <EdwardAvatar
              size={16}
              variant="bust"
              mood={moodFor(turn, message)}
              beat={toolBeat.beat}
              beatKey={toolBeat.key}
              animated
            />
          </span>
          {/* Visual only: the chat view's TurnAnnouncer speaks for the turn. */}
          <span className="min-w-0 flex-1 truncate text-footnote font-medium text-text-secondary">
            {/* A new status fades in once (keyed); nothing loops in the transcript. */}
            <span key={status} className="motion-safe:animate-fade-in">
              {status}
            </span>
          </span>
          {elapsedLive != null ? (
            <span className="flex-none text-caption tabular-nums text-text-muted" aria-label="elapsed">
              {formatElapsed(Math.max(1000, elapsedLive))}
            </span>
          ) : null}
          {nodes.length > 0 ? (
            <ChevronRight
              aria-hidden
              className={cn("h-3.5 w-3.5 flex-none text-text-muted transition-transform duration-fast", open && "rotate-90")}
            />
          ) : null}
        </button>
      ) : (
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={open ? timelineId : undefined}
          aria-label={`${receiptText(receipt)}. ${open ? "Hide" : "Show"} what I did`}
          className="group relative flex min-h-[28px] w-full items-start gap-2 rounded-xs py-1 text-left after:absolute after:-inset-y-1 after:inset-x-0 after:content-[''] focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
        >
          <Receipt parts={receipt} />
          <ChevronRight
            aria-hidden
            className={cn(
              "mt-[3px] h-3.5 w-3.5 flex-none text-text-muted transition-transform duration-fast group-hover:text-text-secondary",
              open && "rotate-90",
            )}
          />
        </button>
      )}

      <LazyMotion features={MOTION_FEATURES} strict>
        <AnimatePresence initial={false} custom={instantFold}>
          {open ? (
            <m.div
              key="timeline"
              id={timelineId}
              custom={instantFold}
              variants={timelineVariants}
              initial="closed"
              animate="open"
              exit="closed"
              style={{ overflow: "hidden" }}
            >
              <ol className="pb-1 pt-3">
                {nodes.map((n, i) => (
                  <NodeView key={n.key} node={n} last={i === nodes.length - 1} live={live} />
                ))}
              </ol>
              {!live ? <CopyTrace text={traceAsText(message, nodes)} /> : null}
            </m.div>
          ) : null}
        </AnimatePresence>
      </LazyMotion>
    </div>
  );
}

/** Memoised: a finished answer's rail re-renders only when its message does. */
export const WorkRail = memo(WorkRailImpl);
