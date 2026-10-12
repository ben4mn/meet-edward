"use client";

// Settings → Memory → Memories (2026-09-26, Jarvis polish).
//
// Search first (debounced, 16px), one Filter chip that opens a sheet (kind
// incl. intention, how sure, how long it holds, order), and the tiers as one
// segment. A card leads with how Edward came by it ("he heard / he read /
// he thought / he did") and whom it is about; tier and reinforcement are
// one muted line. The whole card opens "Where this came from" (provenance
// plus the details: importance, use); ?id= opens it directly. Deleting is
// undoable. Forget… previews, then asks once in a ConfirmSheet.
// Words live in lib/memoryView.ts; provenance and forget semantics in
// lib/provenance.ts and lib/forget.ts.

import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Eraser, SlidersHorizontal } from "lucide-react";
import { deleteMemory, searchMemories, type MemoryStats } from "@/lib/api";
import { confirmForget, getMemoryProvenance, previewForget } from "@/lib/productApi";
import { getMemoryById, type MemoryRecord } from "@/lib/memoryHouseApi";
import { provenanceRows, type MemoryProvenance } from "@/lib/provenance";
import {
  forgetPreviewSummary,
  forgetResultSummary,
  hasAnythingToForget,
  type ForgetPlan,
  type ForgetResult,
} from "@/lib/forget";
import {
  DEFAULT_FILTERS,
  MEMORY_TYPES,
  NATURES,
  TIERS,
  aboutLabel,
  activeFilterCount,
  compactCount,
  memoryEmpty,
  memoryMeta,
  natureLabel,
  normalizeQuery,
  originChip,
  statsLine,
  tierLabel,
  tierOptions,
  typeLabel,
  type MemoryFilters,
  type Tier,
} from "@/lib/memoryView";
import { clearUrlParams, readParam } from "@/lib/deepLink";
import { pendingDeletes } from "@/lib/pendingDeletes";
import { scheduleUndoableDelete } from "@/lib/undoableDelete";
import { formatDateTime, formatRelative } from "@/lib/time";
import { useUserTimezone } from "@/lib/useTimezone";
import {
  Button,
  Card,
  ConfirmSheet,
  EmptyState,
  Field,
  Input,
  Notice,
  Pill,
  SectionLabel,
  Segment,
  Sheet,
  Skeleton,
  Switch,
  cn,
  useToast,
} from "@/components/ui";
import { ChoiceChips, DetailList, SearchField, type DetailRow } from "./panelBits";

const PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 300;
const deleteKey = (id: string) => `memory:${id}`;

export interface MemoryBrowserProps {
  /** Open this memory's "Where this came from" (tests; the page uses ?id=). */
  initialId?: string | null;
  /** Legacy accordion props from settingsConfig; ignored. */
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function MemoryBrowser(props: MemoryBrowserProps) {
  return (
    <Suspense fallback={<ListSkeleton />}>
      <MemoryBrowserBody initialId={props.initialId} />
    </Suspense>
  );
}

function ListSkeleton() {
  return (
    <div className="space-y-2" aria-hidden>
      {[0, 1, 2].map((i) => (
        <Card key={i} className="p-4">
          <Skeleton lines={3} />
        </Card>
      ))}
    </div>
  );
}

function useHiddenIds(): ReadonlySet<string> {
  const [snap, setSnap] = useState(pendingDeletes.pending());
  useEffect(() => pendingDeletes.subscribe(() => setSnap(pendingDeletes.pending())), []);
  return snap;
}

function MemoryBrowserBody({ initialId }: { initialId?: string | null }) {
  const tz = useUserTimezone();
  const params = useSearchParams();
  const deepId = initialId ?? readParam(params, "id");
  const { toast } = useToast();

  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [filters, setFilters] = useState<MemoryFilters>(DEFAULT_FILTERS);
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filterOpen, setFilterOpen] = useState(false);
  const [forgetOpen, setForgetOpen] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const seq = useRef(0);
  const hidden = useHiddenIds();

  useEffect(() => {
    const t = setTimeout(() => setDebounced(normalizeQuery(query)), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query]);

  const fetchPage = useCallback(
    (offset: number) =>
      searchMemories(
        debounced || undefined,
        filters.type === "all" ? undefined : filters.type,
        undefined,
        PAGE_SIZE,
        offset,
        filters.nature === "all" ? undefined : filters.nature,
        filters.tier === "all" ? undefined : filters.tier,
      ),
    [debounced, filters.type, filters.nature, filters.tier],
  );

  const load = useCallback(async () => {
    const mine = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const res = await fetchPage(0);
      if (mine !== seq.current) return;
      setMemories(res.memories as MemoryRecord[]);
      setStats(res.stats);
      setTotal(res.pagination.total);
    } catch {
      if (mine === seq.current) setError("I couldn't open my memory just now.");
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [fetchPage]);

  useEffect(() => {
    load();
  }, [load]);

  const loadMore = async () => {
    const mine = seq.current;
    setLoadingMore(true);
    try {
      const res = await fetchPage(memories.length);
      if (mine !== seq.current) return;
      setMemories((prev) => {
        const seen = new Set(prev.map((m) => m.id));
        return [...prev, ...(res.memories as MemoryRecord[]).filter((m) => !seen.has(m.id))];
      });
      setTotal(res.pagination.total);
    } catch {
      toast({ title: "I couldn't load more just now.", tone: "bad" });
    } finally {
      setLoadingMore(false);
    }
  };

  // ?id= opens that memory once.
  const openedDeep = useRef<string | null>(null);
  useEffect(() => {
    if (deepId && openedDeep.current !== deepId) {
      openedDeep.current = deepId;
      setOpenId(deepId);
    }
  }, [deepId]);

  const closeSheet = () => {
    setOpenId(null);
    clearUrlParams(["id"]);
  };

  const visible = useMemo(() => {
    const rows = memories.filter((m) => !hidden.has(deleteKey(m.id)));
    if (filters.sort === "recent") return rows;
    return [...rows].sort(
      (a, b) =>
        b.reinforcement_count - a.reinforcement_count ||
        (b.created_at ? Date.parse(b.created_at) : 0) - (a.created_at ? Date.parse(a.created_at) : 0),
    );
  }, [memories, hidden, filters.sort]);

  const removeMemory = (m: MemoryRecord) => {
    closeSheet();
    setTotal((t) => Math.max(0, t - 1));
    scheduleUndoableDelete({
      key: deleteKey(m.id),
      commit: () => deleteMemory(m.id),
      onCommit: () => setMemories((ms) => ms.filter((x) => x.id !== m.id)),
      onError: () => {
        toast({ title: "I couldn't delete that memory. It's back.", tone: "bad" });
        void load();
      },
      onUndone: () => setTotal((t) => t + 1),
      toast,
      title: "Memory deleted",
      body: m.content.length > 60 ? `${m.content.slice(0, 60)}…` : m.content,
      tooLate: "Too late — that memory is already deleted.",
    });
  };

  const filterCount = activeFilterCount(filters);
  const filtered = filterCount > 0 || filters.tier !== "all";
  const empty = memoryEmpty(debounced, filtered);
  const openMemory = memories.find((m) => m.id === openId) ?? null;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <SearchField value={query} onChange={setQuery} label="Search memories" placeholder="Search memories" />
        <Button
          variant="secondary"
          onClick={() => setFilterOpen(true)}
          aria-label={filterCount ? `Filter · ${filterCount} on` : "Filter"}
          icon={<SlidersHorizontal aria-hidden />}
          className={cn("h-11 flex-none rounded-full px-3.5 md:h-11", filterCount > 0 && "border-text-primary/30")}
        >
          <span>Filter</span>
          {filterCount > 0 ? (
            <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-text-primary px-1.5 text-caption text-primary-bg">
              {filterCount}
            </span>
          ) : null}
        </Button>
      </div>

      <Segment<Tier>
        label="How sure he is"
        value={filters.tier}
        onChange={(tier) => setFilters((f) => ({ ...f, tier }))}
        options={tierOptions(stats).map((o) => ({
          value: o.value,
          label: (
            <span className="flex flex-col items-center leading-tight">
              <span>{o.label}</span>{" "}
              <span className="text-caption font-normal tabular-nums opacity-70">{o.count == null ? "–" : compactCount(o.count)}</span>
            </span>
          ),
        }))}
      />

      <SectionLabel
        action={
          <span className="inline-flex items-center gap-1.5">
            <Eraser className="h-3.5 w-3.5" aria-hidden />
            Forget…
          </span>
        }
        onAction={() => setForgetOpen(true)}
      >
        {statsLine(stats) || "What I remember"}
      </SectionLabel>

      {error ? (
        <Notice
          tone="bad"
          action={
            <Button size="sm" variant="secondary" onClick={load}>
              Try again
            </Button>
          }
        >
          {error}
        </Notice>
      ) : null}

      {loading && memories.length === 0 ? (
        <ListSkeleton />
      ) : !error && visible.length === 0 ? (
        <EmptyState title={empty.title}>{empty.body}</EmptyState>
      ) : (
        <div className={cn("space-y-2 transition-opacity duration-fast", loading && "opacity-60")}>
          {visible.map((m) => (
            <MemoryCard key={m.id} memory={m} tz={tz} onOpen={() => setOpenId(m.id)} />
          ))}
        </div>
      )}

      {memories.length > 0 && memories.length < total ? (
        <div className="space-y-2 pt-1 text-center">
          <Button variant="secondary" className="w-full" loading={loadingMore} onClick={loadMore}>
            Show more
          </Button>
          <p className="text-caption text-text-muted tabular-nums">
            Showing {visible.length.toLocaleString("en-US")} of {total.toLocaleString("en-US")}
          </p>
        </div>
      ) : null}

      <FilterSheet
        open={filterOpen}
        onClose={() => setFilterOpen(false)}
        filters={filters}
        onChange={setFilters}
      />
      <ProvenanceSheet
        id={openId}
        memory={openMemory}
        tz={tz}
        onClose={closeSheet}
        onDelete={removeMemory}
      />
      <ForgetSheet open={forgetOpen} onClose={() => setForgetOpen(false)} onForgotten={load} />
    </div>
  );
}

// ------------------------------------------------------------ card
function MemoryCard({ memory: m, tz, onOpen }: { memory: MemoryRecord; tz: string; onOpen: () => void }) {
  const chip = originChip(m.origin);
  return (
    <Card onClick={onOpen} className="p-4">
      <span className="flex items-center gap-2 text-footnote text-text-muted">
        <Pill tone={chip.tone}>{chip.label}</Pill>
        <span>{aboutLabel(m.about)}</span>
        {m.score != null && m.score > 0 ? (
          <span className="ml-auto text-caption tabular-nums text-text-muted">{Math.round(m.score * 100)}% match</span>
        ) : null}
      </span>
      <span className="mt-2 block text-body text-text-primary [overflow-wrap:anywhere]">{m.content}</span>
      <span className="mt-1.5 block text-footnote text-text-muted">{memoryMeta(m, { tz })}</span>
    </Card>
  );
}

// ------------------------------------------------------------ filter sheet
function FilterSheet({
  open,
  onClose,
  filters,
  onChange,
}: {
  open: boolean;
  onClose: () => void;
  filters: MemoryFilters;
  onChange: (f: MemoryFilters) => void;
}) {
  const set = <K extends keyof MemoryFilters>(k: K, v: MemoryFilters[K]) => onChange({ ...filters, [k]: v });
  const any = activeFilterCount(filters) > 0 || filters.tier !== "all";
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Filter memories"
      footer={
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => onChange(DEFAULT_FILTERS)} disabled={!any}>
            Clear
          </Button>
          <Button className="flex-1" onClick={onClose}>
            Done
          </Button>
        </div>
      }
    >
      <div className="space-y-5 pb-2">
        <ChoiceChips
          label="Kind"
          value={filters.type}
          onChange={(v) => set("type", v)}
          options={MEMORY_TYPES.map((t) => ({ value: t, label: t === "all" ? "All" : typeLabel(t) }))}
        />
        <ChoiceChips
          label="How sure he is"
          value={filters.tier}
          onChange={(v) => set("tier", v)}
          options={TIERS.map((t) => ({ value: t, label: t === "all" ? "All" : tierLabel(t) }))}
        />
        <ChoiceChips
          label="How long it holds"
          value={filters.nature}
          onChange={(v) => set("nature", v)}
          options={NATURES.map((n) => ({ value: n, label: n === "all" ? "All" : natureLabel(n) }))}
        />
        <div className="space-y-2">
          <p className="text-footnote font-medium text-text-secondary">Order</p>
          <Segment
            label="Order"
            value={filters.sort}
            onChange={(v) => set("sort", v)}
            options={[
              { value: "recent", label: "Newest" },
              { value: "reinforced", label: "Most reinforced" },
            ]}
          />
        </div>
      </div>
    </Sheet>
  );
}

// ------------------------------------------------------------ provenance sheet
type Loaded<T> = { status: "loading" } | { status: "ok"; data: T } | { status: "error" };

function ProvenanceSheet({
  id,
  memory,
  tz,
  onClose,
  onDelete,
}: {
  id: string | null;
  memory: MemoryRecord | null;
  tz: string;
  onClose: () => void;
  onDelete: (m: MemoryRecord) => void;
}) {
  const [prov, setProv] = useState<Loaded<MemoryProvenance>>({ status: "loading" });
  const [fetched, setFetched] = useState<MemoryRecord | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    setProv({ status: "loading" });
    getMemoryProvenance(id)
      .then((data) => alive && setProv({ status: "ok", data }))
      .catch(() => alive && setProv({ status: "error" }));
    return () => {
      alive = false;
    };
  }, [id, attempt]);

  useEffect(() => {
    if (!id || memory) {
      setFetched(null);
      return;
    }
    let alive = true;
    getMemoryById(id)
      .then((m) => alive && setFetched(m))
      .catch(() => alive && setFetched(null));
    return () => {
      alive = false;
    };
  }, [id, memory]);

  const m = memory ?? fetched;
  const p = prov.status === "ok" ? prov.data : null;
  const content = m?.content ?? p?.memory.content ?? "";
  const chip = originChip(m?.origin ?? p?.memory.origin);
  const about = aboutLabel(m?.about ?? p?.memory.about);

  const details: DetailRow[] = [];
  if (m) {
    details.push({ label: "Kind", value: typeLabel(m.memory_type) });
    details.push({
      label: "How sure",
      value: `${tierLabel(m.tier)}${m.reinforcement_count > 0 ? ` · reinforced ${m.reinforcement_count}×` : ""}`,
    });
    if (m.temporal_nature) details.push({ label: "Holds", value: natureLabel(m.temporal_nature) });
    details.push({ label: "Importance", value: `${Math.round((m.importance ?? 0) * 100)}%` });
    details.push({
      label: "Used",
      value:
        m.access_count > 0
          ? `${m.access_count} ${m.access_count === 1 ? "time" : "times"}${m.last_accessed ? ` · last ${formatRelative(m.last_accessed)}` : ""}`
          : "Not yet",
    });
    if (m.created_at) details.push({ label: "Learned", value: formatDateTime(m.created_at, { tz }) });
    if (m.updated_at && m.updated_at !== m.created_at) details.push({ label: "Updated", value: formatDateTime(m.updated_at, { tz }) });
  }

  return (
    <Sheet
      open={Boolean(id)}
      onClose={onClose}
      title="Where this came from"
      footer={
        <div className="flex items-center gap-2">
          <Button variant="ghost" className="text-bad hover:text-bad" disabled={!m} onClick={() => m && onDelete(m)}>
            Delete
          </Button>
          <div className="flex-1" />
          <Button variant="secondary" onClick={onClose}>
            Done
          </Button>
        </div>
      }
    >
      <div className="space-y-5 pb-2">
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-footnote text-text-muted">
            <Pill tone={chip.tone}>{chip.label}</Pill>
            <span>{about}</span>
          </div>
          {content ? (
            <p className="text-body text-text-primary [overflow-wrap:anywhere]">{content}</p>
          ) : (
            <Skeleton lines={2} />
          )}
        </div>

        {prov.status === "loading" ? (
          <Skeleton lines={3} />
        ) : prov.status === "error" ? (
          <Notice
            tone="bad"
            action={
              <Button size="sm" variant="secondary" onClick={() => setAttempt((a) => a + 1)}>
                Try again
              </Button>
            }
          >
            I couldn&apos;t read the records for this one.
          </Notice>
        ) : (
          <div className="space-y-3">
            <p className="text-footnote text-text-secondary">{prov.data.summary}</p>
            {/* "How" is the chip above; "Stored" is "Learned" below, in the owner's zone. */}
            <DetailList rows={provenanceRows(prov.data).filter((r) => r.label !== "How" && r.label !== "Stored")} />
          </div>
        )}

        {details.length ? (
          <div className="space-y-2">
            <SectionLabel>Details</SectionLabel>
            <DetailList rows={details} />
          </div>
        ) : null}
      </div>
    </Sheet>
  );
}

// ------------------------------------------------------------ forget
function ForgetSheet({ open, onClose, onForgotten }: { open: boolean; onClose: () => void; onForgotten: () => void }) {
  const [subject, setSubject] = useState("");
  const [plan, setPlan] = useState<ForgetPlan | null>(null);
  const [includeDocs, setIncludeDocs] = useState(false);
  const [result, setResult] = useState<ForgetResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const close = () => {
    setSubject("");
    setPlan(null);
    setIncludeDocs(false);
    setResult(null);
    setError(null);
    setConfirming(false);
    onClose();
  };

  const preview = async (e: React.FormEvent) => {
    e.preventDefault();
    const s = subject.trim();
    if (!s) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setPlan(await previewForget(s));
    } catch (err) {
      setError(err instanceof Error ? err.message : "I couldn't check that just now.");
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!plan) return;
    setError(null);
    try {
      setResult(await confirmForget(plan, includeDocs));
      setPlan(null);
      onForgotten();
    } catch (err) {
      setError(err instanceof Error ? err.message : "I couldn't forget that just now.");
    } finally {
      setConfirming(false);
    }
  };

  return (
    <>
      <Sheet
        open={open}
        onClose={close}
        title="Forget someone or something"
        description="I'll show you exactly what would go. Nothing is deleted until you confirm."
        footer={
          plan ? (
            <Button
              variant="danger"
              className="w-full"
              disabled={!hasAnythingToForget(plan, includeDocs)}
              onClick={() => setConfirming(true)}
            >
              Forget all of it…
            </Button>
          ) : undefined
        }
      >
        <div className="space-y-4 pb-2">
          <form onSubmit={preview} className="space-y-2">
            <Field label="Who or what should I forget?" hint="A person or a topic, e.g. Alex">
              <Input
                value={subject}
                onChange={(e) => {
                  setSubject(e.target.value);
                  setPlan(null);
                  setResult(null);
                }}
                autoComplete="off"
              />
            </Field>
            <Button type="submit" variant="secondary" loading={busy} disabled={!subject.trim()} className="w-full">
              Preview
            </Button>
          </form>

          {error ? <Notice tone="bad">{error}</Notice> : null}

          {plan ? (
            <div className="space-y-3">
              <p className="text-body text-text-primary">{forgetPreviewSummary(plan, includeDocs)}</p>
              {plan.memories.length > 0 ? (
                <ul className="divide-y divide-border rounded-md border border-border bg-surface">
                  {plan.memories.map((m) => (
                    <li key={m.id} className="px-3 py-2.5 text-footnote">
                      <span className="text-text-primary">{m.content}</span>{" "}
                      <span className="text-text-muted">· {m.why}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {plan.documents.length > 0 ? (
                <div className="flex items-start gap-3 rounded-md border border-border bg-surface px-3 py-2.5">
                  <p className="min-w-0 flex-1 text-footnote text-text-secondary">
                    Also delete {plan.documents.map((d) => `“${d.title}”`).join(", ")}
                  </p>
                  <Switch checked={includeDocs} onChange={setIncludeDocs} label="Also delete the documents" />
                </div>
              ) : null}
              {plan.protected_documents.length > 0 ? (
                <Notice tone="info">My state page mentions it too — ask me to edit that line out.</Notice>
              ) : null}
              {plan.conversations.count > 0 ? (
                <p className="text-footnote text-text-muted">
                  {plan.conversations.count} conversation{plan.conversations.count === 1 ? "" : "s"} mention it (
                  {plan.conversations.titles.join("; ")}). They stay — delete them from the sidebar if you want them gone.
                </p>
              ) : null}
            </div>
          ) : null}

          {result ? <Notice tone="ok">{forgetResultSummary(result)}</Notice> : null}
        </div>
      </Sheet>
      <ConfirmSheet
        open={confirming && Boolean(plan)}
        onClose={() => setConfirming(false)}
        onConfirm={confirm}
        title={`Forget “${plan?.subject ?? subject}”?`}
        body={plan ? `${forgetPreviewSummary(plan, includeDocs)} This can't be undone.` : undefined}
        confirmLabel="Forget"
      />
    </>
  );
}
