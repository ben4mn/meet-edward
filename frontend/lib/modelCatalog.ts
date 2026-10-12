// The live model catalog (GET /api/settings/models) as the Model and
// Orchestrator panels read it. Pure; pinned in __tests__/modelCatalog.test.ts.
//
// Only an available provider's models are offered: the server lists the
// provider that is on as available — Anthropic, first, with the Claude
// routes' models (EDWARD_LLM_PROVIDER unset or anthropic), or the Codex
// fallback's catalog under "openai" (codex) — and the other as unavailable
// with no models. "(Recommended)" arrives inside the name and
// becomes a flag. A stored id the catalog does not list is still shown, as
// itself and marked current, so a select never pretends its first option
// is what is saved (the orchestrator's worker model showed "Haiku 4.5"
// over a stored gpt-5.6-terra).

import { reasonPhrase } from "./edwardMood";

export interface CatalogModel {
  id: string;
  name: string;
  description: string;
  effort: string | null;
  recommended: boolean;
}

export interface Catalog {
  provider: string;
  available: boolean;
  models: CatalogModel[];
  recommended: string | null;
  /** Per-role models and efforts, when the server reports them. */
  roles: {
    background: string | null;
    voice: string | null;
    mind: string | null;
    pulse: string | null;
    /** The model a chat turn really runs on (a retired saved id is rewritten). */
    chat: string | null;
    chatEffort: string | null;
    mindEffort: string | null;
    /** Each wake reason's effort, as the turn will send it. */
    mindEfforts: Record<string, string>;
    pulseEffort: string | null;
  };
}

const RECOMMENDED_SUFFIX = /\s*\(recommended\)\s*$/i;

function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

export function parseCatalog(raw: unknown): Catalog {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const providers = (r.providers && typeof r.providers === "object" ? r.providers : {}) as Record<
    string,
    { available?: boolean; models?: unknown[] }
  >;
  const catalogInfo = (r.catalog && typeof r.catalog === "object" ? r.catalog : {}) as Record<string, unknown>;
  const recommended = str(catalogInfo.recommended);
  // The available provider, else the first that lists anything (so a saved
  // model still reads by name while Codex is logged out).
  const entries = Object.entries(providers);
  const pick =
    entries.find(([, p]) => p?.available && Array.isArray(p.models) && p.models.length) ??
    entries.find(([k, p]) => k === "openai" && Array.isArray(p?.models) && p.models.length) ??
    null;
  const models: CatalogModel[] = [];
  if (pick) {
    for (const m of pick[1].models ?? []) {
      const o = (m && typeof m === "object" ? m : {}) as Record<string, unknown>;
      const id = str(o.id);
      if (!id) continue;
      const rawName = str(o.name) ?? id;
      models.push({
        id,
        name: rawName.replace(RECOMMENDED_SUFFIX, ""),
        description: str(o.description) ?? "",
        effort: str(o.default_effort),
        recommended: id === recommended || RECOMMENDED_SUFFIX.test(rawName),
      });
    }
  }
  return {
    provider: pick?.[0] ?? "openai",
    available: Boolean(pick?.[1]?.available),
    models,
    recommended: recommended ?? models.find((m) => m.recommended)?.id ?? null,
    roles: {
      background: str(r.background_model),
      voice: str(r.voice_model),
      mind: str(r.mind_model),
      pulse: str(r.pulse_model),
      chat: str(r.chat_model),
      chatEffort: str(r.chat_effort),
      mindEfforts: effortsMap(r.mind_efforts),
      mindEffort: str(r.mind_effort),
      pulseEffort: str(r.pulse_effort),
    },
  };
}

function effortsMap(v: unknown): Record<string, string> {
  if (!v || typeof v !== "object") return {};
  const out: Record<string, string> = {};
  for (const [k, e] of Object.entries(v as Record<string, unknown>)) {
    const s = str(e);
    if (s) out[k] = s;
  }
  return out;
}

export function modelName(c: Catalog, id: string): string {
  return c.models.find((m) => m.id === id)?.name ?? id;
}

/** Options for a model select: the catalog by name, and a stored id the
 *  catalog does not list first, marked "(current)". */
export function modelOptions(c: Catalog, stored: string | null | undefined): { value: string; label: string }[] {
  const opts = c.models.map((m) => ({ value: m.id, label: m.recommended ? `${m.name} (recommended)` : m.name }));
  if (stored && !c.models.some((m) => m.id === stored)) opts.unshift({ value: stored, label: `${stored} (current)` });
  return opts;
}

export interface RoleRow {
  role: string;
  model: string;
  effort: string | null;
  /** Where the role departs from its usual effort, in words. */
  detail?: string;
}

function joinAnd(parts: string[]): string {
  return parts.length <= 1 ? parts.join("") : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** "Answering a mention at medium · reflecting on the week at xhigh": the
 *  wake reasons whose effort differs from the mind's usual one, grouped by
 *  effort (lightest first). */
function mindEffortDetail(usual: string | null, efforts: Record<string, string>): string | undefined {
  const byEffort = new Map<string, string[]>();
  for (const [reason, effort] of Object.entries(efforts)) {
    if (effort === usual) continue;
    const words = reasonPhrase(reason);
    if (!words) continue;
    byEffort.set(effort, [...(byEffort.get(effort) ?? []), words]);
  }
  if (byEffort.size === 0) return undefined;
  const rank = (e: string) => {
    const i = EFFORT_ORDER.indexOf(e);
    return i < 0 ? EFFORT_ORDER.length : i;
  };
  const parts = Array.from(byEffort.entries())
    .sort(([a], [b]) => rank(a) - rank(b))
    .map(([effort, reasons]) => `${joinAnd(reasons.sort())} at ${effort}`);
  const text = parts.join(" · ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const EFFORT_ORDER = ["minimal", "low", "medium", "high", "xhigh"];

/** "Who thinks what": each role's model, only as far as the server says.
 *  Chat names the model its turns really run on when the server reports
 *  it, else the saved one. */
export function whoThinksWhat(c: Catalog, chatModel: string | null | undefined): RoleRow[] {
  const rows: RoleRow[] = [];
  const chat = c.roles.chat ?? chatModel;
  if (chat) rows.push({ role: "Chat", model: modelName(c, chat), effort: c.roles.chatEffort });
  if (c.roles.mind) {
    const row: RoleRow = { role: "Mind", model: modelName(c, c.roles.mind), effort: c.roles.mindEffort };
    const detail = mindEffortDetail(c.roles.mindEffort, c.roles.mindEfforts);
    if (detail) row.detail = detail;
    rows.push(row);
  }
  if (c.roles.pulse) rows.push({ role: "Surfaces", model: modelName(c, c.roles.pulse), effort: c.roles.pulseEffort });
  if (c.roles.voice) rows.push({ role: "Voice", model: modelName(c, c.roles.voice), effort: null });
  if (c.roles.background) rows.push({ role: "Background", model: modelName(c, c.roles.background), effort: null });
  return rows;
}
