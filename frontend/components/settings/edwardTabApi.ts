// Reads the Edward tab needs that lib/api.ts does not have: his state page,
// the sends held for the owner's approval, and the push ledger. Kept apart
// from lib/api.ts so the Edward tab's reads live with its pages; the auth
// behaviour is the same (credentials included, a 401 goes to /login
// once — lib/authSession.ts).

import { API_URL, type DocumentItem } from "@/lib/api";
import { redirectToLogin } from "@/lib/authSession";
import { pickStateDocument } from "./edwardHomeModel";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { credentials: "include", cache: "no-store" });
  if (response.status === 401) {
    redirectToLogin();
    throw new Error("Authentication required");
  }
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

/** "Edward: state", or null when he has not written one. Documents tagged
 *  `state` are listed (newest first) and the exact title is picked, so a
 *  near-miss title never stands in for his own page. */
export async function getStateDocument(): Promise<DocumentItem | null> {
  const tagged = await getJson<{ documents?: DocumentItem[] }>(`/api/documents?tags=state&limit=20`);
  const hit = pickStateDocument(tagged.documents ?? []);
  if (hit) return hit;
  const searched = await getJson<{ documents?: DocumentItem[] }>(
    `/api/documents?query=${encodeURIComponent("Edward: state")}&limit=10`,
  );
  return pickStateDocument(searched.documents ?? []);
}

export interface PendingApproval {
  id: number;
  status: string;
  tool_name: string;
  summary: string | null;
  /** The stored call (a held Codex session's whole task lives here). */
  args?: Record<string, unknown>;
  conversation_id: string | null;
  created_at: string | null;
}

export async function getPendingApprovals(): Promise<PendingApproval[]> {
  const body = await getJson<{ approvals?: PendingApproval[] }>(`/api/approvals?status=pending&limit=20`);
  return body.approvals ?? [];
}

/** The owner's answer to a held send: approve runs the stored call once, deny
 *  runs nothing. Resolves only when the server has recorded the decision;
 *  a refusal (already decided, expired, Edward's hands on this app) throws
 *  with the server's reason. */
/** POST /api/approvals/{id}'s answer: `delivered` is the send tool's own
 *  reply read by the backend — a held, refused or "Already sent" reply is
 *  false. Absent from an older backend. */
export interface DecidedApproval extends PendingApproval {
  delivered?: boolean;
  result_text?: string | null;
  already_decided?: boolean;
}

export async function decideApproval(id: number, decision: "approve" | "deny"): Promise<DecidedApproval> {
  const response = await fetch(`${API_URL}/api/approvals/${encodeURIComponent(String(id))}`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  });
  if (response.status === 401) {
    redirectToLogin();
    throw new Error("Authentication required");
  }
  if (!response.ok) {
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* no body */
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<DecidedApproval>;
}

export interface PushLogRow {
  id: string;
  source: string | null;
  title: string;
  body?: string | null;
  url: string | null;
  sent_at: string;
  opened_at: string | null;
  held_reason?: string | null;
  failed?: boolean | null;
}

export async function getPushLog(hours = 48): Promise<PushLogRow[]> {
  const body = await getJson<{ pushes?: PushLogRow[] }>(`/api/push/log?hours=${hours}&limit=20`);
  return body.pushes ?? [];
}
