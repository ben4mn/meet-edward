// "What does Edward have on…" — the Memory room's search across memories,
// documents, files and databases. Pure; pinned in __tests__/knowledgeModel.test.ts.
//
// Memories and documents are searched by the server. Files and databases
// have no server search, so the whole list is fetched (files: the API's
// maximum page) and matched here word by word. Every hit deep-links into
// its panel: ?id=<id>, and ?name=<name> for a database (the API addresses
// databases by name).

import { tierLabel, typeLabel } from "@/lib/memoryView";
import { docDisplay, docKind } from "@/lib/documentView";

export type KnowledgeKind = "memory" | "doc" | "file" | "db";

export interface KnowledgeHit {
  id: string;
  kind: KnowledgeKind;
  title: string;
  detail: string;
  href: string;
  at?: string | null;
}

/** GET /api/files caps `limit` at 100. */
export const FILE_FETCH_LIMIT = 100;
/** Hits per kind the search asks the server for. */
export const SERVER_HIT_LIMIT = 10;

export function queryTokens(query: string): string[] {
  return (query ?? "")
    .toLowerCase()
    .split(/\s+/)
    .map((t) => t.trim())
    .filter(Boolean);
}

export function matchesQuery(text: string, query: string): boolean {
  const tokens = queryTokens(query);
  if (tokens.length === 0) return true;
  const hay = (text ?? "").toLowerCase();
  return tokens.every((t) => hay.includes(t));
}

const PANEL: Record<KnowledgeKind, string> = {
  memory: "/settings/memories",
  doc: "/settings/documents",
  file: "/settings/files",
  db: "/settings/databases",
};

export function hitHref(kind: KnowledgeKind, key: string): string {
  const param = kind === "db" ? "name" : "id";
  return `${PANEL[kind]}?${param}=${encodeURIComponent(key)}`;
}

function time(iso: string | null | undefined): number {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? 0 : t;
}

function sizeLabel(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function capitalise(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

export interface FileLike {
  id: string;
  filename: string;
  description?: string | null;
  tags?: string | null;
  category?: string | null;
  created_at?: string | null;
  size_bytes?: number;
}

export function fileHits(files: readonly FileLike[], query: string, limit = 8): KnowledgeHit[] {
  return files
    .filter((f) => matchesQuery(`${f.filename} ${f.description ?? ""} ${f.tags ?? ""} ${f.category ?? ""}`, query))
    .sort((a, b) => time(b.created_at) - time(a.created_at))
    .slice(0, limit)
    .map((f) => ({
      id: `f:${f.id}`,
      kind: "file" as const,
      title: f.filename,
      detail: [f.description || capitalise(f.category ?? "file"), f.size_bytes != null ? sizeLabel(f.size_bytes) : ""]
        .filter(Boolean)
        .join(" · "),
      href: hitHref("file", f.id),
      at: f.created_at ?? null,
    }));
}

export interface DbLike {
  id: string;
  name: string;
  description?: string | null;
  updated_at?: string | null;
}

export function dbHits(dbs: readonly DbLike[], query: string): KnowledgeHit[] {
  return dbs
    .filter((d) => matchesQuery(`${d.name} ${d.description ?? ""}`, query))
    .map((d) => ({
      id: `db:${d.id}`,
      kind: "db" as const,
      title: d.name,
      detail: d.description || "Database",
      href: hitHref("db", d.name),
      at: d.updated_at ?? null,
    }));
}

export interface MemoryLike {
  id: string;
  content: string;
  memory_type: string;
  tier?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
}

export function memoryHits(memories: readonly MemoryLike[]): KnowledgeHit[] {
  return memories.map((m) => ({
    id: `m:${m.id}`,
    kind: "memory" as const,
    title: m.content,
    // The Memories panel's words ("Known / Believed / Noticed"), never the
    // backend's tier names.
    detail: [typeLabel(m.memory_type), m.tier ? tierLabel(m.tier) : null].filter(Boolean).join(" · "),
    href: hitHref("memory", m.id),
    at: m.updated_at ?? m.created_at ?? null,
  }));
}

export interface DocLike {
  id: string;
  title: string;
  tags?: string | null;
  updated_at?: string | null;
}

export function docHits(docs: readonly DocLike[]): KnowledgeHit[] {
  return docs.map((d) => {
    // The Documents panel's names: "My state" for his own page, a goal
    // without its "Goal:" prefix.
    const shown = docDisplay(d.title);
    const kind = docKind(d.title);
    const tags =
      (d.tags ?? "")
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean)
        .join(", ") || "Document";
    return {
      id: `d:${d.id}`,
      kind: "doc" as const,
      title: shown.title,
      detail: kind === "state" ? "His own page" : shown.badge ?? tags,
      href: hitHref("doc", d.id),
      at: d.updated_at ?? null,
    };
  });
}

export function kindCounts(hits: readonly KnowledgeHit[]): Record<KnowledgeKind | "all", number> {
  const out: Record<KnowledgeKind | "all", number> = { all: hits.length, memory: 0, doc: 0, file: 0, db: 0 };
  for (const h of hits) out[h.kind] += 1;
  return out;
}
