// Settings → Memory → Documents (2026-09-26, Jarvis polish). Pure; pinned
// in __tests__/documentView.test.ts.
//
// "Edward: state" (the page he carries into every wake) and the owner's goals
// ("Goal: …") are pinned above the rest. A list preview is prose — the
// markdown is for the reading view.

export const STATE_DOC_TITLE = "Edward: state";

export type DocKind = "state" | "goal" | "doc";

const GOAL_RE = /^Goal:\s*/;

export function docKind(title: string): DocKind {
  if (title.trim() === STATE_DOC_TITLE) return "state";
  if (GOAL_RE.test(title)) return "goal";
  return "doc";
}

export function splitPinned<D extends { title: string }>(docs: D[]): { pinned: D[]; rest: D[] } {
  const state: D[] = [];
  const goals: D[] = [];
  const rest: D[] = [];
  for (const d of docs) {
    const k = docKind(d.title);
    if (k === "state") state.push(d);
    else if (k === "goal") goals.push(d);
    else rest.push(d);
  }
  return { pinned: [...state, ...goals], rest };
}

export function docDisplay(title: string): { title: string; badge: string | null } {
  const k = docKind(title);
  if (k === "state") return { title: "My state", badge: null };
  if (k === "goal") return { title: title.replace(GOAL_RE, "") || title, badge: "Goal" };
  return { title, badge: null };
}

/** Markdown as one line of prose, clipped on a word. Headings end with a
 *  colon ("Why: …"), list items run together with semicolons. */
export function docPreview(content: string, max = 160): string {
  type Seg = { text: string; kind: "heading" | "item" | "text" };
  const segs: Seg[] = [];
  let inFence = false;
  for (const raw of (content || "").split("\n")) {
    if (/^\s*```/.test(raw)) {
      inFence = !inFence;
      continue;
    }
    if (/^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(raw)) continue; // table rule
    const heading = /^\s{0,3}#{1,6}\s+/.test(raw);
    const item = /^\s*(?:[-*+]|\d+\.)\s+/.test(raw);
    const text = raw
      .replace(/^\s{0,3}#{1,6}\s+/, "")
      .replace(/^\s*>\s?/, "")
      .replace(/^\s*(?:[-*+]|\d+\.)\s+(?:\[[ xX]\]\s+)?/, "")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/`([^`]*)`/g, "$1")
      .replace(/(\*\*|__)(.+?)\1/g, "$2")
      .replace(/(^|[\s(])[*_](\S(?:.*?\S)?)[*_](?=[\s).,!?:;]|$)/g, "$1$2")
      .replace(/\|/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (text) segs.push({ text, kind: heading ? "heading" : item ? "item" : "text" });
  }
  const out: string[] = [];
  segs.forEach((seg, i) => {
    let t = seg.text;
    const ends = /[.:;!?…]$/.test(t);
    if (seg.kind === "heading" && !ends) t += ":";
    else if (seg.kind === "item" && segs[i + 1] && !ends) t += segs[i + 1].kind === "item" ? ";" : ".";
    out.push(t);
  });
  const text = out.join(" ");
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const at = cut.lastIndexOf(" ");
  return `${(at > max * 0.5 ? cut.slice(0, at) : cut).replace(/[\s:;,.]+$/, "")}…`;
}

export function tagList(tags: string | null | undefined): string[] {
  return (tags || "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

/** Housekeeping tags that would only clutter the chips. */
const HIDDEN_TAGS = new Set(["state", "self", "active", "paused", "done", "dropped"]);

export function tagOptions(byTag: Record<string, number> | null | undefined): { tag: string; count: number }[] {
  return Object.entries(byTag || {})
    .filter(([t]) => !HIDDEN_TAGS.has(t))
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
}

/** The document opens with its own `# Title`: the reading view lets that
 *  be the heading instead of printing the title twice. */
export function opensWithTitle(content: string): boolean {
  return /^\s*#\s+\S/.test(content || "");
}
