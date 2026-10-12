// The chat turn model: the single answer to "what is Edward doing on this
// turn right now", driven only by stream events (never by whether the
// answer text happens to be empty — that made the work rail flap between
// tool rounds). Pure, so it runs under node --test; ChatContext holds one
// Turn and folds every SSE event through reduceTurn.
//
//   start ─► thinking ─► recalling (memory search) ─► thinking (reasoning)
//              │                                        │
//              └─► working (tool args / tool running) ◄─┘
//                        │ tool_end with nothing left running → thinking
//                        ▼
//                     writing (content deltas)
//   any end of the stream ─► done | stopped | error   (terminal, nothing running)

export type TurnPhase = "recalling" | "thinking" | "working" | "writing" | "done" | "stopped" | "error";

/** How a turn ended. */
export type TurnOutcome = "done" | "stopped" | "error";

/**
 * How the stream ended, from ChatContext's side: `done` (the server said so),
 * `stopped` (the owner pressed Stop), `cut` (the stream closed without `done` — a
 * hidden tab or a dropped connection; the server may still be finishing),
 * `error` (the request failed, or the server sent an `error` event).
 */
export type StreamOutcome = "done" | "stopped" | "cut" | "error";

export interface TurnTools {
  running: number;
  ok: number;
  failed: number;
}

export interface TurnToolEvent {
  /** Rises on every tool start/end in the turn, so a watcher can tell two
   *  consecutive successes of the same tool apart. */
  seq: number;
  tool: string;
  status: "running" | "ok" | "error";
  at: number;
}

export interface Turn {
  /** The assistant message this turn streams into. */
  id: string;
  /** Null until the first event of a brand-new chat names it. */
  conversationId: string | null;
  phase: TurnPhase;
  startedAt: number;
  endedAt: number | null;
  tools: TurnTools;
  lastToolEvent: TurnToolEvent | null;
  /** The latest **Heading** of the Codex reasoning summary ("Checking the calendar"). */
  reasoningHeading: string | null;
  /** The last ≤ 400 chars of reasoning, so a heading split across deltas still parses. */
  reasoningTail: string;
  /** An execution tool reports success in execution_result before its tool_end. */
  execOk: boolean | null;
  /** The backend ran out of tool iterations / time (progress tool_loop exhausted). */
  exhausted: boolean;
  /** Successful outward acts this turn — a send, a push, a note, a hosted page
   *  (the thought journal's OUTWARD tier). A held or repeated send is not one. */
  outward: number;
}

/**
 * The slice of a StreamEvent the turn reads. Structural and without an index
 * signature, so lib/api.ts's StreamEvent (an interface) is assignable to it.
 */
export interface TurnEvent {
  type: string;
  conversation_id?: string;
  content?: string;
  tool_name?: string;
  args_summary?: string;
  result?: string;
  success?: boolean;
  duration_ms?: number;
  code?: string;
  language?: string;
  step?: string;
  status?: string;
  message?: string;
  count?: number;
  task_id?: string;
  call_id?: string;
  delta?: string;
}

export const EXECUTION_TOOLS: ReadonlySet<string> = new Set([
  "bash", // Edward's shell (2026-10-11): code / execution_output / execution_result like the sandboxes it replaced
  "execute_code",
  "execute_javascript",
  "execute_sql",
  "execute_shell",
]);

/** The backend's OUTWARD tier (services/thought_journal.py): it reaches a person or a surface. */
export const OUTWARD_TOOLS: ReadonlySet<string> = new Set([
  "send_imessage",
  "send_message",
  "send_sms",
  "send_whatsapp",
  "send_push_notification",
  "leave_note",
  "tell_owner", // a note on Today or a push (2026-10-11)
  "create_hosted_page",
  "update_hosted_page",
  "browser_type",
]);
const OUTWARD_PREFIXES = ["whatsapp_send"];
/** A send the taint gate held: the backend answers "Waiting for <owner>'s
 *  approval (#id)" (services/approvals_service.py WAITING_PREFIX, rendered
 *  with the owner's name, or "the owner" when none is configured). */
const HELD_FOR_APPROVAL = /^waiting for [^\n]{1,80}?'s approval/i;

/** The tool's reply says the send is held for the owner's approval. */
export function isHeldForApproval(result: string | undefined | null): boolean {
  return HELD_FOR_APPROVAL.test((result ?? "").trim());
}

/** Replies that mean nothing went out: held by the taint gate, or a duplicate refused. */
const NOT_SENT_PREFIXES = ["already sent"];

const TAIL_CHARS = 400;
const FAILURE_PREFIXES = ["Tool error:", "Unknown tool:", "BLOCKED:"];

/** A tool result the backend counts as a failure (the turn ledger's rule). */
export function isToolFailure(result: string | undefined | null): boolean {
  if (!result) return false;
  return FAILURE_PREFIXES.some((p) => result.startsWith(p));
}

export function isOutwardTool(tool: string): boolean {
  return OUTWARD_TOOLS.has(tool) || OUTWARD_PREFIXES.some((p) => tool.startsWith(p));
}

/** A finished tool call that reached someone (the tool's own reply says it went). */
export function isOutwardAct(tool: string, result: string | undefined | null): boolean {
  if (!isOutwardTool(tool) || isToolFailure(result)) return false;
  if (isHeldForApproval(result)) return false;
  const text = (result ?? "").trimStart().toLowerCase();
  return !NOT_SENT_PREFIXES.some((p) => text.startsWith(p));
}

export function isTerminalPhase(phase: TurnPhase): boolean {
  return phase === "done" || phase === "stopped" || phase === "error";
}

export function isTurnLive(turn: Turn | null | undefined): boolean {
  return Boolean(turn) && !isTerminalPhase(turn!.phase);
}

export function turnOutcomeFor(outcome: StreamOutcome): TurnOutcome {
  if (outcome === "cut") return "stopped";
  return outcome;
}

export function startTurn(id: string, conversationId: string | null | undefined, now: number): Turn {
  return {
    id,
    conversationId: conversationId || null,
    phase: "thinking",
    startedAt: now,
    endedAt: null,
    tools: { running: 0, ok: 0, failed: 0 },
    lastToolEvent: null,
    reasoningHeading: null,
    reasoningTail: "",
    execOk: null,
    exhausted: false,
    outward: 0,
  };
}

const LINE_HEADING = /(?:^|\n)[ \t]*\*\*([^*\n]{1,120}?)\*\*/g;
const ANY_BOLD = /\*\*([^*\n]{1,120}?)\*\*/g;

function lastMatch(re: RegExp, text: string): string | null {
  let found: string | null = null;
  re.lastIndex = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) found = m[1].trim() || found;
  return found;
}

/**
 * The latest complete **Heading** in a Codex reasoning summary. Summary parts
 * open with a bold heading on its own line; a heading still being streamed
 * ("**Draft") is ignored. Falls back to any bold run when parts were glued
 * together without a line break.
 */
export function latestReasoningHeading(text: string): string | null {
  if (!text) return null;
  return lastMatch(LINE_HEADING, text) ?? lastMatch(ANY_BOLD, text);
}

function withConversation(turn: Turn, event: TurnEvent): Turn {
  if (turn.conversationId || !event.conversation_id) return turn;
  return { ...turn, conversationId: event.conversation_id };
}

function toolEvent(turn: Turn, tool: string, status: TurnToolEvent["status"], now: number): TurnToolEvent {
  return { seq: (turn.lastToolEvent?.seq ?? 0) + 1, tool, status, at: now };
}

function finishTool(turn: Turn, tool: string, failed: boolean, now: number, outward = false): Turn {
  const running = Math.max(0, turn.tools.running - 1);
  return {
    ...turn,
    phase: running > 0 ? "working" : "thinking",
    tools: {
      running,
      ok: turn.tools.ok + (failed ? 0 : 1),
      failed: turn.tools.failed + (failed ? 1 : 0),
    },
    lastToolEvent: toolEvent(turn, tool, failed ? "error" : "ok", now),
    execOk: null,
    outward: turn.outward + (outward && !failed ? 1 : 0),
  };
}

function withPhase(turn: Turn, phase: TurnPhase): Turn {
  return turn.phase === phase ? turn : { ...turn, phase };
}

/** One stream event → the next turn. Returns the same object when nothing changed. */
export function reduceTurn(turn: Turn, event: TurnEvent, now: number): Turn {
  if (isTerminalPhase(turn.phase)) return turn;
  const t = withConversation(turn, event);

  switch (event.type) {
    case "progress": {
      if (event.step === "memory_search") {
        if (event.status === "started") return t.tools.running > 0 ? t : withPhase(t, "recalling");
        return t.phase === "recalling" ? withPhase(t, "thinking") : t;
      }
      if (event.step === "tool_loop" && event.status === "exhausted") {
        return t.exhausted ? t : { ...t, exhausted: true };
      }
      return t;
    }

    case "reasoning": {
      const delta = event.content ?? "";
      if (!delta) return t;
      const tail = (t.reasoningTail + delta).slice(-TAIL_CHARS);
      const heading = latestReasoningHeading(tail) ?? t.reasoningHeading;
      return {
        ...t,
        phase: t.tools.running > 0 ? t.phase : "thinking",
        reasoningTail: tail,
        reasoningHeading: heading,
      };
    }

    case "thinking":
      return t.tools.running > 0 ? t : withPhase(t, "thinking");

    case "tool_args_delta":
      return withPhase(t, "working");

    case "tool_start": {
      const tool = event.tool_name || "tool";
      return {
        ...t,
        phase: "working",
        tools: { ...t.tools, running: t.tools.running + 1 },
        lastToolEvent: toolEvent(t, tool, "running", now),
        execOk: EXECUTION_TOOLS.has(tool) ? null : t.execOk,
      };
    }

    case "execution_result":
      return { ...t, execOk: typeof event.success === "boolean" ? event.success : null };

    case "tool_end": {
      const tool = event.tool_name || "tool";
      const failed = EXECUTION_TOOLS.has(tool)
        ? t.execOk === false || isToolFailure(event.result)
        : isToolFailure(event.result);
      return finishTool(t, tool, failed, now, isOutwardAct(tool, event.result));
    }

    case "cc_session_start":
      return {
        ...t,
        phase: "working",
        tools: { ...t.tools, running: t.tools.running + 1 },
        lastToolEvent: toolEvent(t, "claude_code", "running", now),
      };

    case "cc_session_end":
      return finishTool(t, "claude_code", event.status !== "completed", now);

    case "content":
      return event.content ? withPhase(t, "writing") : t;

    case "done":
      return endTurn(t, "done", now);

    default:
      return t;
  }
}

/** The stream ended: a terminal phase, nothing left running. The first ending wins. */
export function endTurn(turn: Turn, outcome: TurnOutcome, now: number): Turn {
  if (isTerminalPhase(turn.phase)) return turn;
  return {
    ...turn,
    phase: outcome,
    endedAt: now,
    tools: { ...turn.tools, running: 0 },
    execOk: null,
  };
}
