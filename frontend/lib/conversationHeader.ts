// The conversation header's words (2026-09-26 chat chrome). Pure; pinned in
// __tests__/conversationHeader.test.ts.
//
// A new chat has no row in the list until its first turn lands; the header
// shows a provisional title from the owner's first line at once, so nothing jumps
// when the real title arrives. Where a thread came from is a plain word
// beside a dot: neutral for inbound and voice, emerald only for what Edward
// started (emerald means Edward). The owner's own text chats say nothing.

import type { Conversation } from "./api";

const MAX_TITLE = 60;
const PLACEHOLDER = /^(new (chat|conversation)|untitled|edward is still writing this)?$/i;

export interface HeaderMessage {
  role: string;
  content: string;
}

function clip(s: string): string {
  return s.length <= MAX_TITLE ? s : `${s.slice(0, MAX_TITLE - 1).trimEnd()}…`;
}

function firstLine(messages: readonly HeaderMessage[]): string {
  const first = messages.find((m) => m.role === "user" && m.content.trim());
  if (!first) return "";
  return first.content.trim().split(/\n/)[0].trim();
}

export function headerTitle(
  conversation: Pick<Conversation, "title" | "source"> | null | undefined,
  messages: readonly HeaderMessage[],
): string {
  let title = (conversation?.title || "").trim();
  if (conversation?.source === "heartbeat") title = title.replace(/^iMessage:\s*/i, "");
  if (title && !PLACEHOLDER.test(title)) return title;
  const line = firstLine(messages);
  return line ? clip(line) : title && !PLACEHOLDER.test(title) ? title : "New chat";
}

export interface HeaderSource {
  label: string;
  /** Edward started it (his colour); otherwise neutral. */
  edward: boolean;
}

const CHANNEL_LABEL: Record<string, string> = { sms: "Text", whatsapp: "WhatsApp", imessage: "iMessage" };

export function headerSource(conversation: Pick<Conversation, "source" | "channel"> | null | undefined): HeaderSource | null {
  if (!conversation) return null;
  const channel = (conversation.channel || "text") as string;
  switch (conversation.source) {
    case "heartbeat":
      return { label: CHANNEL_LABEL[channel] ?? "iMessage", edward: false };
    case "external_message":
      return { label: CHANNEL_LABEL[channel] ?? "Message", edward: false };
    case "edward_thought":
      return { label: "Edward", edward: true };
    case "scheduled_event":
      return { label: "Scheduled", edward: true };
    case "orchestrator_worker":
      return { label: "Worker", edward: true };
    default:
      return channel === "voice" ? { label: "Voice", edward: false } : null;
  }
}
