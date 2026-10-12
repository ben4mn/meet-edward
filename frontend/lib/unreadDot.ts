/** Which sidebar conversations get the unread dot. Pure; tested. */

export type DotSource = "user" | "scheduled_event" | "external_message" | "heartbeat" | "orchestrator_worker" | "edward_thought";

/** Edward-initiated sources: he tried to get the owner's attention, so an unread one deserves a dot. */
export const EDWARD_INITIATED: ReadonlySet<string> = new Set(["heartbeat", "scheduled_event", "edward_thought"]);

export function shouldShowUnreadDot(c: { notified_user?: boolean; source?: string }): boolean {
  return !!c.notified_user && !!c.source && EDWARD_INITIATED.has(c.source);
}
