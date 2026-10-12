"use client";

// Settings → Memory → Documents (2026-09-26, Jarvis polish).
//
// The pages Edward keeps whole: recipes, protocols, trip plans, his own
// state page and the owner's goals. His state page ("Edward: state") and the
// goals ("Goal: …") are pinned to the top. Search is debounced; tag chips
// filter. A document opens as a reading view — rendered markdown at full
// height, no inner scroll box — and ?id= opens one directly. Deleting is
// undoable. Words live in lib/documentView.ts.

import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { FileText, Plus, Target, Sparkles } from "lucide-react";
import {
  createDocument,
  deleteDocument,
  getDocument,
  searchDocuments,
  type DocumentItem,
  type DocumentStats,
} from "@/lib/api";
import {
  docDisplay,
  docKind,
  docPreview,
  opensWithTitle,
  splitPinned,
  tagList,
  tagOptions,
} from "@/lib/documentView";
import { normalizeQuery } from "@/lib/memoryView";
import { clearUrlParams, readParam } from "@/lib/deepLink";
import { pendingDeletes } from "@/lib/pendingDeletes";
import { scheduleUndoableDelete } from "@/lib/undoableDelete";
import { formatRelative } from "@/lib/time";
import { MarkdownContent } from "@/components/chat/MarkdownContent";
import {
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Notice,
  Pill,
  SectionLabel,
  Sheet,
  Skeleton,
  Textarea,
  cn,
  useToast,
} from "@/components/ui";
import { SearchField } from "./panelBits";

const PAGE_SIZE = 50;
const deleteKey = (id: string) => `document:${id}`;

export interface DocumentBrowserProps {
  /** Tests; the page reads ?id=. */
  initialId?: string | null;
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function DocumentBrowser(props: DocumentBrowserProps) {
  return (
    <Suspense fallback={<ListSkeleton />}>
      <DocumentsBody initialId={props.initialId} />
    </Suspense>
  );
}

function ListSkeleton() {
  return (
    <div className="space-y-2" aria-hidden>
      {[0, 1, 2].map((i) => (
        <Card key={i} className="p-4">
          <Skeleton lines={2} />
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

/** The nearest scrolling ancestor (the settings column scrolls, not the window). */
function scrollParent(el: HTMLElement | null): HTMLElement | null {
  let p = el?.parentElement ?? null;
  while (p) {
    const y = getComputedStyle(p).overflowY;
    if (y === "auto" || y === "scroll") return p;
    p = p.parentElement;
  }
  return null;
}

/** Opening a document is a step: its own history entry (Next's router
 *  picks the pushed URL up), so Back or the edge swipe returns to the list
 *  instead of leaving the panel. */
function pushUrlId(id: string) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  url.searchParams.set("id", id);
  window.history.pushState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function DocumentsBody({ initialId }: { initialId?: string | null }) {
  const params = useSearchParams();
  const deepId = initialId ?? readParam(params, "id");
  const { toast } = useToast();
  const hidden = useHiddenIds();

  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [tag, setTag] = useState<string | null>(null);
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [stats, setStats] = useState<DocumentStats | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [viewId, setViewId] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const seq = useRef(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const listScroll = useRef(0);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(normalizeQuery(query)), 300);
    return () => clearTimeout(t);
  }, [query]);

  const load = useCallback(async () => {
    const mine = ++seq.current;
    setLoading(true);
    setError(false);
    try {
      const res = await searchDocuments(debounced || undefined, tag ?? undefined, PAGE_SIZE, 0);
      if (mine !== seq.current) return;
      setDocs(res.documents);
      setStats(res.stats);
      setTotal(res.pagination.total);
    } catch {
      if (mine === seq.current) setError(true);
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [debounced, tag]);

  useEffect(() => {
    load();
  }, [load]);

  const restoreListScroll = () => {
    const top = listScroll.current;
    requestAnimationFrame(() => scrollParent(rootRef.current)?.scrollTo?.({ top }));
  };

  // The URL's ?id= is what is open: a deep link opens it, and when the param
  // goes away (Back after a tap pushed it) the list comes back.
  const openedDeep = useRef<string | null>(null);
  const pushedOpen = useRef(false);
  useEffect(() => {
    if (deepId && openedDeep.current !== deepId) {
      openedDeep.current = deepId;
      setViewId(deepId);
    } else if (!deepId && openedDeep.current && params) {
      openedDeep.current = null;
      pushedOpen.current = false;
      setViewId(null);
      restoreListScroll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepId]);

  const open = (id: string) => {
    const scroller = scrollParent(rootRef.current);
    listScroll.current = scroller?.scrollTop ?? 0;
    openedDeep.current = id;
    setViewId(id);
    pushUrlId(id);
    pushedOpen.current = true;
    scroller?.scrollTo?.({ top: 0 });
  };

  const back = () => {
    // A tap pushed this entry: go back through history, so the browser's
    // Back and this button agree (the router clears ?id=, the effect above
    // shows the list). Outside the app router (tests) there is no router to
    // tell us, so close directly.
    if (pushedOpen.current && params && typeof window !== "undefined") {
      pushedOpen.current = false;
      window.history.back();
      return;
    }
    openedDeep.current = null;
    setViewId(null);
    clearUrlParams(["id"]);
    restoreListScroll();
  };

  const remove = (d: DocumentItem) => {
    back();
    scheduleUndoableDelete({
      key: deleteKey(d.id),
      commit: () => deleteDocument(d.id),
      onCommit: () => {
        setDocs((ds) => ds.filter((x) => x.id !== d.id));
        setTotal((t) => Math.max(0, t - 1));
      },
      onError: () => {
        toast({ title: "I couldn't delete that document. It's back.", tone: "bad" });
        void load();
      },
      toast,
      title: "Document deleted",
      body: docDisplay(d.title).title,
      tooLate: "Too late — that document is already deleted.",
    });
  };

  const visible = useMemo(() => docs.filter((d) => !hidden.has(deleteKey(d.id))), [docs, hidden]);
  const chips = tagOptions(stats?.by_tag);
  const searching = Boolean(debounced || tag);
  const { pinned, rest } = splitPinned(visible);

  if (viewId) {
    return (
      <div ref={rootRef}>
        <DocumentView
          id={viewId}
          fromList={docs.find((d) => d.id === viewId) ?? null}
          onDelete={remove}
          onTag={(t) => {
            setTag(t);
            back();
          }}
        />
      </div>
    );
  }

  return (
    <div ref={rootRef} className="space-y-4">
      <div className="flex items-center gap-2">
        <SearchField value={query} onChange={setQuery} label="Search documents" placeholder="Search documents" />
        <Button variant="secondary" size="icon" aria-label="New document" onClick={() => setNewOpen(true)} className="h-11 w-11 flex-none rounded-full md:h-11 md:w-11">
          <Plus aria-hidden />
        </Button>
      </div>

      {chips.length ? (
        <div
          role="radiogroup"
          aria-label="Tags"
          className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none] md:mx-0 md:flex-wrap md:overflow-visible md:px-0 [&::-webkit-scrollbar]:hidden"
        >
          <TagChip label="All" on={tag === null} onClick={() => setTag(null)} />
          {chips.map((c) => (
            <TagChip key={c.tag} label={c.tag} count={c.count} on={tag === c.tag} onClick={() => setTag(tag === c.tag ? null : c.tag)} />
          ))}
        </div>
      ) : null}

      {error ? (
        <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={load}>Try again</Button>}>
          I couldn&apos;t open my documents just now.
        </Notice>
      ) : null}

      {loading && docs.length === 0 ? (
        <ListSkeleton />
      ) : !error && visible.length === 0 ? (
        <EmptyState title={searching ? "No documents match." : "No documents yet."}>
          {searching
            ? "Try other words or another tag."
            : "When something deserves to be kept whole — a recipe, a plan, a protocol — I save it here."}
        </EmptyState>
      ) : (
        <div className={cn("space-y-5 transition-opacity duration-fast", loading && "opacity-60")}>
          {!searching && pinned.length ? (
            <section className="space-y-2" aria-label="Pinned">
              <SectionLabel>Pinned</SectionLabel>
              {pinned.map((d) => (
                <DocCard key={d.id} doc={d} onOpen={() => open(d.id)} />
              ))}
            </section>
          ) : null}
          <section className="space-y-2" aria-label="Documents">
            {!searching && pinned.length ? <SectionLabel action={rest.length ? String(rest.length) : undefined}>Everything else</SectionLabel> : null}
            {(searching ? [...pinned, ...rest] : rest).map((d) => (
              <DocCard key={d.id} doc={d} onOpen={() => open(d.id)} />
            ))}
          </section>
          {total > docs.length ? (
            <p className="text-center text-caption text-text-muted">
              Showing the newest {docs.length} of {total}. Search to find older ones.
            </p>
          ) : null}
        </div>
      )}

      <NewDocumentSheet open={newOpen} onClose={() => setNewOpen(false)} onSaved={load} />
    </div>
  );
}

function TagChip({ label, count, on, onClick }: { label: string; count?: number; on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      onClick={onClick}
      className={cn(
        "inline-flex min-h-[44px] md:min-h-[36px] flex-none items-center gap-1.5 rounded-full border px-3 text-footnote font-medium transition-[background-color,color,border-color,transform] duration-fast ease-out active:scale-[.97]",
        on ? "border-transparent bg-text-primary text-primary-bg" : "border-border bg-surface text-text-secondary hover:text-text-primary",
      )}
    >
      {label}
      {count != null ? <span className="tabular-nums opacity-60">{count}</span> : null}
    </button>
  );
}

const KIND_ICON = { state: Sparkles, goal: Target, doc: FileText } as const;

function DocCard({ doc, onOpen }: { doc: DocumentItem; onOpen: () => void }) {
  const kind = docKind(doc.title);
  const shown = docDisplay(doc.title);
  const Icon = KIND_ICON[kind];
  const tags = tagList(doc.tags).filter((t) => !["state", "self", "goal", "active"].includes(t));
  return (
    <Card onClick={onOpen} className="p-4">
      <span className="flex items-start gap-3">
        <span
          className={cn(
            "mt-0.5 flex h-8 w-8 flex-none items-center justify-center rounded-sm",
            kind === "state" ? "bg-terminal-soft text-terminal-strong" : "bg-surface-3 text-text-secondary",
          )}
        >
          <Icon className="h-4 w-4" aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span data-testid="doc-title" className="min-w-0 truncate text-callout text-text-primary">
              {shown.title}
            </span>
            {shown.badge ? <Pill tone={kind === "state" ? "edward" : "neutral"}>{shown.badge}</Pill> : null}
          </span>
          <span className="mt-0.5 line-clamp-2 text-footnote text-text-muted">{docPreview(doc.content, 180)}</span>
          <span className="mt-1.5 block text-caption text-text-muted">
            {doc.updated_at ? `Updated ${formatRelative(doc.updated_at)}` : null}
            {tags.length ? ` · ${tags.slice(0, 3).join(", ")}` : null}
          </span>
        </span>
      </span>
    </Card>
  );
}

// ------------------------------------------------------------ reading view
function DocumentView({
  id,
  fromList,
  onDelete,
  onTag,
}: {
  id: string;
  fromList: DocumentItem | null;
  onDelete: (d: DocumentItem) => void;
  onTag: (tag: string) => void;
}) {
  const [doc, setDoc] = useState<DocumentItem | null>(fromList);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setFailed(false);
    // Always read the full document: the list may be a search page.
    getDocument(id)
      .then((d) => alive && setDoc(d))
      .catch(() => alive && !fromList && setFailed(true));
    return () => {
      alive = false;
    };
    // fromList only seeds the first paint.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const shown = doc ? docDisplay(doc.title) : null;
  const tags = doc ? tagList(doc.tags) : [];

  return (
    <article className="max-w-3xl space-y-4">
      {/* The way back is the page's breadcrumb ("‹ Documents"): one back, not two. */}
      {doc ? (
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" className="text-bad hover:text-bad" onClick={() => onDelete(doc)}>
            Delete
          </Button>
        </div>
      ) : null}

      {failed ? (
        <Notice tone="bad">I couldn&apos;t find that document. It may have been deleted.</Notice>
      ) : !doc ? (
        <Skeleton lines={6} />
      ) : (
        <>
          <header className="space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-caption text-text-muted">
              {shown?.badge ? <Pill tone={docKind(doc.title) === "state" ? "edward" : "neutral"}>{shown.badge}</Pill> : null}
              {doc.updated_at ? <span>Updated {formatRelative(doc.updated_at)}</span> : null}
              {doc.access_count ? <span>· read {doc.access_count}×</span> : null}
            </div>
            {opensWithTitle(doc.content) ? null : <h1 className="text-title2 text-text-primary">{shown?.title}</h1>}
          </header>
          <div className="text-body text-text-primary">
            <MarkdownContent content={doc.content} />
          </div>
          {tags.length ? (
            <div className="flex flex-wrap gap-2 border-t border-border pt-4">
              {tags.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => onTag(t)}
                  className="inline-flex min-h-[44px] items-center rounded-full border border-border bg-surface px-3 text-footnote text-text-secondary transition-colors duration-fast hover:text-text-primary md:min-h-[36px]"
                >
                  #{t}
                </button>
              ))}
            </div>
          ) : null}
        </>
      )}
    </article>
  );
}

// ------------------------------------------------------------ new document
function NewDocumentSheet({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: () => void }) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await createDocument({ title: title.trim(), content: content.trim(), tags: tags.trim() || undefined });
      toast({ title: "Saved", body: title.trim(), tone: "ok" });
      setTitle("");
      setContent("");
      setTags("");
      onClose();
      onSaved();
    } catch {
      setError("I couldn't save it just now. Your text is still here.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="New document"
      description="Markdown works. I'll find it by its title and first lines."
      footer={
        <div className="flex gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button className="flex-1" loading={busy} disabled={!title.trim() || !content.trim()} onClick={save}>
            Save
          </Button>
        </div>
      }
    >
      <div className="space-y-4 pb-2">
        <Field label="Title">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Biscuit — med protocol" />
        </Field>
        <Field label="Text">
          <Textarea value={content} onChange={(e) => setContent(e.target.value)} rows={8} className="font-mono text-[15px]" />
        </Field>
        <Field label="Tags" hint="Comma-separated, e.g. pets, biscuit">
          <Input value={tags} onChange={(e) => setTags(e.target.value)} autoCapitalize="none" />
        </Field>
        {error ? <Notice tone="bad">{error}</Notice> : null}
      </div>
    </Sheet>
  );
}
