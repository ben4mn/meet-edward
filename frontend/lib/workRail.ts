// Pure logic for the chat work rail: what Edward did during a turn
// (thinking, memory search, tools, code) summarised to one line, the live
// status while it runs, and whether the rail is open given the user's
// preference. Kept free of React so it runs under node --test.

import type {
  CodeBlock,
  ProgressStep,
  ToolActivity,
  CCSession,
  PlanBlock,
  TraceItem,
} from "@/lib/ChatContext";
import { isHeldForApproval, isOutwardAct, isOutwardTool, isToolFailure, type Turn } from "./turnModel";
import { describeTool, failedPhrase, pendingArgsSummary, receiptPhrase, tallyKey } from "./toolLabels";

export type WorkPref = "collapse" | "open" | "tap";

export const WORK_PREF_KEY = "edward:workDisplay";

export const WORK_PREF_OPTIONS: { value: WorkPref; label: string; detail: string }[] = [
  { value: "collapse", label: "Live, then folded", detail: "Thinking and tools stream open while he works, then fold to one line you can reopen." },
  { value: "open", label: "Always open", detail: "The full trace stays expanded on every turn." },
  { value: "tap", label: "Only on tap", detail: "One quiet status line while he works. Tap to see the trace." },
];

export function parseWorkPref(v: unknown): WorkPref {
  return v === "open" || v === "tap" || v === "collapse" ? v : "collapse";
}

/** The slice of a chat Message the rail cares about. */
export interface WorkMessage {
  role: "user" | "assistant";
  content: string;
  isThinking?: boolean;
  thinkingContent?: string;
  reasoningContent?: string;
  progressSteps?: ProgressStep[];
  toolActivities?: ToolActivity[];
  codeBlocks?: CodeBlock[];
  ccSessions?: CCSession[];
  planBlock?: PlanBlock;
  pendingToolArgs?: Record<string, { name: string; args: string }>;
  wasInterrupted?: boolean;
  /** The turn in arrival order (live, or rebuilt from the history trace). */
  trace?: TraceItem[];
}

export interface WorkSummary {
  hasWork: boolean;
  thinking: boolean;
  memories: number;
  tools: number;
  failed: number;
  /** Tools the stream ended under (Stop, a cut, an error): neither ok nor failed. */
  stopped: number;
  label: string;
}

export function summarizeWork(m: WorkMessage): WorkSummary {
  const thinking = Boolean(m.reasoningContent && m.reasoningContent.trim());
  const memStep = (m.progressSteps ?? []).find((s) => s.step === "memory_search");
  const memories = memStep?.count ?? 0;
  const activities = m.toolActivities ?? [];
  const code = m.codeBlocks ?? [];
  const cc = m.ccSessions ?? [];
  const tools = activities.length + code.length + cc.length;
  const failed =
    activities.filter((a) => a.status === "error").length +
    code.filter((c) => c.success === false).length +
    cc.filter((s) => s.status === "failed").length;
  const stopped = activities.filter((a) => a.status === "stopped").length;
  const plan = Boolean(m.planBlock);
  const hasWork = thinking || memories > 0 || tools > 0 || plan || Boolean(memStep);

  const parts: string[] = [];
  if (thinking) parts.push("Thought");
  if (memStep) parts.push("searched memory");
  if (tools) {
    const notes = [failed ? `${failed} failed` : "", stopped ? `${stopped} stopped` : ""].filter(Boolean);
    parts.push(`${tools} tool${tools === 1 ? "" : "s"}${notes.length ? `, ${notes.join(", ")}` : ""}`);
  }
  if (plan) parts.push("planned");
  return { hasWork, thinking, memories, tools, failed, stopped, label: parts.join(" · ") };
}

/**
 * What the header says while the turn is live — capitalised, in Edward's
 * words ("Checking your calendar…", "Opening nws.gov…"). With the turn
 * (ChatContext's `turn`, when it streams into this message) the phase
 * decides — so text already on screen between two tool rounds reads
 * "Thinking…", not "Writing…" — and the reasoning heading names what he is
 * thinking about. Without it (or once it ended) the message's own fields decide.
 *
 * `timelineOpen` (2026-09-26 review): beside an open timeline the running
 * node already names the step ("Searching memory…", "Running Python…"), so
 * the header never says it again — it says what he is thinking about (the
 * reasoning heading) or simply "Working…". Folded, it names the step.
 */
export function liveStatus(m: WorkMessage, turn?: Turn | null, opts: { timelineOpen?: boolean } = {}): string {
  const open = Boolean(opts.timelineOpen);
  switch (turn?.phase) {
    case "recalling":
      return open ? "Checking what I know…" : "Searching memory…";
    case "thinking":
      return turn.reasoningHeading ? headingStatus(turn.reasoningHeading) : "Thinking…";
    case "working":
      if (open) return turn.reasoningHeading ? headingStatus(turn.reasoningHeading) : "Working…";
      return workingStatus(m) ?? "Working…";
    case "writing":
      return "Writing…";
    default:
      break;
  }
  const working = workingStatus(m);
  if (working) return working;
  const step = (m.progressSteps ?? []).find((s) => s.status === "started");
  if (step && step.step === "memory_search") return "Searching memory…";
  if (m.isThinking) return "Thinking…";
  if (m.content) return "Writing…";
  return "Thinking…";
}

function headingStatus(heading: string): string {
  const h = heading.trim().replace(/[.…:;,!]+$/, "");
  return `${h.charAt(0).toUpperCase()}${h.slice(1)}…`;
}

/** The message a turn streams into is the live one — never "content is empty". */
export function isLiveMessage(messageId: string, streamingMessageId: string | null | undefined): boolean {
  return Boolean(streamingMessageId) && messageId === streamingMessageId;
}

const LANGUAGE_TOOL: Record<string, string> = {
  python: "execute_code",
  javascript: "execute_javascript",
  js: "execute_javascript",
  sql: "execute_sql",
  // Edward's shell (2026-10-11) emits bash code blocks; the old sandbox's
  // receipts still name execute_shell.
  bash: "bash",
  sh: "bash",
  shell: "bash",
};

/** The execution tool a code block came from (the block keeps only its language). */
export function codeToolName(language: string | undefined): string {
  return LANGUAGE_TOOL[(language || "python").toLowerCase()] ?? "execute_code";
}

function workingStatus(m: WorkMessage): string | null {
  const running = (m.toolActivities ?? []).find((a) => a.status === "running");
  if (running) return describeTool(running.tool_name, running.args_summary).live;
  const exec = (m.codeBlocks ?? []).find((c) => c.isExecuting);
  if (exec) return describeTool(codeToolName(exec.language)).live;
  const cc = (m.ccSessions ?? []).find((s) => s.status === "running");
  if (cc) return describeTool("spawn_cc_worker").live;
  const pending = m.pendingToolArgs ? Object.values(m.pendingToolArgs)[0] : undefined;
  if (pending) return describeTool(pending.name || "tool", pendingArgsSummary(pending.name || "tool", pending.args)).live;
  return null;
}

/**
 * Open/closed given preference, liveness and any manual toggle.
 *
 * `answering` (2026-09-26 review): the answer has begun on a live turn.
 * Under "collapse" the timeline folds right then — above empty space,
 * before there is text to read — instead of at `done`, when the fold yanked
 * the answer the owner was reading up by the timeline's height. The answer text
 * only grows, so once folded it stays folded for the rest of the turn (a
 * preamble followed by more tools does not make it flap); the live header
 * keeps naming each step.
 */
export function isRailOpen(pref: WorkPref, live: boolean, userToggled: boolean | null, answering = false): boolean {
  if (userToggled !== null) return userToggled;
  if (pref === "open") return true;
  if (pref === "tap") return false;
  return live && !answering;
}

// ---------------------------------------------------------------------------
// The timeline (2026-09-26): one surface, in arrival order — memory search,
// each reasoning step, tools, code, plans, helper sessions — the same for a
// live turn and one rebuilt from history (lib/chatHistory.ts).
// ---------------------------------------------------------------------------

export interface ReasoningStep {
  /** The Codex summary part's **Heading**, or null for text before the first one. */
  title: string | null;
  body: string;
}

const HEADING_LINE = /^[ \t]*\*\*([^*\n]{1,160}?)\*\*[ \t]*$/;
const OPEN_HEADING = /^[ \t]*\*\*([^*\n]{0,160})$/;

/** A reasoning summary → steps, split on bold headings that stand on their own line. */
export function reasoningSteps(text: string): ReasoningStep[] {
  const steps: ReasoningStep[] = [];
  let title: string | null = null;
  let body: string[] = [];
  let started = false;
  const flush = () => {
    const b = body.join("\n").trim();
    if (started || b) steps.push({ title, body: b });
  };
  const lines = (text || "").split("\n");
  lines.forEach((line, i) => {
    const heading = line.match(HEADING_LINE);
    const open = i === lines.length - 1 ? line.match(OPEN_HEADING) : null;
    if (heading || (open && open[1].trim())) {
      flush();
      title = (heading ? heading[1] : open![1]).trim();
      body = [];
      started = true;
      return;
    }
    body.push(line);
  });
  flush();
  return steps.filter((s) => s.title || s.body);
}

export type TimelineNode =
  | { kind: "memory"; key: string; status: "running" | "done" | "error" | "stopped"; count?: number }
  | { kind: "reasoning"; key: string; title: string | null; body: string; latest: boolean }
  | { kind: "tool"; key: string; activity: ToolActivity }
  | { kind: "code"; key: string; block: CodeBlock }
  | { kind: "session"; key: string; session: CCSession }
  | { kind: "plan"; key: string; plan: PlanBlock }
  | { kind: "pending"; key: string; name: string; args: string }
  | { kind: "exhausted"; key: string; message: string };

const PLAN_TOOLS = new Set(["create_plan", "update_plan_step", "edit_plan", "complete_plan"]);

function memoryStatus(status: string | undefined): "running" | "done" | "error" | "stopped" {
  if (status === "started") return "running";
  if (status === "error") return "error";
  if (status === "stopped") return "stopped";
  return "done";
}

/** Everything the turn did, in the order it happened. */
export function buildTimeline(m: WorkMessage): TimelineNode[] {
  const activities = m.toolActivities ?? [];
  const blocks = m.codeBlocks ?? [];
  const sessions = m.ccSessions ?? [];
  const memStep = (m.progressSteps ?? []).find((s) => s.step === "memory_search");
  const out: TimelineNode[] = [];
  const used = new Set<string>();
  let r = 0;

  const pushReasoning = (text: string) => {
    for (const step of reasoningSteps(text)) {
      out.push({ kind: "reasoning", key: `r-${r++}`, title: step.title, body: step.body, latest: false });
    }
  };
  const pushTool = (id: string) => {
    if (used.has(id)) return;
    const a = activities.find((x) => x.id === id);
    if (a) {
      used.add(id);
      out.push({ kind: "tool", key: a.id, activity: a });
      return;
    }
    const s = sessions.find((x) => x.id === id);
    if (s) {
      used.add(id);
      out.push({ kind: "session", key: `s-${s.id}`, session: s });
    }
  };
  const pushCode = (id: string) => {
    if (used.has(id)) return;
    const b = blocks.find((x) => x.id === id);
    if (b) {
      used.add(id);
      out.push({ kind: "code", key: b.id, block: b });
    }
  };

  const trace = (m.trace ?? []).filter((t) => t.kind !== "text");
  let sawMemory = false;
  if (trace.length) {
    for (const item of trace) {
      if (item.kind === "memory") {
        if (sawMemory) continue;
        sawMemory = true;
        out.push({ kind: "memory", key: "memory", status: memoryStatus(item.status), count: item.count });
      } else if (item.kind === "reasoning") pushReasoning(item.text);
      else if (item.kind === "tool") pushTool(item.id);
      else if (item.kind === "code") pushCode(item.id);
    }
  } else {
    if (memStep) {
      sawMemory = true;
      out.push({ kind: "memory", key: "memory", status: memoryStatus(memStep.status), count: memStep.count });
    }
    if (m.reasoningContent?.trim()) pushReasoning(m.reasoningContent);
  }
  // Anything the trace did not mention (older shapes) still shows, in its own order.
  if (!sawMemory && memStep) {
    out.unshift({ kind: "memory", key: "memory", status: memoryStatus(memStep.status), count: memStep.count });
  }
  for (const a of activities) pushTool(a.id);
  for (const b of blocks) pushCode(b.id);
  for (const s of sessions) pushTool(s.id);

  // A plan is one node where it was made; its step updates live inside it.
  let nodes = out;
  if (m.planBlock) {
    const plan = m.planBlock;
    let placed = false;
    nodes = [];
    for (const n of out) {
      if (n.kind === "tool" && PLAN_TOOLS.has(n.activity.tool_name)) {
        if (!placed) {
          nodes.push({ kind: "plan", key: "plan", plan });
          placed = true;
        }
        continue;
      }
      nodes.push(n);
    }
    if (!placed) nodes.push({ kind: "plan", key: "plan", plan });
  }
  // A helper's session carries its own status and log; its spawn call is the same work.
  if (sessions.length) {
    nodes = nodes.filter((n) => !(n.kind === "tool" && n.activity.tool_name === "spawn_cc_worker"));
  }

  for (const [id, info] of Object.entries(m.pendingToolArgs ?? {})) {
    nodes.push({ kind: "pending", key: `p-${id}`, name: info.name || "tool", args: info.args });
  }
  const exhausted = (m.progressSteps ?? []).find((s) => s.step === "tool_loop" && (s.status as string) === "exhausted");
  if (exhausted) nodes.push({ kind: "exhausted", key: "exhausted", message: exhausted.message });

  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i];
    if (n.kind === "reasoning") {
      nodes[i] = { ...n, latest: true };
      break;
    }
  }
  return nodes;
}

// ---------------------------------------------------------------------------
// The receipt: what the rail folds to when the turn is done.
//   "Thought for 14s · texted Parker · searched memory · opened 2 pages"
// Outward acts come right after the lead (they are what the owner must never miss)
// and only a send the tool says went out counts as one.
// ---------------------------------------------------------------------------

export type ReceiptTone = "lead" | "outward" | "plain" | "muted" | "warn" | "bad";

export interface ReceiptPart {
  text: string;
  tone: ReceiptTone;
}

/** 400 → "1s", 14 200 → "14s", 65 000 → "1m 5s", 120 000 → "2m". */
export function formatElapsed(ms: number): string {
  const total = Math.max(1, Math.round(ms / 1000));
  if (total < 60) return `${total}s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

const MAX_PLAIN_PARTS = 3;
const MEMORY_KEY = "memory";

interface Group {
  name: string;
  args: (string | undefined)[];
}

export function workReceipt(m: WorkMessage, opts: { elapsedMs?: number | null } = {}): ReceiptPart[] {
  const parts: ReceiptPart[] = [];
  const outward: ReceiptPart[] = [];
  const plain: ReceiptPart[] = [];
  const groups = new Map<string, Group>();
  const order: string[] = [];
  const failures = new Map<string, Group>();
  const failOrder: string[] = [];
  let stopped = 0;
  let held = 0;
  let searchedMemory = (m.progressSteps ?? []).some((s) => s.step === "memory_search");

  const add = (name: string, arg: string | undefined) => {
    const key = tallyKey(name, arg);
    if (key === MEMORY_KEY) {
      searchedMemory = true;
      return;
    }
    const g = groups.get(key);
    if (g) g.args.push(arg);
    else {
      groups.set(key, { name, args: [arg] });
      order.push(key);
    }
  };

  // What failed is named ("couldn't check your calendar"), never counted as done.
  const fail = (name: string, arg: string | undefined) => {
    const key = tallyKey(name, arg);
    const g = failures.get(key);
    if (g) g.args.push(arg);
    else {
      failures.set(key, { name, args: [arg] });
      failOrder.push(key);
    }
  };

  for (const a of m.toolActivities ?? []) {
    if (a.status === "error" || isToolFailure(a.result)) {
      fail(a.tool_name, a.args_summary);
      continue;
    }
    if (a.status === "stopped") {
      stopped++;
      if (!isOutwardTool(a.tool_name)) add(a.tool_name, a.args_summary);
      continue;
    }
    if (isOutwardTool(a.tool_name) && a.status === "completed" && !isOutwardAct(a.tool_name, a.result)) {
      if (isHeldForApproval(a.result)) held++;
      continue; // held, or a duplicate refused: nothing went out
    }
    add(a.tool_name, a.args_summary);
  }
  for (const b of m.codeBlocks ?? []) {
    if (b.success === false) fail(codeToolName(b.language), undefined);
    else add(codeToolName(b.language), undefined);
  }
  for (const s of m.ccSessions ?? []) {
    if (s.status === "failed") fail("spawn_cc_worker", undefined);
    else if (!(m.toolActivities ?? []).some((a) => a.tool_name === "spawn_cc_worker")) add("spawn_cc_worker", undefined);
  }
  if (m.planBlock && !groups.has(tallyKey("create_plan"))) add("create_plan", undefined);

  const thought = Boolean(m.reasoningContent && m.reasoningContent.trim());
  const elapsed = typeof opts.elapsedMs === "number" && opts.elapsedMs > 0 ? formatElapsed(opts.elapsedMs) : null;
  if (thought) parts.push({ text: elapsed ? `Thought for ${elapsed}` : "Thought", tone: "lead" });
  else if (elapsed && (groups.size || failures.size || searchedMemory)) parts.push({ text: `Worked for ${elapsed}`, tone: "lead" });

  for (const key of order) {
    const g = groups.get(key)!;
    const text = receiptPhrase(g.name, g.args);
    if (isOutwardTool(g.name)) outward.push({ text, tone: "outward" });
    else plain.push({ text, tone: "plain" });
  }
  if (searchedMemory) plain.unshift({ text: "searched memory", tone: "plain" });

  parts.push(...outward);
  if (held) parts.push({ text: held === 1 ? "held a send for your approval" : `held ${held} sends for your approval`, tone: "warn" });
  if (plain.length > MAX_PLAIN_PARTS + 1) {
    parts.push(...plain.slice(0, MAX_PLAIN_PARTS), { text: `and ${plain.length - MAX_PLAIN_PARTS} more`, tone: "muted" });
  } else parts.push(...plain);
  if (stopped) parts.push({ text: `${stopped} stopped`, tone: "muted" });
  for (const key of failOrder) {
    const g = failures.get(key)!;
    parts.push({ text: failedPhrase(g.name, g.args), tone: "bad" });
  }
  // Nothing leads ("Thought for 9s", "Worked for 4s"): the line still starts
  // like a sentence, never with a lowercase verb that reads as a truncation.
  const first = parts[0];
  if (first && first.tone !== "lead") parts[0] = { ...first, text: first.text.charAt(0).toUpperCase() + first.text.slice(1) };
  return parts;
}

/** The receipt as one line (tests, the copy-trace text, aria labels). */
export function receiptText(parts: ReceiptPart[]): string {
  return parts.map((p) => p.text).join(" · ");
}
