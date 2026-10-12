// Where a tapped notification takes the owner.
//
// Every push carries a destination — Edward's chain of thought
// (/chat?c=<id>), the Today feed, a settings room. The service worker
// hands the app the URL; this turns it into a path the router can push,
// whatever tab the PWA was left on. Other origins are refused: a push
// payload is data, not a place to send the user.

export function resolveNotificationNav(
  url: string | null | undefined,
  origin: string,
): string | null {
  if (!url) return null;
  let parsed: URL;
  try {
    parsed = new URL(url, origin);
  } catch {
    return null;
  }
  if (parsed.origin !== origin) return null;
  const path = parsed.pathname === "/" ? "/today" : parsed.pathname;
  return `${path}${parsed.search}${parsed.hash}`;
}

// What the service worker keeps on a notification (push_service.build_payload).
export interface NotificationData {
  url?: string | null;
  kind?: string | null;
  approval_id?: number | string | null;
  conversation_id?: string | null;
}

// A send Edward held at the boundary (taint and approvals, 2026-09-24):
// the push carries Approve / Deny / Open. The owner's answer goes out of band —
// a POST from the service worker, never chat text a page could forge.
export const APPROVAL_KIND = "approval";

export type ApprovalDecision = "approve" | "deny";

export interface ApprovalDecisionRequest {
  path: string;
  body: { decision: ApprovalDecision };
}

// Where a tap goes. An approval opens the thread the send came from (iOS
// draws no web-push buttons, so the tap is the whole interface there);
// anything else goes wherever its URL points, and Today when that is not
// a place in this app. public/sw-push.js carries a copy; __tests__/swPush
// pins that the two agree.
export function notificationDestination(
  data: NotificationData | null | undefined,
  origin: string,
): string {
  if (data?.kind === APPROVAL_KIND && data.conversation_id) {
    return `/chat?c=${encodeURIComponent(data.conversation_id)}`;
  }
  return resolveNotificationNav(data?.url, origin) ?? "/today";
}

// The request an Approve / Deny button sends, or null for every other
// action and every other kind of push.
export function approvalDecisionRequest(
  data: NotificationData | null | undefined,
  action: string,
): ApprovalDecisionRequest | null {
  if (data?.kind !== APPROVAL_KIND) return null;
  if (action !== "approve" && action !== "deny") return null;
  const raw = data.approval_id;
  const id = typeof raw === "string" && !/^\d+$/.test(raw) ? NaN : Number(raw);
  if (!Number.isInteger(id) || id <= 0) return null;
  return { path: `/api/approvals/${id}`, body: { decision: action } };
}
