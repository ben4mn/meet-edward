// Pure helpers for the display ComposeBar: key classification, the
// "open in inbox" deep link, and the canned quick-action prompts.
// React component owns ref / state / network.

export type ComposeKeyResult = "submit" | "clear" | "newline" | "ignore";

export function displayInboxHref(conversationId: string | null | undefined): string | null {
  if (!conversationId) return null;
  return `/chat?c=${encodeURIComponent(conversationId)}`;
}

export interface QuickAction {
  label: string;
  prompt: string;
}

export const QUICK_ACTIONS: readonly QuickAction[] = [
  { label: "What's next?", prompt: "What's next on my plate today?" },
  { label: "Today's summary", prompt: "Give me a quick summary of today so far." },
  { label: "Anything urgent?", prompt: "Anything urgent I should look at right now?" },
] as const;

interface ComposeKeyEvent {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  shiftKey?: boolean;
  preventDefault?: () => void;
}

export function classifyComposeKey(e: ComposeKeyEvent): ComposeKeyResult {
  if (e.key === "Enter") {
    if (e.shiftKey) {
      // Let the textarea handle the newline.
      return "newline";
    }
    e.preventDefault?.();
    return "submit";
  }
  if (e.key === "Escape") {
    e.preventDefault?.();
    return "clear";
  }
  return "ignore";
}

// ---- the reply card: what Edward is thinking, doing and saying ----
//
// Why (2026-09-07): the owner likes reading Edward's thoughts on /screen, but
// the card only showed the answer, grew off the top of the screen and could
// not scroll. The card now keeps the reasoning stream, the tools used and
// the answer apart, and the component draws it in a scrollable box that
// follows the tail while Edward works.

export interface ComposeState {
  thinking: string;
  reply: string;
  tools: string[];
}

export interface ComposeEventLike {
  type: string;
  content?: string;
  tool_name?: string;
}

export function initialComposeState(): ComposeState {
  return { thinking: "", reply: "", tools: [] };
}

export function applyComposeEvent(state: ComposeState, event: ComposeEventLike): ComposeState {
  switch (event.type) {
    case "reasoning":
      return { ...state, thinking: state.thinking + (event.content ?? "") };
    case "thinking": {
      const line = (event.content ?? "").trim();
      if (!line) return state;
      const sep = state.thinking && !state.thinking.endsWith("\n") ? "\n" : "";
      return { ...state, thinking: state.thinking + sep + line };
    }
    case "tool_start":
      return event.tool_name ? { ...state, tools: [...state.tools, event.tool_name] } : state;
    case "content":
      return { ...state, reply: state.reply + (event.content ?? "") };
    default:
      return state;
  }
}

/** A tool's name as words for the card's header ("web_search" → "Web search"), never snake_case. */
function toolWords(tool: string): string {
  const words = tool.replace(/^mcp_[a-z0-9]+_/, "").split(/[_\s]+/).filter(Boolean).join(" ");
  return words ? words[0].toUpperCase() + words.slice(1) : "A step";
}

export function composeCardStatus(state: ComposeState, busy: boolean): string {
  if (busy) {
    if (state.reply) return "answering…";
    const last = state.tools[state.tools.length - 1];
    return last ? `${toolWords(last)}…` : "thinking…";
  }
  const parts: string[] = [];
  if (state.thinking.trim()) parts.push("thought");
  if (state.tools.length) parts.push(`${state.tools.length} tool${state.tools.length === 1 ? "" : "s"}`);
  return parts.join(" · ") || "done";
}

// ---- the card, cleaned (round 2, 2026-09-28) ----
//
// The owner saw raw markdown in the card ("**Planning larger heartbeat call**",
// "**Today so far…**") under a stack of bare "Thinking..." lines. The
// thinking is cleaned into lines (a bold summary heading becomes its own
// line; the stream's "Thinking..." placeholders collapse away), and the
// answer's light markdown — **bold**, "- " / "* " bullets, blank-line
// paragraphs, headings as bold lines — becomes plain data the component
// draws as React nodes. No HTML strings, no markdown library on /screen.

export interface ThinkLine {
  text: string;
  /** A reasoning summary's bold heading ("**Planning…**" on its own line). */
  heading: boolean;
}

const PLACEHOLDER = /^Thinking(?:\.\.\.|…)$/;

export function thinkingLines(thinking: string): ThinkLine[] {
  let t = (thinking ?? "").replace(/\r/g, "");
  // The stream's placeholder, often glued to the summary after it ("Thinking...**Planning…**").
  let prev = "";
  while (prev !== t) {
    prev = t;
    t = t.replace(/(^|\n)[ \t]*Thinking(?:\.\.\.|…)[ \t]*/g, "$1\n");
  }
  // A summary's bold heading glued to the text before it ("…twice.**Deciding**").
  t = t.replace(/([^\s*])\*\*(?=[A-Z0-9][^*\n]*\*\*)/g, "$1\n**");
  const out: ThinkLine[] = [];
  for (const line of t.split("\n")) {
    const raw = line.trim();
    const heading = /^\*\*[^*]+\*\*$/.test(raw);
    const text = raw
      .replace(/^#{1,6}\s+/, "")
      .replace(/^[-*•]\s+/, "")
      .replace(/\*\*|__/g, "")
      .replace(/`/g, "")
      .trim();
    if (!text || PLACEHOLDER.test(text)) continue;
    if (out.length && out[out.length - 1].text === text) continue;
    out.push({ text, heading });
  }
  return out;
}

export interface ReplySpan {
  text: string;
  bold: boolean;
}

export type ReplyBlock = { kind: "p"; lines: ReplySpan[][] } | { kind: "ul"; items: ReplySpan[][] };

/** One line's spans: **bold** (a bold still being written is bold to the end), backticks dropped. */
function spans(line: string): ReplySpan[] {
  return line
    .replace(/`/g, "")
    .split("**")
    .map((text, i) => ({ text, bold: i % 2 === 1 }))
    .filter((s) => s.text);
}

export function replyBlocks(reply: string): ReplyBlock[] {
  const out: ReplyBlock[] = [];
  for (const block of (reply ?? "").replace(/\r/g, "").split(/\n[ \t]*\n/)) {
    let para: ReplySpan[][] = [];
    let list: ReplySpan[][] = [];
    const flushPara = () => {
      if (para.length) out.push({ kind: "p", lines: para });
      para = [];
    };
    const flushList = () => {
      if (list.length) out.push({ kind: "ul", items: list });
      list = [];
    };
    for (const line of block.split("\n")) {
      if (!line.trim()) continue;
      const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
      if (bullet) {
        flushPara();
        const item = spans(bullet[1].trim());
        if (item.length) list.push(item);
        continue;
      }
      flushList();
      const heading = /^\s*#{1,6}\s+(.*)$/.exec(line);
      if (heading) {
        const text = heading[1].replace(/\*\*|`/g, "").trim();
        if (text) para.push([{ text, bold: true }]);
        continue;
      }
      const s = spans(line.trim());
      if (s.length) para.push(s);
    }
    flushList();
    flushPara();
  }
  return out;
}
