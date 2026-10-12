// A thought entry: the short conversation a wake of Edward's mind leaves
// when it takes an action. Pure; pinned in __tests__/thoughtEntry.test.ts.
//
// Why (2026-09-25, design decision): the thoughts that led to an action
// read like chats Edward had with himself. Edward shouldn't need approval,
// but it must be obvious when he is choosing to take action. Every wake
// used to land in one long `edward-mind` thread of raw packs. Now a wake that acts also writes one conversation (source
// `edward_thought`) whose `thought` says so; the chat opens it as an entry —
// "Edward acted · messages wake · 2h ago" — with a "Right call?" that goes
// to the same `POST /api/mind/verdict` as the Mind panel, so the verdict
// reaches his next wake's pack. The owner can still reply to ask "why?".

import { formatRelative } from "./time";
import { wakeSourceLabel } from "./mindLog";

export type ThoughtVerdict = "right_call" | "wrong_call";

/** `thought` on a conversation (GET /api/conversations and the single one). */
export interface ThoughtMeta {
  kind: "mind_action";
  /** The mind's `ledger_events` row — what /api/mind/verdict is about. */
  ledger_id: number;
  /** The wake's reason: morning, messages, mention, self, … */
  reason: string;
  acted_at: string;
  /** An outward-tier action: a send, a push, but also a Today note or a
   *  send held for the owner's approval. Never what the "reached out" chip reads. */
  outward: boolean;
  /** A message went out to a person or a push reached the owner
   *  (thought_journal.reached_someone, 2026-09-27). null or absent: an
   *  entry written before the field — unknown, never guessed from outward. */
  reached?: boolean | null;
  verdict: ThoughtVerdict | null;
}

export type Opts = { now?: Date; timeZone?: string };

export function isMindAction(c: { thought?: ThoughtMeta | null } | null | undefined): boolean {
  return !!c && !!c.thought && c.thought.kind === "mind_action";
}

/** "Edward acted · messages wake · 2h ago" */
export function describeThought(t: ThoughtMeta, o: Opts = {}): string {
  const parts = ["Edward acted", wakeSourceLabel(t.reason)];
  if (t.acted_at) parts.push(formatRelative(t.acted_at, { now: o.now, tz: o.timeZone }));
  return parts.join(" · ");
}

const SAID: Record<ThoughtVerdict, string> = { right_call: "right call", wrong_call: "wrong call" };

export function verdictLabel(v: ThoughtVerdict): string {
  return `You said: ${SAID[v] ?? v}`;
}

// ---- The verdict: optimistic, rolled back when the post fails ------------

export interface VerdictState {
  /** What the backend has (or had, before this session's post). */
  committed: ThoughtVerdict | null;
  /** Posted and not yet answered. Shown in place of `committed`. */
  pending: ThoughtVerdict | null;
  error: string | null;
}

export function initialVerdict(t: Pick<ThoughtMeta, "verdict">): VerdictState {
  return { committed: t.verdict ?? null, pending: null, error: null };
}

export function shownVerdict(s: VerdictState): ThoughtVerdict | null {
  return s.pending ?? s.committed;
}

export function beginVerdict(s: VerdictState, v: ThoughtVerdict): VerdictState {
  return { committed: s.committed, pending: v, error: null };
}

export function settleVerdict(s: VerdictState, ok: boolean, error?: string): VerdictState {
  if (ok) return { committed: s.pending ?? s.committed, pending: null, error: null };
  return { committed: s.committed, pending: null, error: error || "That verdict didn't save. Try again." };
}

export type VerdictView = { mode: "ask" } | { mode: "said"; label: string; saving: boolean };

/** Ask "Right call?" until there is a verdict; then read it back, unless
 *  the owner asked to change it. */
export function verdictView(s: VerdictState, editing: boolean): VerdictView {
  const v = shownVerdict(s);
  if (!v || editing) return { mode: "ask" };
  return { mode: "said", label: verdictLabel(v), saving: s.pending !== null };
}

// ---- Which conversation the header describes -----------------------------

type WithThought = { id: string; thought?: ThoughtMeta | null };

/** The sidebar list is paged per group, so a thought opened from Today or
 *  the Mind panel is often not in it. The header takes the list's row (its
 *  title follows retitles) and the thought from the conversation that was
 *  loaded, which is fresher; with no row, the loaded one alone. */
export function withThought<T extends WithThought>(
  listed: T | null | undefined,
  loaded: T | null | undefined,
  conversationId: string | undefined,
): T | null {
  if (!conversationId) return null;
  const fresh = loaded && loaded.id === conversationId ? loaded : null;
  const row = listed && listed.id === conversationId ? listed : null;
  if (row && fresh) return { ...row, thought: fresh.thought ?? row.thought ?? null };
  return row ?? fresh ?? null;
}
