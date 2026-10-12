// Fetches for provenance and the verified forget (plan Phase 5,
// 2026-09-24; the goals and ideas ones went with unit G, 2026-10-11). Kept out of lib/api.ts so this wave's pages do not
// collide with other agents' edits there; the auth behaviour is the same
// (credentials included, a 401 goes to /login once — lib/authSession.ts).

import { redirectToLogin } from "./authSession";
import type { MemoryProvenance } from "./provenance";
import type { ForgetPlan, ForgetResult } from "./forget";

async function call<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    ...init,
    credentials: "include",
    headers: init.body ? { "Content-Type": "application/json", ...(init.headers ?? {}) } : init.headers,
  });
  if (response.status === 401) {
    redirectToLogin();
    throw new Error("Authentication required");
  }
  if (!response.ok) {
    let detail = "";
    try {
      detail = (await response.json()).detail ?? "";
    } catch {
      /* not JSON */
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

// Memory: provenance and forget ----------------------------------------------

export function getMemoryProvenance(id: string): Promise<MemoryProvenance> {
  return call(`/api/memories/${encodeURIComponent(id)}/provenance`);
}

export async function previewForget(subject: string): Promise<ForgetPlan> {
  const out = await call<{ plan: ForgetPlan }>("/api/memories/forget", {
    method: "POST",
    body: JSON.stringify({ subject, confirm: false }),
  });
  return out.plan;
}

export async function confirmForget(plan: ForgetPlan, includeDocuments: boolean): Promise<ForgetResult> {
  const out = await call<{ result: ForgetResult }>("/api/memories/forget", {
    method: "POST",
    body: JSON.stringify({ subject: plan.subject, plan_id: plan.id, confirm: true, include_documents: includeDocuments }),
  });
  return out.result;
}
