"use client";

// "What does Edward have on…?" — memories, documents, files and databases
// at once (the Memory room's live view; the landing's Knows field sends its
// query here as ?q=). Memories and documents are searched by the server;
// files and databases are fetched whole once and matched word by word
// (knowledgeModel), so an old file is found, not just the newest eight.
// Results stay on screen while a new query loads; every hit opens its
// panel on that item (?id=, databases ?name=).

import React, { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Brain, Database, FileText, FolderOpen, Search, type LucideIcon } from "lucide-react";
import {
  listDatabases,
  listFiles,
  searchDocuments,
  searchMemories,
  type PersistentDatabase,
  type StoredFileItem,
} from "@/lib/api";
import { formatRelative } from "@/lib/time";
import { CONTROL, EmptyState, Notice, Pulse, Row, Segment, Skeleton } from "@/components/ui";
import { cn } from "@/lib/utils";
import {
  FILE_FETCH_LIMIT,
  SERVER_HIT_LIMIT,
  dbHits,
  docHits,
  fileHits,
  kindCounts,
  memoryHits,
  type KnowledgeHit,
  type KnowledgeKind,
} from "./knowledgeModel";

type Filter = "all" | KnowledgeKind;

const ICON: Record<KnowledgeKind, LucideIcon> = {
  memory: Brain,
  doc: FileText,
  file: FolderOpen,
  db: Database,
};

const GROUPS: { kind: KnowledgeKind; label: string; panel: string }[] = [
  { kind: "memory", label: "Memories", panel: "/settings/memories" },
  { kind: "doc", label: "Documents", panel: "/settings/documents" },
  { kind: "file", label: "Files", panel: "/settings/files" },
  { kind: "db", label: "Data", panel: "/settings/databases" },
];

/** In the All view each kind shows this many before "See all". */
const GROUP_PREVIEW = 4;

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

function HitRow({ h }: { h: KnowledgeHit }) {
  const Icon = ICON[h.kind];
  return (
    <Row
      href={h.href}
      icon={<Icon aria-hidden />}
      title={h.title}
      detail={h.at ? `${h.detail} · ${formatRelative(h.at)}` : h.detail}
    />
  );
}

const SOURCE_WORDS: Record<string, string> = { memories: "memories", documents: "documents", files: "files", data: "saved data" };

/** "I can't search memories right now." / "…memories or documents…". */
function cantSearch(failed: string[]): string {
  const words = failed.map((f) => SOURCE_WORDS[f] ?? f);
  const list = words.length > 1 ? `${words.slice(0, -1).join(", ")} or ${words[words.length - 1]}` : words[0];
  return `I can't search ${list} right now — the rest is below. Try again in a moment.`;
}

export function KnowledgeSearch({ initialQuery = "" }: { initialQuery?: string }) {
  const [q, setQ] = useState(initialQuery);
  const [filter, setFilter] = useState<Filter>("all");
  const [hits, setHits] = useState<KnowledgeHit[] | null>(null);
  const [loading, setLoading] = useState(true);
  // Sources that failed on the last search: said in his voice, never
  // rendered as "nothing on it" (review 2026-09-26, failures-rendered-as-empty).
  const [failed, setFailed] = useState<string[]>([]);
  const query = useDebounced(q.trim(), 250);
  const lists = useRef<Promise<{ files: StoredFileItem[] | null; dbs: PersistentDatabase[] | null }> | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    if (!lists.current) {
      lists.current = Promise.all([
        listFiles(undefined, undefined, FILE_FETCH_LIMIT)
          .then((r) => r.files ?? [])
          .catch(() => null),
        listDatabases().catch(() => null),
      ]).then(([files, dbs]) => ({ files, dbs: dbs === null ? null : Array.isArray(dbs) ? dbs : [] }));
    }
    (async () => {
      const [mem, docs, whole] = await Promise.all([
        searchMemories(query || undefined, undefined, undefined, SERVER_HIT_LIMIT).catch(() => null),
        searchDocuments(query || undefined, undefined, SERVER_HIT_LIMIT).catch(() => null),
        lists.current!,
      ]);
      if (cancelled) return;
      setFailed(
        [
          mem === null ? "memories" : null,
          docs === null ? "documents" : null,
          whole.files === null ? "files" : null,
          whole.dbs === null ? "data" : null,
        ].filter((x): x is string => x !== null),
      );
      // A list that failed once is fetched again on the next search.
      if (whole.files === null || whole.dbs === null) lists.current = null;
      setHits([
        ...memoryHits(mem?.memories ?? []),
        ...docHits(docs?.documents ?? []),
        ...fileHits(whole.files ?? [], query, query ? 10 : 5),
        ...dbHits(whole.dbs ?? [], query),
      ]);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [query]);

  const counts = useMemo(() => kindCounts(hits ?? []), [hits]);
  const shown = useMemo(() => (hits ?? []).filter((h) => filter === "all" || h.kind === filter), [hits, filter]);
  const refreshing = loading && hits !== null;
  // With no query the lists are previews (the newest few), so a count would mislead.
  const showCounts = Boolean(hits && query);

  return (
    <div className="space-y-3">
      <label className="relative block">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" aria-hidden />
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="What does he have on…"
          aria-label="Search everything Edward knows"
          enterKeyHint="search"
          className={cn(CONTROL, "min-h-[44px] py-2.5 pl-9 pr-10")}
        />
        {refreshing ? (
          <span className="absolute right-3 top-1/2 -translate-y-1/2">
            <Pulse size="sm" label="Searching" />
          </span>
        ) : null}
      </label>

      <Segment
        value={filter}
        onChange={setFilter}
        label="Kind"
        options={[
          { value: "all", label: "All" },
          { value: "memory", label: "Memories", count: showCounts ? counts.memory : undefined },
          { value: "doc", label: "Docs", count: showCounts ? counts.doc : undefined },
          { value: "file", label: "Files", count: showCounts ? counts.file : undefined },
          { value: "db", label: "Data", count: showCounts ? counts.db : undefined },
        ]}
      />

      {hits !== null && failed.length > 0 && failed.length < 4 && shown.length > 0 ? (
        <Notice tone="warn">{cantSearch(failed)}</Notice>
      ) : null}

      <div
        aria-busy={loading}
        className={cn(
          "rounded-lg border border-border bg-surface px-3 shadow-e1 transition-opacity duration-base",
          refreshing && "opacity-60",
        )}
      >
        {hits === null ? (
          <div className="py-4">
            <Pulse label="Searching what he knows" className="sr-only" />
            <Skeleton lines={4} />
          </div>
        ) : failed.length === 4 ? (
          <Notice tone="warn" className="my-3">
            {"I can't search what I know right now — the Mac didn't answer. Try again in a moment."}
          </Notice>
        ) : shown.length === 0 && failed.length > 0 ? (
          <Notice tone="warn" className="my-3">
            {cantSearch(failed)}
          </Notice>
        ) : shown.length === 0 ? (
          <EmptyState title={query ? `Nothing on “${query}”` : "Nothing here yet"}>
            {query ? "Ask me in chat — I might know it another way." : "When I remember something, it shows up here."}
          </EmptyState>
        ) : filter === "all" ? (
          GROUPS.filter((g) => counts[g.kind] > 0).map((g) => {
            const rows = shown.filter((h) => h.kind === g.kind);
            return (
              <section key={g.kind} aria-label={g.label} className="border-b border-border py-1 last:border-b-0">
                <div className="flex items-center justify-between px-1 pb-0.5 pt-2.5">
                  <h3 className="text-overline text-text-muted">{g.label}</h3>
                  {!query ? (
                    // A preview of the newest few: its length is not how
                    // many he has, so it opens the kind's own panel.
                    <Link
                      href={g.panel}
                      className="-my-3 -mr-2 inline-flex min-h-[44px] items-center rounded-md px-2 text-footnote font-medium text-text-secondary hover:text-text-primary"
                    >
                      See all
                    </Link>
                  ) : rows.length > GROUP_PREVIEW ? (
                    <button
                      type="button"
                      onClick={() => setFilter(g.kind)}
                      className="-my-3 -mr-2 inline-flex min-h-[44px] items-center rounded-md px-2 text-footnote font-medium text-text-secondary hover:text-text-primary"
                    >
                      All {rows.length}
                    </button>
                  ) : null}
                </div>
                {rows.slice(0, GROUP_PREVIEW).map((h) => (
                  <HitRow key={h.id} h={h} />
                ))}
              </section>
            );
          })
        ) : (
          shown.map((h) => <HitRow key={h.id} h={h} />)
        )}
      </div>
    </div>
  );
}
