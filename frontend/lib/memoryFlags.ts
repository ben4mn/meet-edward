/**
 * Contradiction flags the owner has to settle themselves.
 *
 * Consolidation auto-resolves a contradiction only when the answer is clear
 * (a user-stated memory beats an inferred one). A tie between two things
 * the owner said, or a flag with one side missing, stays open and shows up
 * here with both texts so they can pick the one to keep.
 */

import { readParam, type ParamsLike } from "./deepLink";
import { formatRelative } from "./time";

export interface FlagLike {
  id: string;
  flag_type: string;
  description: string;
  resolved: boolean;
  created_at: string;
  memory_id: string;
  related_memory_id: string | null;
  memory_content?: string | null;
  related_memory_content?: string | null;
}

/** Open flags, newest first. */
export function flagsNeedingReview<T extends FlagLike>(flags: T[]): T[] {
  return flags
    .filter((f) => !f.resolved)
    .slice()
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
}

/** A contradiction with both memory texts present can be settled in place. */
export function isChoosable(flag: FlagLike): boolean {
  return (
    flag.flag_type === "contradiction" &&
    typeof flag.memory_content === "string" &&
    typeof flag.related_memory_content === "string"
  );
}

// ---------------------------------------------------------------------------
// The "Which is right?" sheet (2026-09-26, Jarvis polish). Contradiction
// notes and pushes link to /settings/dreaming?flag=<id>; `&ask=rewrite`
// marks a proposed rewrite (Edward thinks one memory is worded wrong),
// which is never offered as "keep one". Pinned in __tests__/memoryFlags.test.ts.


export type DreamsTab = "overview" | "review" | "links";

export interface FlagDeepLink {
  flagId: string | null;
  rewrite: boolean;
  tab: DreamsTab | null;
}

export function flagDeepLink(params: ParamsLike | null | undefined): FlagDeepLink {
  const flagId = readParam(params, "flag");
  const rewrite = readParam(params, "ask") === "rewrite";
  const tabParam = readParam(params, "tab");
  let tab: DreamsTab | null = null;
  if (flagId || tabParam === "flags" || tabParam === "review") tab = "review";
  else if (tabParam === "connections" || tabParam === "links") tab = "links";
  else if (tabParam === "status" || tabParam === "overview") tab = "overview";
  return { flagId, rewrite, tab };
}

export interface FlagChoice {
  keep: "a" | "b";
  label: string;
  text: string;
}

/** The two sides the owner can keep; nothing for a rewrite or a one-sided flag. */
export function flagChoices(flag: FlagLike, opts: { rewrite?: boolean } = {}): FlagChoice[] {
  if (opts.rewrite || !isChoosable(flag)) return [];
  return [
    { keep: "a", label: "Keep this", text: flag.memory_content as string },
    { keep: "b", label: "Keep that", text: flag.related_memory_content as string },
  ];
}

function humanize(s: string): string {
  const t = s.replace(/_/g, " ").trim();
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : "Something to check";
}

export function flagHeadline(flag: FlagLike, opts: { rewrite?: boolean } = {}): string {
  if (opts.rewrite) return "Reword this?";
  switch (flag.flag_type) {
    case "contradiction":
      return isChoosable(flag) ? "Which is right?" : "Two memories disagree";
    case "stale":
      return "Might be out of date";
    case "low_quality":
      return "Too vague to keep?";
    case "duplicate":
      return "Said twice";
    default:
      return humanize(flag.flag_type);
  }
}

/** The list chip: one word for the kind of flag. */
export function flagKind(flagType: string): string {
  switch (flagType) {
    case "contradiction":
      return "Disagree";
    case "stale":
      return "Stale";
    case "low_quality":
      return "Vague";
    case "duplicate":
      return "Duplicate";
    default:
      return humanize(flagType);
  }
}

/** Today's note about this flag, if one still holds it: the question and
 *  whether it asked for a rewrite. Matched on the exact `flag` param, so
 *  fl-1 never finds fl-10. */
export function flagNote(
  flagId: string,
  notes: { href?: string | null; body?: string | null }[],
): { question: string | null; rewrite: boolean } | null {
  for (const n of notes) {
    const href = n.href || "";
    const q = href.indexOf("?");
    if (q < 0) continue;
    const params = new URLSearchParams(href.slice(q + 1));
    if (params.get("flag") === flagId) {
      const body = (n.body || "").trim();
      return { question: body || null, rewrite: params.get("ask") === "rewrite" };
    }
  }
  return null;
}

/** The question Edward asked on Today for this flag, if a note still holds it. */
export function questionFor(
  flagId: string,
  notes: { href?: string | null; body?: string | null }[],
): string | null {
  return flagNote(flagId, notes)?.question ?? null;
}

// ---------------------------------------------------------------------------
// Overview and Connections words (review 2026-09-26).

/** "next in 22m"; "next any minute" when the run is due or late (the loop
 *  runs late, or the rate gate deferred it) — never "next 10m ago". */
export function nextDreamText(nextRun: string | null | undefined, opts: { now?: Date } = {}): string | null {
  if (!nextRun) return null;
  const at = Date.parse(nextRun);
  if (Number.isNaN(at)) return null;
  const now = opts.now ?? new Date();
  if (at - now.getTime() < 60_000) return "next any minute";
  return `next ${formatRelative(nextRun, { now })}`;
}

const CONNECTION_PHRASE: Record<string, string> = {
  same_person: "is about the same person as",
  same_topic: "is on the same topic as",
  same_event: "is about the same event as",
  related: "is related to",
  elaborates: "adds detail to",
  supports: "supports",
  contradicts: "disagrees with",
};

/** What a link between two memories means, read between them:
 *  "<first> adds detail to <second>". */
export function connectionPhrase(type: string): string {
  return CONNECTION_PHRASE[type] ?? "is linked to";
}
