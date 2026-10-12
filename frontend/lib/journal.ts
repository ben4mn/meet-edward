// Pure wording for the weekly journal (Settings → Mind → Journal): what
// Edward noticed about himself this week and how his wakes went, in words
// rather than ledger kinds. Pinned in __tests__/journal.test.ts.

import { formatRelative } from "./time";

const KIND_LABEL: Record<string, string> = {
  tool_failure: "Tools that failed",
  user_correction: "You corrected me",
  triage_mismatch: "Messages I misjudged",
  triage: "Messages I misjudged",
  push_ignored: "Pushes you didn't open",
  push_outcome: "How my pushes landed",
  push_outcome_health: "How my pushes landed",
  push_acted: "Pushes you acted on",
};

export function observationKindLabel(kind: string): string {
  const known = KIND_LABEL[kind];
  if (known) return known;
  const words = kind.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** "4 times · still happening · last 13h ago" */
export function observationMeta(
  o: { occurrences: number; status: string; last_seen_at: string | null },
  now: Date = new Date(),
): string {
  const parts = [o.occurrences === 1 ? "once" : `${o.occurrences} times`];
  parts.push(o.status === "open" ? "still happening" : "settled");
  if (o.last_seen_at) parts.push(`last ${formatRelative(o.last_seen_at, { now })}`);
  return parts.join(" · ");
}

/** "Woke 20 times: acted on 5, left 14 alone, 1 failed." */
export function outcomeSummary(rows: { outcome: string; count: number }[]): string {
  const total = rows.reduce((n, r) => n + r.count, 0);
  if (!total) return "I haven't woken this week.";
  const get = (k: string) => rows.find((r) => r.outcome === k)?.count ?? 0;
  const bits: string[] = [];
  if (get("acted")) bits.push(`acted on ${get("acted")}`);
  if (get("left_alone")) bits.push(`left ${get("left_alone")} alone`);
  if (get("failed")) bits.push(`${get("failed")} failed`);
  if (get("skipped")) bits.push(`skipped ${get("skipped")}`);
  const head = total === 1 ? "Woke once" : `Woke ${total} times`;
  return bits.length ? `${head}: ${bits.join(", ")}.` : `${head}.`;
}
