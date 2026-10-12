// Pure helpers for applying SSE stream deltas to a chat message's
// in-flight state. Lives in its own file so we can unit-test the
// reducer without dragging in the whole ChatContext + React tree.
//
// Used by ChatContext live during SSE consumption. The shape is
// intentionally minimal — just the slice of Message we mutate while
// streaming. ChatContext spreads it back into the full Message.

import type {
  CCSession,
  CCSessionEvent,
  CodeBlock,
  Message,
  PlanBlock,
  PlanStep,
  ProgressStep,
  ToolActivity,
  TraceItem,
} from "./ChatContext";
import type { StreamEvent } from "./api";
import { EXECUTION_TOOLS, isToolFailure, type StreamOutcome } from "./turnModel";

export interface StreamMessageState {
  content: string;
  reasoningContent?: string;
}

/**
 * N4: append a Codex reasoning_summary delta to the message's
 * reasoningContent. Reasoning is shown in a distinct UI panel above
 * the answer so the user sees "Edward is thinking…" with content
 * instead of a blank wait.
 *
 * - Empty deltas are no-ops (returns the same object so React skips
 *   re-renders).
 * - Undefined reasoningContent on input is treated as empty string.
 */
export function applyReasoningDelta(
  state: StreamMessageState,
  delta: string
): StreamMessageState {
  if (!delta) return state;
  return {
    ...state,
    reasoningContent: (state.reasoningContent ?? "") + delta,
  };
}

/**
 * Append a content (text) delta. Kept separate so the reasoning
 * trace persists alongside the answer rather than being mixed in.
 */
export function applyContentDelta(
  state: StreamMessageState,
  delta: string
): StreamMessageState {
  if (!delta) return state;
  return {
    ...state,
    content: state.content + delta,
  };
}


/** Written into answers before 2026-09-26; taken out if a stale copy has one. */
const OLD_INTERRUPTED_MARKER = "_(stream interrupted)_";

/**
 * N8, reworded 2026-09-26 (review: interrupted-answer-jargon): the answer
 * content when the SSE stream errors out.
 *
 * - Already streamed content → kept exactly as Edward said it. The
 *   message is flagged `wasCutOff` and the bubble says so in his voice,
 *   with Retry — never "_(stream interrupted)_" inside the markdown.
 * - Nothing streamed → a refusal the server worded for the owner (a 409) is his
 *   answer; otherwise nothing (the cut-off notice is the whole message —
 *   no canned "Sorry, I encountered an error").
 */
export function contentForStreamError(
  state: { streamed: string; refusal?: string },
): string {
  if (!state.streamed.trim()) return state.refusal?.trim() || "";
  const text = state.streamed.trimEnd();
  return text.endsWith(OLD_INTERRUPTED_MARKER) ? text.slice(0, -OLD_INTERRUPTED_MARKER.length).trimEnd() : state.streamed;
}


// ---------------------------------------------------------------------------
// The streaming assistant message, one SSE event at a time (2026-09-25).
//
// ChatContext keeps one StreamCursor per stream (what the last event was,
// which code block is executing, id counters) and folds each event:
//
//   const { cursor, ctx } = advanceCursor(cursor, event, messageId, now);
//   message = applyStreamEvent(message, event, ctx);
//
// advanceCursor depends only on the event sequence, so ids are computed
// once, outside any React state updater; applyStreamEvent is pure and
// immutable. When the stream ends, finishStreamMessage clears every live
// flag whatever the ending was.
// ---------------------------------------------------------------------------

export type StreamKind = "content" | "reasoning" | "work";

export interface StreamCursor {
  /** The kind of the last event that matters for segmenting (neutral events leave it). */
  lastKind: StreamKind | null;
  codeSeq: number;
  toolSeq: number;
  eventSeq: number;
  /** The execution tool's code block between its tool_start and tool_end. */
  currentCodeId: string | null;
}

export interface StepContext {
  now: number;
  /** A different kind of event came since the last delta of this kind. */
  breakBefore: boolean;
  /** tool_start of an execution tool: the new block; code, execution events and tool_end: the current one. */
  codeBlockId: string | null;
  /** tool_start of any other tool: the new activity's id. */
  activityId: string | null;
  seq: number;
}

export function newCursor(): StreamCursor {
  return { lastKind: null, codeSeq: 0, toolSeq: 0, eventSeq: 0, currentCodeId: null };
}

const WORK_EVENTS = new Set([
  "thinking",
  "tool_args_delta",
  "tool_start",
  "tool_end",
  "code",
  "execution_output",
  "execution_result",
  "plan_created",
  "plan_step_update",
  "plan_updated",
  "plan_completed",
  "cc_session_start",
  "cc_text",
  "cc_tool_use",
  "cc_tool_result",
  "cc_session_end",
]);

/** content / reasoning / work, or null for events that do not separate text
 *  (debug memory matches, the voice transcript, "generating", done). */
export function streamKind(event: Pick<StreamEvent, "type" | "content" | "step">): StreamKind | null {
  if (event.type === "content") return event.content ? "content" : null;
  if (event.type === "reasoning") return event.content ? "reasoning" : null;
  if (event.type === "progress") return event.step === "generating" ? null : "work";
  return WORK_EVENTS.has(event.type) ? "work" : null;
}

export function advanceCursor(
  cursor: StreamCursor,
  event: StreamEvent,
  messageId: string,
  now: number,
): { cursor: StreamCursor; ctx: StepContext } {
  const kind = streamKind(event);
  const next: StreamCursor = { ...cursor, eventSeq: cursor.eventSeq + 1 };
  const ctx: StepContext = { now, breakBefore: false, codeBlockId: null, activityId: null, seq: next.eventSeq };
  if (kind === "content" || kind === "reasoning") {
    ctx.breakBefore = cursor.lastKind !== null && cursor.lastKind !== kind;
  }
  if (kind) next.lastKind = kind;

  const tool = event.tool_name || "";
  switch (event.type) {
    case "tool_start":
      if (EXECUTION_TOOLS.has(tool)) {
        next.codeSeq = cursor.codeSeq + 1;
        next.currentCodeId = `code-${messageId}-${next.codeSeq}`;
        ctx.codeBlockId = next.currentCodeId;
      } else if (tool) {
        next.toolSeq = cursor.toolSeq + 1;
        ctx.activityId = `tool-${messageId}-${next.toolSeq}-${tool}`;
      }
      break;
    case "code":
    case "execution_output":
    case "execution_result":
      ctx.codeBlockId = cursor.currentCodeId;
      break;
    case "tool_end":
      if (EXECUTION_TOOLS.has(tool)) {
        ctx.codeBlockId = cursor.currentCodeId;
        next.currentCodeId = null;
      }
      break;
  }
  return { cursor: next, ctx };
}

function paragraphJoin(existing: string, delta: string): string {
  if (!existing) return delta;
  if (existing.endsWith("\n\n") || delta.startsWith("\n\n")) return existing + delta;
  if (existing.endsWith("\n") && delta.startsWith("\n")) return existing + delta;
  if (existing.endsWith("\n") || delta.startsWith("\n")) return `${existing}\n${delta}`;
  return `${existing}\n\n${delta}`;
}

/** Answer text: a new segment after other work starts a new paragraph. */
export function joinContent(existing: string, delta: string, breakBefore: boolean): string {
  if (!delta) return existing;
  if (breakBefore && existing.trim()) return paragraphJoin(existing, delta);
  return existing + delta;
}

function countBold(text: string): number {
  let n = 0;
  for (let i = text.indexOf("**"); i !== -1; i = text.indexOf("**", i + 2)) n++;
  return n;
}

const SENTENCE_END = /[.!?…]/;

/**
 * Codex streams reasoning summary parts back to back with nothing between
 * them ("…in the calendar.**Drafting a reply**"). Put a paragraph break
 * before a "**" that opens a heading straight after the end of a sentence.
 */
function separateSummaryParts(existing: string, delta: string): string {
  let out = "";
  let bold = countBold(existing);
  let i = 0;
  while (i < delta.length) {
    if (delta.startsWith("**", i)) {
      const prev = i > 0 ? delta[i - 1] : existing[existing.length - 1];
      if (bold % 2 === 0 && prev !== undefined && SENTENCE_END.test(prev)) out += "\n\n";
      out += "**";
      bold++;
      i += 2;
      continue;
    }
    out += delta[i];
    i++;
  }
  return out;
}

/** Reasoning summary text: a new paragraph after other work, and between glued summary parts. */
export function joinReasoning(existing: string, delta: string, breakBefore: boolean): string {
  if (!delta) return existing;
  if (breakBefore && existing.trim()) return paragraphJoin(existing, delta);
  return existing + separateSummaryParts(existing, delta);
}

function extendTrace(
  trace: TraceItem[] | undefined,
  kind: "text" | "reasoning",
  delta: string,
  fresh: boolean,
): TraceItem[] {
  const list = trace ?? [];
  const last = list[list.length - 1];
  if (!fresh && last && last.kind === kind) {
    return [...list.slice(0, -1), { kind, text: last.text + delta }];
  }
  const text = delta.replace(/^\n+/, "");
  return text ? [...list, { kind, text }] : list;
}

function withMemoryTrace(trace: TraceItem[] | undefined, status: ProgressStep["status"], count?: number): TraceItem[] {
  const list = trace ?? [];
  const item: TraceItem =
    status === "started" ? { kind: "memory", status } : { kind: "memory", status, ...(count !== undefined ? { count } : {}) };
  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i].kind === "memory") {
      const next = list.slice();
      next[i] = item;
      return next;
    }
  }
  return [...list, item];
}

function updateCode(msg: Message, id: string | null, fn: (b: CodeBlock) => CodeBlock): Message {
  if (!id || !msg.codeBlocks) return msg;
  const i = msg.codeBlocks.findIndex((b) => b.id === id);
  if (i < 0) return msg;
  const blocks = msg.codeBlocks.slice();
  blocks[i] = fn(blocks[i]);
  return { ...msg, codeBlocks: blocks };
}

function withoutPending(
  pending: Message["pendingToolArgs"],
  toolName: string,
): Message["pendingToolArgs"] {
  if (!pending) return pending;
  const kept = Object.entries(pending).filter(([, v]) => v.name !== toolName);
  if (kept.length === Object.keys(pending).length) return pending;
  return kept.length ? Object.fromEntries(kept) : undefined;
}

function toPlanSteps(steps: NonNullable<StreamEvent["plan_steps"]>): PlanStep[] {
  return steps.map((s) => ({ id: s.id, title: s.title, status: s.status as PlanStep["status"], result: s.result }));
}

/** One SSE event → the next assistant message (immutable; same object when nothing changed). */
export function applyStreamEvent(msg: Message, event: StreamEvent, ctx: StepContext): Message {
  switch (event.type) {
    case "thinking":
      return { ...msg, isThinking: true, thinkingContent: event.content };

    case "reasoning": {
      const delta = event.content ?? "";
      if (!delta) return msg;
      const existing = msg.reasoningContent ?? "";
      return {
        ...msg,
        reasoningContent: joinReasoning(existing, delta, ctx.breakBefore),
        trace: extendTrace(msg.trace, "reasoning", delta, ctx.breakBefore),
      };
    }

    case "content": {
      const delta = event.content ?? "";
      if (!delta) return msg;
      return {
        ...msg,
        content: joinContent(msg.content, delta, ctx.breakBefore),
        isThinking: false,
        trace: extendTrace(msg.trace, "text", delta, ctx.breakBefore),
      };
    }

    case "memory_match":
      return {
        ...msg,
        memoryMatches: [
          ...(msg.memoryMatches ?? []),
          { id: event.memory_id, type: event.memory_type, preview: event.content_preview, score: event.score },
        ],
      };

    case "tool_args_delta": {
      if (!event.delta) return msg;
      const callId = event.call_id || event.tool_name || "pending";
      const existing = msg.pendingToolArgs?.[callId];
      return {
        ...msg,
        isThinking: false,
        pendingToolArgs: {
          ...(msg.pendingToolArgs ?? {}),
          [callId]: { name: existing?.name || event.tool_name || "", args: (existing?.args ?? "") + event.delta },
        },
      };
    }

    case "progress": {
      if (!event.step || !event.status || !event.message) return msg;
      const stepId = `${event.step}-${event.tool_name || "main"}`;
      const step: ProgressStep = {
        id: stepId,
        step: event.step,
        status: event.status as ProgressStep["status"],
        message: event.message,
        timestamp: ctx.now,
        count: event.count,
        tool_name: event.tool_name,
      };
      const steps = (msg.progressSteps ?? []).slice();
      const i = steps.findIndex((s) => s.id === stepId);
      if (i >= 0) steps[i] = step;
      else steps.push(step);
      const next: Message = { ...msg, progressSteps: steps };
      if (event.step === "memory_search") {
        next.trace = withMemoryTrace(msg.trace, step.status, event.count);
      }
      return next;
    }

    case "tool_start": {
      const tool = event.tool_name;
      if (!tool) return msg;
      const pendingToolArgs = withoutPending(msg.pendingToolArgs, tool);
      if (ctx.codeBlockId) {
        const block: CodeBlock = { id: ctx.codeBlockId, code: "", language: "python", isExecuting: true };
        return {
          ...msg,
          pendingToolArgs,
          codeBlocks: [...(msg.codeBlocks ?? []), block],
          trace: [...(msg.trace ?? []), { kind: "code", id: block.id }],
        };
      }
      const activity: ToolActivity = {
        id: ctx.activityId || `tool-${msg.id}-${ctx.seq}-${tool}`,
        tool_name: tool,
        status: "running",
        args_summary: event.args_summary,
        timestamp: ctx.now,
      };
      return {
        ...msg,
        pendingToolArgs,
        toolActivities: [...(msg.toolActivities ?? []), activity],
        trace: [...(msg.trace ?? []), { kind: "tool", id: activity.id }],
      };
    }

    case "code":
      if (!event.code) return msg;
      return updateCode(msg, ctx.codeBlockId, (b) => ({ ...b, code: event.code!, language: event.language || "python" }));

    case "execution_output":
      if (!event.output) return msg;
      return updateCode(msg, ctx.codeBlockId, (b) => ({ ...b, output: (b.output || "") + event.output }));

    case "execution_result":
      return updateCode(msg, ctx.codeBlockId, (b) => ({
        ...b,
        success: event.success,
        duration_ms: event.duration_ms,
        isExecuting: false,
      }));

    case "tool_end": {
      let next: Message = msg.isThinking ? { ...msg, isThinking: false } : msg;
      const tool = event.tool_name;
      if (!tool) return next;
      if (EXECUTION_TOOLS.has(tool)) {
        return updateCode(next, ctx.codeBlockId, (b) =>
          b.isExecuting ? { ...b, isExecuting: false, success: b.success ?? !isToolFailure(event.result) } : b,
        );
      }
      const activities = next.toolActivities;
      if (!activities) return next;
      for (let i = activities.length - 1; i >= 0; i--) {
        const a = activities[i];
        if (a.tool_name === tool && a.status === "running") {
          const list = activities.slice();
          list[i] = {
            ...a,
            status: isToolFailure(event.result) ? "error" : "completed",
            result: event.result,
            duration_ms: Math.max(0, ctx.now - a.timestamp),
          };
          next = { ...next, toolActivities: list };
          break;
        }
      }
      return next;
    }

    case "plan_created": {
      if (!event.plan_steps) return msg;
      const planBlock: PlanBlock = { id: `plan-${msg.id}`, steps: toPlanSteps(event.plan_steps), isComplete: false };
      return { ...msg, planBlock };
    }

    case "plan_step_update": {
      if (!event.step_id || !msg.planBlock) return msg;
      const steps = msg.planBlock.steps.map((s) =>
        s.id === event.step_id
          ? { ...s, status: (event.step_status || s.status) as PlanStep["status"], result: event.step_result ?? s.result }
          : s,
      );
      return { ...msg, planBlock: { ...msg.planBlock, steps } };
    }

    case "plan_updated":
      if (!event.plan_steps || !msg.planBlock) return msg;
      return { ...msg, planBlock: { ...msg.planBlock, steps: toPlanSteps(event.plan_steps) } };

    case "plan_completed": {
      if (!msg.planBlock) return msg;
      const steps = event.plan_steps
        ? toPlanSteps(event.plan_steps)
        : msg.planBlock.steps.map((s) => ({ ...s, status: "completed" as const }));
      return { ...msg, planBlock: { ...msg.planBlock, steps, isComplete: true, summary: event.plan_summary } };
    }

    case "cc_session_start": {
      if (!event.task_id) return msg;
      const session: CCSession = { id: event.task_id, description: event.task_description || "", status: "running", events: [] };
      return {
        ...msg,
        ccSessions: [...(msg.ccSessions ?? []), session],
        trace: [...(msg.trace ?? []), { kind: "tool", id: session.id }],
      };
    }

    case "cc_text":
    case "cc_tool_use":
    case "cc_tool_result": {
      if (!event.task_id || !msg.ccSessions) return msg;
      const ccEvent: CCSessionEvent = {
        id: `cc-evt-${msg.id}-${ctx.seq}`,
        type: event.type === "cc_text" ? "text" : event.type === "cc_tool_use" ? "tool_use" : "tool_result",
        text: event.text,
        toolName: event.cc_tool_name,
        toolInput: event.tool_input,
        timestamp: ctx.now,
      };
      return {
        ...msg,
        ccSessions: msg.ccSessions.map((s) => (s.id === event.task_id ? { ...s, events: [...s.events, ccEvent] } : s)),
      };
    }

    case "cc_session_end": {
      if (!event.task_id || !msg.ccSessions) return msg;
      const status: CCSession["status"] = event.status === "completed" ? "completed" : "failed";
      return {
        ...msg,
        ccSessions: msg.ccSessions.map((s) =>
          s.id === event.task_id ? { ...s, status, resultSummary: event.result_summary } : s,
        ),
      };
    }

    default:
      return msg;
  }
}

/** A voice memo: the transcript and the stored file ids, once the server has them. */
export function applyUserMessageEvent(user: Message, event: StreamEvent): Message {
  const serverAtts = event.attachments || [];
  return {
    ...user,
    content: event.content || user.content,
    attachments: user.attachments?.map((att, i) => ({ ...att, file_id: serverAtts[i]?.file_id || att.file_id })),
  };
}

/**
 * The stream ended — whatever the ending, nothing on the message is still
 * "live": no thinking, no pending tool args, no tool running (it is
 * "stopped"), no code executing, no step spinning. `stopped` (the owner
 * pressed Stop) marks it interrupted; `error` keeps a partial answer with a marker
 * or shows the refusal / canned line; `cut` (closed without `done`) only
 * clears the flags — a recovery reload brings the server's copy.
 */
export function finishStreamMessage(
  msg: Message,
  outcome: StreamOutcome,
  opts: { refusal?: string } = {},
): Message {
  const next: Message = { ...msg, isThinking: false, pendingToolArgs: undefined };
  if (msg.toolActivities?.some((a) => a.status === "running")) {
    next.toolActivities = msg.toolActivities.map((a) => (a.status === "running" ? { ...a, status: "stopped" } : a));
  }
  if (msg.codeBlocks?.some((b) => b.isExecuting)) {
    next.codeBlocks = msg.codeBlocks.map((b) => (b.isExecuting ? { ...b, isExecuting: false } : b));
  }
  if (msg.progressSteps?.some((s) => s.status === "started")) {
    next.progressSteps = msg.progressSteps.map((s) => (s.status === "started" ? { ...s, status: "stopped" } : s));
  }
  if (msg.trace?.some((t) => t.kind === "memory" && t.status === "started")) {
    next.trace = msg.trace.map((t) => (t.kind === "memory" && t.status === "started" ? { ...t, status: "stopped" } : t));
  }
  if (outcome === "stopped") next.wasInterrupted = true;
  if (outcome === "error") {
    next.content = contentForStreamError({ streamed: msg.content || "", refusal: opts.refusal });
    // A refusal the server worded is his answer, not a cut.
    const refused = !(msg.content || "").trim() && Boolean(opts.refusal?.trim());
    if (!refused) next.wasCutOff = true;
  }
  return next;
}

/** Replace one message by id — the same array when it is not there (not on screen). */
export function replaceMessage(
  messages: Message[],
  id: string,
  next: Message | ((m: Message) => Message),
): Message[] {
  const i = messages.findIndex((m) => m.id === id);
  if (i < 0) return messages;
  const value = typeof next === "function" ? next(messages[i]) : next;
  if (value === messages[i]) return messages;
  const out = messages.slice();
  out[i] = value;
  return out;
}
