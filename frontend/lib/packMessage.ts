// Which chat messages are Edward's signal packs, and the one line a folded
// pack shows. Pure; pinned in __tests__/packMessage.test.ts.
//
// Why (2026-09-25, owner feedback: "his thinking is confusing to read and
// all over the place"): every wake of the mind is an ordinary turn in `edward-mind` whose
// "user" message is the raw pack — 10–20K chars starting `[WAKE: <reason>]`
// (mind_service.render_pack) or `[EXPRESSION PULSE]` (the surfaces pulse's
// trigger). As chat bubbles they buried his answers. The chat folds any long
// message behind an all-caps bracketed header into one line; the raw text is
// one tap away. A short bracketed message (the owner typing "[test]") is their
// own words and stays a bubble.

/** Below this a message is readable as it is, header or not. */
export const PACK_MIN_CHARS = 600;

/** `[WAKE: morning]`, `[EXPRESSION PULSE]`, `[REFLECT_LITE: nightly]` … */
const PACK_HEADER = /^\[([A-Z][A-Z _-]{2,})(?::\s*([^\]]*))?\]/;

/** A first sentence this short after the header is the pack's clock time
 *  (`thu 7:00am`); anything longer is prose. */
const WHEN_MAX_CHARS = 24;
const LINE_MAX_CHARS = 160;
const FIRST_LINES = 3;

export interface PackSummary {
  /** "wake", "pulse", or the header lowercased with underscores. */
  kind: string;
  /** The text after the header's colon (`morning`), when there is one. */
  reason: string | null;
  /** The pack's own clock line (`thu 7:00am`), when it has one. */
  when: string | null;
  chars: number;
  /** The first few non-empty lines, clipped, for a preview. */
  firstLines: string[];
}

export function isPackMessage(text: string): boolean {
  if (typeof text !== "string" || text.length <= PACK_MIN_CHARS) return false;
  return PACK_HEADER.test(text.trimStart());
}

function kindFor(header: string): string {
  const h = header.trim().toUpperCase();
  if (h === "WAKE") return "wake";
  if (h === "EXPRESSION PULSE" || h === "PULSE") return "pulse";
  return h.toLowerCase().replace(/[\s-]+/g, "_");
}

function clip(line: string): string {
  return line.length > LINE_MAX_CHARS ? `${line.slice(0, LINE_MAX_CHARS).trimEnd()}…` : line;
}

export function packSummary(text: string): PackSummary {
  const t = (text ?? "").trimStart();
  const m = PACK_HEADER.exec(t);
  const kind = m ? kindFor(m[1]) : "pack";
  const reason = m && m[2] && m[2].trim() ? m[2].trim() : null;
  let when: string | null = null;
  if (m) {
    const rest = t.slice(m[0].length).split("\n", 1)[0].trim();
    const dot = rest.indexOf(". ");
    const first = (dot >= 0 ? rest.slice(0, dot) : rest.replace(/\.$/, "")).trim();
    if (first && first.length <= WHEN_MAX_CHARS) when = first;
  }
  const firstLines = t
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .slice(0, FIRST_LINES)
    .map(clip);
  return { kind, reason, when, chars: (text ?? "").length, firstLines };
}

/** "14.2K chars"; "842 chars" under a thousand. */
export function formatChars(n: number): string {
  if (n < 1000) return `${n} chars`;
  const k = n / 1000;
  return `${k >= 100 ? Math.round(k) : k.toFixed(1)}K chars`;
}

/** "Wake pack · morning · thu 7:00am · 14.2K chars" */
export function packLabel(s: PackSummary): string {
  const name = s.kind.replace(/_/g, " ");
  const parts = [`${name.charAt(0).toUpperCase()}${name.slice(1)} pack`];
  if (s.reason) parts.push(s.reason);
  if (s.when) parts.push(s.when);
  parts.push(formatChars(s.chars));
  return parts.join(" · ");
}
