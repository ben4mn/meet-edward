// Renaming a conversation from the list (2026-09-26 chat chrome). The
// backend has PATCH /api/conversations/{id} {title}; lib/api.ts has no
// wrapper for it, so this is the one. Same auth handling as authFetch:
// credentials included, a 401 goes to /login.

import { API_URL } from "./api";
import { redirectToLogin } from "./authSession";

export const MAX_TITLE_CHARS = 120;

export function cleanTitle(raw: string): string | null {
  const t = raw.replace(/\s+/g, " ").trim();
  if (!t) return null;
  return t.length > MAX_TITLE_CHARS ? t.slice(0, MAX_TITLE_CHARS) : t;
}

export async function renameConversation(
  id: string,
  rawTitle: string,
  fetchImpl: typeof fetch = fetch,
): Promise<{ id: string; title: string }> {
  const title = cleanTitle(rawTitle);
  if (!title) throw new Error("A title can't be empty.");
  const res = await fetchImpl(`${API_URL}/api/conversations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (res.status === 401) {
    redirectToLogin();
    throw new Error("Authentication required");
  }
  if (!res.ok) throw new Error("I couldn't rename that conversation.");
  const data = (await res.json().catch(() => ({}))) as { id?: string; title?: string };
  return { id: data.id ?? id, title: data.title ?? title };
}
