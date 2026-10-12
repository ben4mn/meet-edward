// Loading a conversation into the chat (2026-09-25): the server's messages
// → Message[], the work rail rebuilt from each turn's stored `trace`, a
// small LRU of recently opened threads so a sidebar tap renders at once,
// and the merge that keeps a still-streaming turn on the end of a thread
// that is re-opened mid-answer. Pure, so it runs under node --test.

import type { Message, ProgressStep, ToolActivity, TraceItem } from "./ChatContext";
import type { Conversation, ConversationMessage, ConversationWithMessages } from "./api";

/** One tool of a stored turn (GET /api/conversations/{id}, assistant messages). */
export interface HistoryTraceTool {
  name: string;
  args_summary?: string | null;
  status: "ok" | "error";
  duration_ms?: number | null;
  result_preview?: string | null;
}

/**
 * The compact per-turn trace the backend stores beside an assistant message
 * (absent on turns older than it): what the rail showed live.
 */
export interface HistoryTrace {
  reasoning?: string | null;
  tools?: HistoryTraceTool[] | null;
  memory_hits?: number | null;
  /** Tools past the backend's cap (MAX_TOOLS 40), present only when some were cut. Not shown yet. */
  tools_omitted?: number | null;
}

export type HistoryMessage = ConversationMessage & { trace?: HistoryTrace | null };

/** The rail's fields for one stored turn; `{}` when there is nothing to show. */
export function hydrateHistoryTrace(
  messageId: string,
  trace: HistoryTrace | null | undefined,
  content: string,
): Partial<Message> {
  if (!trace) return {};
  const out: Partial<Message> = {};
  const items: TraceItem[] = [];

  if (typeof trace.memory_hits === "number") {
    const n = trace.memory_hits;
    const step: ProgressStep = {
      id: "memory_search-main",
      step: "memory_search",
      status: "completed",
      message: n ? `Found ${n} memories` : "No relevant memories",
      timestamp: 0,
      count: n,
    };
    out.progressSteps = [step];
    items.push({ kind: "memory", status: "completed", count: n });
  }

  const reasoning = trace.reasoning?.trim() ? trace.reasoning : null;
  if (reasoning) {
    out.reasoningContent = reasoning;
    items.push({ kind: "reasoning", text: reasoning });
  }

  const tools = trace.tools ?? [];
  if (tools.length) {
    out.toolActivities = tools.map((t, j): ToolActivity => {
      const a: ToolActivity = {
        id: `${messageId}-tool-${j}`,
        tool_name: t.name,
        status: t.status === "error" ? "error" : "completed",
        timestamp: 0,
      };
      if (t.args_summary) a.args_summary = t.args_summary;
      if (t.result_preview) a.result = t.result_preview;
      if (typeof t.duration_ms === "number") a.duration_ms = t.duration_ms;
      return a;
    });
    for (const a of out.toolActivities) items.push({ kind: "tool", id: a.id });
  }

  if (!items.length) return {};
  if (content.trim()) items.push({ kind: "text", text: content });
  out.trace = items;
  return out;
}

/** GET /api/conversations/{id} → the chat's messages (ids `${conversationId}-${index}`). */
export function mapHistoryMessages(conversationId: string, data: ConversationWithMessages): Message[] {
  const mapped = (data.messages as HistoryMessage[]).map((msg, index) => {
    const id = `${conversationId}-${index}`;
    const message: Message = { id, role: msg.role as Message["role"], content: msg.content };
    if (msg.attachments && msg.attachments.length > 0) {
      message.attachments = msg.attachments.map((att, i) => ({
        id: `${id}-att-${i}`,
        file_id: att.file_id,
        filename: att.filename,
        mime_type: att.mime_type,
        size: att.size || 0,
      }));
    }
    if (msg.is_trigger) {
      message.isTrigger = true;
      message.triggerType = msg.trigger_type;
    }
    if (message.role === "assistant" && msg.trace) {
      Object.assign(message, hydrateHistoryTrace(id, msg.trace, msg.content || ""));
    }
    return message;
  });

  if (data.cc_sessions && data.cc_sessions.length > 0) {
    for (let i = mapped.length - 1; i >= 0; i--) {
      if (mapped[i].role === "assistant") {
        mapped[i] = {
          ...mapped[i],
          ccSessions: data.cc_sessions.map((s) => ({
            id: s.task_id,
            description: s.description,
            status: s.status === "completed" ? "completed" : "failed",
            events: [],
            resultSummary: s.result_summary || undefined,
          })),
        };
        break;
      }
    }
  }
  return mapped;
}

/** The turn streaming right now for a conversation: its question and its answer so far. */
export interface LiveTurn {
  user: Message;
  assistant: Message;
  /**
   * How many of the owner's messages the thread held when this turn was sent.
   * The server's copy can only contain the live question once it holds
   * more than that (the checkpoint is written at the end of a turn).
   * Users, not messages: the server may split one assistant bubble in two
   * (text beside tool calls), but keeps one human message per turn.
   */
  baseUserCount?: number;
}

/**
 * A thread re-opened while its turn still streams: the live question and
 * answer go on the end, replacing any copy of them (a cached snapshot with
 * the same ids, or the server's saved question / half answer).
 *
 * The server's trailing question is only taken for the live one when the
 * list is not a snapshot that already held the live ids, and the server
 * holds more of the owner's messages than when the turn began — otherwise a
 * repeated "continue" stripped the *previous* "continue" and its answer
 * (2026-09-26 review).
 */
export function withLiveTurn(messages: Message[], live: LiveTurn | null | undefined): Message[] {
  if (!live) return messages;
  const liveIds = new Set([live.user.id, live.assistant.id]);
  let list = messages.filter((m) => !liveIds.has(m.id));
  const fromSnapshot = list.length !== messages.length;
  const users = list.filter((m) => m.role === "user").length;
  const serverHasIt = live.baseUserCount === undefined || users > live.baseUserCount;
  if (!fromSnapshot && serverHasIt) {
    const n = list.length;
    const same = (m: Message | undefined) => m?.role === "user" && m.content.trim() === live.user.content.trim();
    if (same(list[n - 1])) list = list.slice(0, n - 1);
    else if (same(list[n - 2]) && list[n - 1]?.role === "assistant") list = list.slice(0, n - 2);
  }
  return [...list, live.user, live.assistant];
}

/** Object-URL previews (blob:) still referenced by any of these message lists. */
export function referencedBlobUrls(lists: readonly (readonly Message[])[]): Set<string> {
  const out = new Set<string>();
  for (const list of lists) {
    for (const m of list) {
      for (const a of m.attachments ?? []) {
        if (a.preview_url && a.preview_url.startsWith("blob:")) out.add(a.preview_url);
      }
    }
  }
  return out;
}

export interface CachedConversation {
  conversation: Conversation | null;
  messages: Message[];
}

/** The last few opened conversations, most recent last (a Map keeps insertion order). */
export class ConversationCache {
  private entries = new Map<string, CachedConversation>();

  constructor(private readonly max = 8) {}

  get size(): number {
    return this.entries.size;
  }

  get(id: string): CachedConversation | undefined {
    const e = this.entries.get(id);
    if (!e) return undefined;
    this.entries.delete(id);
    this.entries.set(id, e);
    return e;
  }

  set(id: string, entry: CachedConversation): void {
    this.entries.delete(id);
    this.entries.set(id, entry);
    while (this.entries.size > this.max) {
      const oldest = this.entries.keys().next().value as string;
      this.entries.delete(oldest);
    }
  }

  /** Edit a cached thread (a turn that finished off screen) without making it recent. */
  patch(id: string, fn: (entry: CachedConversation) => CachedConversation): void {
    const e = this.entries.get(id);
    if (e) this.entries.set(id, fn(e));
  }

  delete(id: string): void {
    this.entries.delete(id);
  }

  /** Every cached thread's messages (for what still references a preview). */
  messageLists(): Message[][] {
    return Array.from(this.entries.values(), (e) => e.messages);
  }
}
