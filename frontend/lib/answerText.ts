// An answer's links and its read-aloud text (2026-09-26). Pure.
//
// classifyLink: a link into the app (a relative path, or the app's own
// origin) stays in the app — next/link, no new tab, no ↗; a link off the
// app opens in a new tab with ↗; a stored file (/api/files/<id>/download)
// downloads by its relative path whatever host the model wrote.
// speakableText: the answer without markdown punctuation, code or tables,
// for POST /api/voice/synthesize.
// retryQuestion: what Retry sends again — the owner's own question, never a wake
// pack, a trigger or an empty (attachment-only) message.

import { isPackMessage } from "./packMessage";

export type LinkKind = "internal" | "external" | "download" | "none";

const FILE_DOWNLOAD = /^(?:https?:\/\/[^/]+)?\/api\/files\/([^/?#]+)\/download$/;
const SAFE_SCHEMES = /^(https?:|mailto:|tel:|sms:)/i;

export function classifyLink(href: string | null | undefined, origin?: string | null): { kind: LinkKind; href: string } {
  const raw = (href ?? "").trim();
  if (!raw) return { kind: "none", href: "" };
  const file = raw.match(FILE_DOWNLOAD);
  if (file) return { kind: "download", href: `/api/files/${file[1]}/download` };
  if (raw.startsWith("#") || (raw.startsWith("/") && !raw.startsWith("//"))) return { kind: "internal", href: raw };
  if (!SAFE_SCHEMES.test(raw)) return { kind: "none", href: "" };
  if (origin && /^https?:/i.test(raw)) {
    try {
      const url = new URL(raw);
      if (url.origin === new URL(origin).origin) return { kind: "internal", href: `${url.pathname}${url.search}${url.hash}` || "/" };
    } catch {
      /* not a URL: external as written */
    }
  }
  return { kind: "external", href: raw };
}

function endSentence(line: string): string {
  const t = line.trim();
  if (!t) return t;
  return /[.!?…:;,—-]$/.test(t) ? t : `${t}.`;
}

export function speakableText(markdown: string): string {
  const lines: string[] = [];
  let inFence = false;
  for (const rawLine of (markdown || "").split("\n")) {
    const line = rawLine.trim();
    if (/^(```|~~~)/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence || !line) continue;
    if (/^\|.*\|$/.test(line)) continue; // table rows and rules
    if (/^([-*_])\1{2,}$/.test(line)) continue; // horizontal rules
    const text = line
      .replace(/^#{1,6}\s+/, "")
      .replace(/^>\s?/, "")
      .replace(/^([-*+]|\d+[.)])\s+/, "")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/`([^`]*)`/g, "$1")
      .replace(/(\*\*|__)(.+?)\1/g, "$2")
      .replace(/(\*|_)(.+?)\1/g, "$2")
      .replace(/~~(.+?)~~/g, "$1")
      .trim();
    if (text) lines.push(endSentence(text));
  }
  return lines.join("\n");
}

/** The question before the answer at `index`, when Retry may send it again. */
export function retryQuestion(
  messages: ReadonlyArray<{ role: "user" | "assistant"; content: string; isTrigger?: boolean }>,
  index: number,
): string | undefined {
  for (let i = index - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role !== "user") continue;
    const text = m.content.trim();
    if (m.isTrigger || !text || isPackMessage(m.content)) return undefined;
    return text;
  }
  return undefined;
}

export interface TriggerView {
  kind: "heartbeat" | "scheduled";
  /** In Edward's words, never "Heartbeat Trigger". */
  label: string;
  /** Unstructured text (the first line after the header, or all of it). */
  body: string;
  /** `Key: value` lines, internal ids left out. */
  fields: [string, string][];
}

const TRIGGER_HEADER = /^\[(heartbeat|scheduled)[^\]]*\]\s*/i;
const HIDDEN_FIELDS = new Set(["conversation id"]);

/** What woke Edward in a heartbeat or scheduled-event thread. */
export function describeTrigger(content: string, triggerType?: string | null): TriggerView {
  const text = (content || "").trim();
  const header = text.match(TRIGGER_HEADER);
  const heartbeat = triggerType === "heartbeat" || header?.[1].toLowerCase() === "heartbeat";
  const rest = header ? text.slice(header[0].length) : text;
  const fields: [string, string][] = [];
  const prose: string[] = [];
  for (const line of rest.split("\n")) {
    const m = line.match(/^([A-Za-z][A-Za-z ]+?):\s*(.+)$/);
    if (m) {
      if (!HIDDEN_FIELDS.has(m[1].trim().toLowerCase())) fields.push([m[1].trim(), m[2].trim()]);
    } else if (line.trim()) prose.push(line.trim());
  }
  return {
    kind: heartbeat ? "heartbeat" : "scheduled",
    label: heartbeat ? "A message I noticed" : "Something I scheduled",
    body: prose.join(" "),
    fields,
  };
}
