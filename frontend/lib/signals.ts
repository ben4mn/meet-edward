/**
 * The owner's implicit verdicts from the UI → `POST /api/signals`.
 *
 * Nothing here asks the owner to rate anything; the pages just note what they
 * did (a Today need acted on, an action that failed) so a health check
 * can count it. Pure builders live here; the fetch is in `api.ts`.
 */
import type { TodayAction, TodayNeed } from "@/components/today/todayModel";

export type SignalKind = "today_action" | "today_action_failed" | "chat_stopped" | "response_retry" | "feedback";

export interface Signal {
  kind: SignalKind;
  detail: Record<string, unknown>;
  ref_id?: string;
  conversation_id?: string;
}

const ERROR_MAX = 200;

export function buildTodayActionSignal(
  need: Pick<TodayNeed, "id" | "kind">,
  action: Pick<TodayAction, "label" | "url">,
  ok: boolean,
  error?: string,
): Signal {
  const detail: Record<string, unknown> = { need_kind: need.kind, action: action.label, url: action.url };
  if (!ok) detail.error = (error ?? "").slice(0, ERROR_MAX);
  return { kind: ok ? "today_action" : "today_action_failed", detail, ref_id: need.id };
}
