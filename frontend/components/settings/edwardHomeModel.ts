// What the Edward tab's landing says, decided from real signals only
// (docs/design-system-2026-09-25.md §1: "the animation is a claim, so it
// must be true" — so is every line). Pure; pinned in __tests__/edwardHome.test.ts.
//
//   * his own words: the first sentences of the "Edward: state" document;
//   * the three answers — Knows, Watches, Does;
//   * one live line and a health tone per room, shared by the landing's
//     rooms list and the desktop rail.

import { formatRelative } from "../../lib/time";
import { formatResetTime, reasonPhrase } from "../../lib/edwardMood";
import type { MindNow, MindWakeRow } from "../../lib/mindNowApi";
import type { RoomId } from "./settingsRooms";

/** The one document he carries in his head (backend mind_service.STATE_DOC_TITLE). */
export const STATE_DOC_TITLE = "Edward: state";

export type LineTone = "neutral" | "ok" | "warn" | "bad" | "info";

export interface TimeOptions {
  now?: Date;
  /** IANA zone for clock times (the reset time); the browser's by default. */
  tz?: string;
}

// ---------------------------------------------------------------- his words

export function pickStateDocument<T extends { title: string }>(docs: readonly T[]): T | null {
  const want = STATE_DOC_TITLE.toLowerCase();
  return docs.find((d) => (d.title ?? "").trim().toLowerCase() === want) ?? null;
}

const ABBREVIATIONS = /\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|etc|e\.g|i\.e|approx)\.$/i;

function plainInline(text: string): string {
  return text
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/(\*\*|__)(.+?)\1/g, "$2")
    .replace(/(^|[^\w*])[*_]([^*_]+)[*_](?=[^\w*]|$)/g, "$1$2")
    .replace(/\s+/g, " ")
    .trim();
}

function sentences(text: string): string[] {
  const out: string[] = [];
  let current = "";
  const parts = text.split(/(?<=[.!?])\s+/);
  for (const part of parts) {
    current = current ? `${current} ${part}` : part;
    if (ABBREVIATIONS.test(current)) continue;
    out.push(current);
    current = "";
  }
  if (current) out.push(current);
  return out.map((s) => s.trim()).filter(Boolean);
}

function clip(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  const cut = text.slice(0, maxChars);
  const space = cut.lastIndexOf(" ");
  return `${(space > maxChars * 0.5 ? cut.slice(0, space) : cut).replace(/[\s,;:.—-]+$/, "")}…`;
}

/** The opening of his state page in his own voice: the first prose
 *  paragraph's first sentences (headings skipped), else the first list
 *  items as sentences. */
export function stateExcerpt(content: string, opts: { sentences?: number; maxChars?: number } = {}): string {
  const count = opts.sentences ?? 2;
  const maxChars = opts.maxChars ?? 240;
  const lines = (content ?? "").split(/\r?\n/);

  const paragraphs: string[] = [];
  let buf: string[] = [];
  const bullets: string[] = [];
  const flush = () => {
    if (buf.length) paragraphs.push(buf.join(" "));
    buf = [];
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flush();
      continue;
    }
    if (/^#{1,6}\s/.test(line) || /^(-{3,}|\*{3,})$/.test(line)) {
      flush();
      continue;
    }
    const bullet = line.match(/^(?:[-*+]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      flush();
      bullets.push(plainInline(bullet[1]));
      continue;
    }
    buf.push(line);
  }
  flush();

  const prose = paragraphs.map(plainInline).find((p) => p.length > 0);
  if (prose) return clip(sentences(prose).slice(0, count).join(" "), maxChars);
  if (bullets.length) {
    const items = bullets
      .filter(Boolean)
      .slice(0, count)
      .map((b) => (/[.!?]$/.test(b) ? b : `${b}.`));
    return clip(items.join(" "), maxChars);
  }
  return "";
}

// ---------------------------------------------------------------- the answers

function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

/** What he knows, one number everywhere: live memories (not retired or
 *  expired) when the stats carry them — the count the Memories panel shows
 *  (lib/memoryView.statsLine) — else every row. */
export interface MemoryCount {
  total: number;
  live?: number | null;
  created_last_24h?: number | null;
}

export function memoryCount(stats: MemoryCount): number {
  return stats.live ?? stats.total;
}

export function knowsLine(stats: MemoryCount | null): string {
  if (!stats) return "Search what he knows";
  const base = plural(memoryCount(stats), "memory", "memories");
  const today = stats.created_last_24h;
  if (today == null) return base;
  return today > 0 ? `${base} · ${today.toLocaleString("en-US")} today` : `${base} · nothing new today`;
}

/** `waiting` null: his mind could not be read, so nothing is claimed
 *  (design principle 2 — never "fine" when he can't see). */
function waitingPhrase(waiting: number | null): string {
  if (waiting == null) return "can't check what's waiting";
  return waiting > 0 ? `${waiting} waiting on you` : "nothing waiting on you";
}

export function watchesLine(intentions: number | null, waiting: number | null): string {
  if (intentions == null) {
    if (waiting == null) return "Can't check what's waiting right now";
    const w = waitingPhrase(waiting);
    return w.charAt(0).toUpperCase() + w.slice(1);
  }
  const head = intentions > 0 ? plural(intentions, "intention", "intentions") : "No open intentions";
  return `${head} · ${waitingPhrase(waiting)}`;
}

export interface DoesAnswer {
  title: string;
  /** His closing line (or the last one that acted). */
  detail: string | null;
  /** The thought entry to read, when a wake left one. */
  thoughtHref: string | null;
  /** A wake is running right now. */
  live: boolean;
}

const OUTCOME_WORDS: Record<string, string> = {
  acted: "acted",
  left_alone: "nothing needed doing",
  failed: "didn't finish",
};

function rel(iso: string | null, o: TimeOptions): string | null {
  if (!iso) return null;
  return formatRelative(iso, { now: o.now, tz: o.tz });
}

function wakeTitle(r: MindWakeRow, o: TimeOptions): string {
  const when = rel(r.at, o);
  if (r.outcome === "skipped") return when ? `Skipped a wake ${when} · out of fuel` : "Skipped a wake · out of fuel";
  const words = r.outcome ? OUTCOME_WORDS[r.outcome] : undefined;
  const head = when ? `Woke ${when}` : "Woke";
  return words ? `${head} · ${words}` : head;
}

function thoughtHref(r: MindWakeRow | null | undefined): string | null {
  return r?.thought_id ? `/chat?c=${encodeURIComponent(r.thought_id)}` : null;
}

export function doesAnswer(now: MindNow | null, o: TimeOptions = {}): DoesAnswer {
  if (!now) return { title: "What he does on his own", detail: null, thoughtHref: null, live: false };
  if (now.waking) {
    const phrase = reasonPhrase(now.waking.reason);
    const lastActed = now.recent.find((r) => r.outcome === "acted");
    return {
      title: phrase ? `Awake now · ${phrase}` : "Awake now",
      detail: lastActed?.line ? `Last acted ${rel(lastActed.at, o) ?? ""} — ${lastActed.line}`.replace("  ", " ") : null,
      thoughtHref: thoughtHref(lastActed),
      live: true,
    };
  }
  const last = now.last ?? now.recent[0] ?? null;
  if (!last) return { title: "No wakes yet", detail: null, thoughtHref: null, live: false };
  if (last.outcome === "acted") {
    return { title: wakeTitle(last, o), detail: last.line, thoughtHref: thoughtHref(last), live: false };
  }
  const acted = now.recent.find((r) => r !== last && r.outcome === "acted");
  if (acted) {
    const when = rel(acted.at, o);
    const head = when ? `Last acted ${when}` : "Last acted";
    return {
      title: wakeTitle(last, o),
      detail: acted.line ? `${head} — ${acted.line}` : head,
      thoughtHref: thoughtHref(acted),
      live: false,
    };
  }
  // A failed wake's line is an error string — never owner-facing.
  const detail = last.outcome === "left_alone" ? last.line : null;
  return { title: wakeTitle(last, o), detail, thoughtHref: thoughtHref(last), live: false };
}

// ---------------------------------------------------------------- the rooms

export interface RoomLine {
  tone: LineTone;
  text: string;
}

export interface RoomLineInput {
  mindNow: MindNow | null;
  intentions: number | null;
  memory: MemoryCount | null;
  heartbeat: { running: boolean; enabled: boolean } | null;
  workersRunning: number | null;
  wishesOpen: number | null;
  house: { overall: string; problems: string[]; repairs: string[] } | null;
}

function mindLine(inp: RoomLineInput, o: TimeOptions): RoomLine {
  const intentions = inp.intentions != null ? plural(inp.intentions, "intention", "intentions") : null;
  const tail = intentions ? ` · ${intentions}` : "";
  const m = inp.mindNow;
  if (!m) {
    return { tone: "neutral", text: intentions ? `${intentions.charAt(0).toUpperCase()}${intentions.slice(1)} open` : "Can't reach his mind right now" };
  }
  if (m.waking) {
    const phrase = m.waking.reason === "surfaces" ? "his screens" : reasonPhrase(m.waking.reason);
    return { tone: "ok", text: `Thinking now${phrase ? ` · ${phrase}` : ""}` };
  }
  const last = m.last ?? m.recent[0] ?? null;
  if (!last) return { tone: "neutral", text: `No wakes yet${tail}` };
  const when = rel(last.at, o);
  const head = when ? `Woke ${when}` : "Woke";
  if (last.outcome === "failed") return { tone: "warn", text: `${head} · didn't finish${tail}` };
  if (last.outcome === "skipped") return { tone: "warn", text: `Skipped a wake · out of fuel${tail}` };
  const word = last.outcome === "acted" ? " · acted" : "";
  return { tone: "ok", text: `${head}${word}${tail}` };
}

function memoryLine(inp: RoomLineInput): RoomLine {
  const s = inp.memory;
  if (!s) return { tone: "neutral", text: "Can't count memories right now" };
  const base = plural(memoryCount(s), "memory", "memories");
  const today = s.created_last_24h;
  return { tone: "ok", text: today != null && today > 0 ? `${base} · ${today.toLocaleString("en-US")} new today` : base };
}

function attentionLine(inp: RoomLineInput): RoomLine {
  const hb = inp.heartbeat;
  const waiting = inp.mindNow ? inp.mindNow.approvals_waiting ?? 0 : null;
  if (!hb) {
    return waiting != null && waiting > 0
      ? { tone: "warn", text: `${waiting} waiting on you` }
      : { tone: "neutral", text: "Can't see what he's watching" };
  }
  if (!hb.enabled) return { tone: "warn", text: "Not watching messages — switched off" };
  if (!hb.running) return { tone: "warn", text: "Not watching messages — paused" };
  const tone: LineTone = waiting == null ? "neutral" : waiting > 0 ? "warn" : "ok";
  return { tone, text: `Watching · ${waitingPhrase(waiting)}` };
}

function workLine(inp: RoomLineInput, o: TimeOptions): RoomLine {
  const q = inp.mindNow?.quota;
  if (q?.exhausted) {
    const when = q.resets_at ? formatResetTime(q.resets_at, { now: o.now, tz: o.tz }) : null;
    return { tone: "warn", text: when ? `Out of fuel · back ${when}` : `Out of fuel for ${q.period === "day" ? "today" : "the week"}` };
  }
  const wishes = inp.wishesOpen ? ` · ${plural(inp.wishesOpen, "open wish", "open wishes")}` : "";
  if (inp.workersRunning == null) {
    return inp.wishesOpen ? { tone: "neutral", text: plural(inp.wishesOpen, "open wish", "open wishes") } : { tone: "neutral", text: "Can't see his workers right now" };
  }
  if (inp.workersRunning > 0) {
    return { tone: "info", text: `${plural(inp.workersRunning, "worker", "workers")} running${wishes}` };
  }
  return { tone: "ok", text: `Nothing running${wishes}` };
}

/** A watchdog line for the owner: no exit codes or pids. */
export function plainHealthNote(note: string): string {
  return note
    .replace(/\s*\((?:exit|pid|code)\b[^)]*\)/gi, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function houseLine(inp: RoomLineInput): RoomLine {
  const h = inp.house;
  if (!h) return { tone: "neutral", text: "Can't reach the health check" };
  switch (h.overall) {
    case "healthy":
      return { tone: "ok", text: "Everything healthy" };
    case "repaired":
      return { tone: "info", text: h.repairs[0] ? `Fixed itself · ${plainHealthNote(h.repairs[0])}` : "Fixed itself" };
    case "problem":
      return { tone: "bad", text: h.problems[0] ? `Needs you · ${plainHealthNote(h.problems[0])}` : "Needs you" };
    case "stale":
      return {
        tone: "warn",
        text: h.problems[0] ? `Health check is stale · ${plainHealthNote(h.problems[0])}` : "Health check is stale",
      };
    default:
      return { tone: "neutral", text: "Health unknown" };
  }
}

export function roomLines(inp: RoomLineInput, o: TimeOptions = {}): Record<RoomId, RoomLine> {
  return {
    mind: mindLine(inp, o),
    memory: memoryLine(inp),
    attention: attentionLine(inp),
    work: workLine(inp, o),
    house: houseLine(inp),
  };
}
