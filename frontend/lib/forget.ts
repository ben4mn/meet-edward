// The verified forget in the Memory room (plan Phase 5, 2026-09-24). Pure.
//
// POST /api/memories/forget previews (what matches, and why), then deletes
// exactly the previewed plan and re-checks. This module only says those
// numbers in words. Pinned in __tests__/forget.test.ts.

export interface ForgetRelated {
  enrichments: number;
  connections: number;
  flags: number;
  entity_mentions: number;
  entities: number;
  graph_queue: number;
}

export interface ForgetPlan {
  id: string;
  subject: string;
  terms: string[];
  memories: { id: string; content: string; why: string }[];
  documents: { id: string; title: string; why: string }[];
  protected_documents: { id: string; title: string; why: string }[];
  related: ForgetRelated;
  conversations: { count: number; titles: string[] };
}

export interface ForgetResult {
  subject: string;
  plan_id: string;
  deleted: ForgetRelated & { memories: number; documents: number };
  still_matching: { memories: number; documents: number };
  protected_documents: string[];
  conversations_mentioning: number;
  graph_note?: string;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function joinAnd(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

export function linkedRowCount(r: ForgetRelated): number {
  return r.enrichments + r.connections + r.flags + r.entity_mentions + r.entities + r.graph_queue;
}

export function hasAnythingToForget(plan: ForgetPlan, includeDocuments: boolean): boolean {
  return plan.memories.length > 0 || linkedRowCount(plan.related) > 0 || (includeDocuments && plan.documents.length > 0);
}

export function forgetPreviewSummary(plan: ForgetPlan, includeDocuments: boolean): string {
  if (!hasAnythingToForget(plan, includeDocuments) && plan.documents.length === 0) {
    return `Nothing matches “${plan.subject}”.`;
  }
  const parts: string[] = [];
  if (plan.memories.length) parts.push(plural(plan.memories.length, "memory", "memories"));
  if (includeDocuments && plan.documents.length) parts.push(plural(plan.documents.length, "document", "documents"));
  const linked = linkedRowCount(plan.related);
  if (linked) parts.push(plural(linked, "linked row", "linked rows"));
  let text = parts.length ? `${joinAnd(parts)} would be deleted.` : "No memories would be deleted.";
  if (!includeDocuments && plan.documents.length) {
    text += ` ${plural(plan.documents.length, "document mentions", "documents mention")} it (kept).`;
  }
  return text;
}

export function forgetResultSummary(result: ForgetResult): string {
  const d = result.deleted;
  const parts: string[] = [plural(d.memories, "memory", "memories")];
  if (d.documents) parts.push(plural(d.documents, "document", "documents"));
  const linked = linkedRowCount(d);
  if (linked) parts.push(plural(linked, "linked row", "linked rows"));
  let text = `Deleted ${joinAnd(parts)}.`;
  const still = result.still_matching;
  if (!still.memories && !still.documents) {
    text += " Checked again: nothing still matches.";
  } else {
    const bits: string[] = [];
    bits.push(still.memories ? `${plural(still.memories, "memory", "memories")} still match` : "no memories still match");
    if (still.documents) bits.push(`${plural(still.documents, "document still mentions", "documents still mention")} it`);
    text += ` Checked again: ${bits.join("; ")}.`;
  }
  if (result.conversations_mentioning) {
    const n = result.conversations_mentioning;
    text += ` ${plural(n, "conversation still mentions", "conversations still mention")} it — delete ${n === 1 ? "it" : "them"} from the sidebar if you want ${n === 1 ? "it" : "them"} gone.`;
  }
  return text;
}
