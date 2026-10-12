"use client";

// Settings → Memory → Dreaming (2026-09-26, Jarvis polish).
//
// Dreaming is how Edward tidies his memory: every so often he links what
// belongs together and flags what disagrees. Three views: Overview (on/off,
// when he last dreamed, "Dream now", limits folded away), To review (the
// flags) and Connections.
//
// Contradiction notes and pushes link to /settings/dreaming?flag=<id>: the
// panel opens that flag as "Which is right?" with both memories and Keep
// this / Keep that / Both are fine (POST …/resolve {keep} or PATCH
// {resolved: true}). `&ask=rewrite` (or a note that asked for one) is a
// proposed rewrite — never a keep-one choice; the owner tells Edward the right
// wording in chat or leaves it. ?tab=flags opens the review list.
// Words live in lib/memoryFlags.ts.

import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Link2, Moon, SlidersHorizontal } from "lucide-react";
import {
  getDreamingConnections,
  getDreamingFlag,
  getDreamingFlags,
  getDreamingStatus,
  keepDreamingFlagSide,
  resolveDreamingFlag,
  triggerDreamingCycle,
  updateDreamingConfig,
  type ConsolidationStatus,
  type MemoryConnection,
  type MemoryFlag,
} from "@/lib/api";
import {
  connectionPhrase,
  flagChoices,
  flagDeepLink,
  flagHeadline,
  flagKind,
  flagNote,
  flagsNeedingReview,
  nextDreamText,
  type DreamsTab,
} from "@/lib/memoryFlags";
import { getMemoryById, listRecentNotes, type NoteLike } from "@/lib/memoryHouseApi";
import { clearUrlParams } from "@/lib/deepLink";
import { formatRelative } from "@/lib/time";
import {
  Button,
  Card,
  EmptyState,
  Notice,
  Pill,
  Row,
  SectionLabel,
  Segment,
  Sheet,
  Skeleton,
  Switch,
  cn,
  useToast,
} from "@/components/ui";
import { NumberField } from "./NumberField";

export interface DreamsPanelProps {
  /** Tests; the page reads ?flag= / ?ask= / ?tab=. */
  initialFlag?: string | null;
  initialAsk?: "rewrite" | null;
  initialTab?: DreamsTab | null;
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function DreamsPanel(props: DreamsPanelProps) {
  return (
    <Suspense fallback={<Skeleton lines={4} />}>
      <DreamsBody {...props} />
    </Suspense>
  );
}

type Loaded<T> = T | null | "failed";

function DreamsBody({ initialFlag, initialAsk, initialTab }: DreamsPanelProps) {
  const params = useSearchParams();
  const deep = useMemo(() => {
    const fromUrl = flagDeepLink(params);
    return {
      flagId: initialFlag ?? fromUrl.flagId,
      rewrite: initialAsk === "rewrite" || fromUrl.rewrite,
      tab: initialTab ?? (initialFlag ? "review" : fromUrl.tab),
    };
  }, [params, initialFlag, initialAsk, initialTab]);

  const { toast } = useToast();
  const [tab, setTab] = useState<DreamsTab>(deep.tab ?? "overview");
  const [status, setStatus] = useState<Loaded<ConsolidationStatus>>(null);
  const [flags, setFlags] = useState<Loaded<MemoryFlag[]>>(null);
  const [connections, setConnections] = useState<Loaded<MemoryConnection[]>>(null);
  const [notes, setNotes] = useState<NoteLike[]>([]);
  const [openFlag, setOpenFlag] = useState<{ id: string; rewrite: boolean } | null>(null);
  // A deep-linked flag that is not on the first page of open flags is looked
  // up among every flag before the sheet says anything about it.
  const [lookup, setLookup] = useState<{ id: string; state: "looking" | "found" | "settled" | "missing" | "failed" } | null>(null);
  const [dreaming, setDreaming] = useState(false);

  const load = useCallback(async () => {
    const [s, f, c, n] = await Promise.allSettled([
      getDreamingStatus(),
      getDreamingFlags(false, 50, true),
      getDreamingConnections(50, true),
      listRecentNotes(),
    ]);
    setStatus(s.status === "fulfilled" ? s.value : "failed");
    setFlags(f.status === "fulfilled" ? f.value : "failed");
    setConnections(c.status === "fulfilled" ? c.value : "failed");
    if (n.status === "fulfilled") setNotes(n.value);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (deep.tab) setTab(deep.tab);
    if (deep.flagId) setOpenFlag({ id: deep.flagId, rewrite: deep.rewrite });
  }, [deep.flagId, deep.rewrite, deep.tab]);

  useEffect(() => {
    if (!openFlag || !Array.isArray(flags)) return;
    if (flags.some((f) => f.id === openFlag.id)) return;
    if (lookup?.id === openFlag.id) return;
    const id = openFlag.id;
    setLookup({ id, state: "looking" });
    // Read it directly; an older backend without GET /flags/{id} falls back
    // to searching every flag (settled ones included).
    const settle = (hit: MemoryFlag | null | undefined) => {
      if (hit && !hit.resolved) {
        setFlags((prev) => (Array.isArray(prev) && !prev.some((f) => f.id === id) ? [...prev, hit] : prev));
        setLookup({ id, state: "found" });
      } else {
        setLookup({ id, state: hit ? "settled" : "missing" });
      }
    };
    getDreamingFlag(id)
      .then(settle)
      .catch(() =>
        getDreamingFlags(undefined, 200, true)
          .then((all) => settle(all.find((f) => f.id === id)))
          .catch(() => setLookup({ id, state: "failed" })),
      );
  }, [openFlag, flags, lookup]);

  const retry = () => {
    setLookup(null);
    setFlags(null);
    void load();
  };

  const open = Array.isArray(flags) ? flagsNeedingReview(flags) : [];
  const links = Array.isArray(connections) ? connections : [];
  const st = status && status !== "failed" ? status : null;

  const patch = async (body: Record<string, number | boolean>) => {
    const before = st;
    if (before) setStatus({ ...before, ...body } as ConsolidationStatus);
    try {
      const updated = await updateDreamingConfig(body);
      setStatus((prev) => (prev && prev !== "failed" ? { ...prev, ...updated } : updated));
    } catch {
      if (before) setStatus(before);
      toast({ title: "I couldn't save that just now.", tone: "bad" });
    }
  };

  const dreamNow = async () => {
    setDreaming(true);
    try {
      await triggerDreamingCycle();
      toast({ title: "Dreaming now", body: "I'll link and flag what I find.", tone: "edward" });
      setTimeout(load, 1500);
    } catch {
      toast({ title: "I couldn't start dreaming just now.", tone: "bad" });
    } finally {
      setDreaming(false);
    }
  };

  const closeFlag = () => {
    setOpenFlag(null);
    clearUrlParams(["flag", "ask"]);
  };

  const settled = (id: string, title: string) => {
    setFlags((prev) => (Array.isArray(prev) ? prev.filter((f) => f.id !== id) : prev));
    closeFlag();
    toast({ title, tone: "ok" });
  };

  const current = openFlag && Array.isArray(flags) ? flags.find((f) => f.id === openFlag.id && !f.resolved) ?? null : null;
  const note = openFlag ? flagNote(openFlag.id, notes) : null;
  const look = openFlag && lookup?.id === openFlag.id ? lookup.state : null;
  const sheetState: FlagSheetState =
    flags === "failed" || look === "failed"
      ? "failed"
      : flags === null || look === "looking" || (openFlag && Array.isArray(flags) && !current && look === null)
        ? "loading"
        : look === "missing"
          ? "missing"
          : "settled";

  return (
    <div className="space-y-5">
      <Segment<DreamsTab>
        label="Dreaming views"
        value={tab}
        onChange={setTab}
        options={[
          { value: "overview", label: "Overview" },
          { value: "review", label: "To review", count: open.length },
          { value: "links", label: "Connections", count: links.length },
        ]}
      />

      {tab === "overview" ? (
        <Overview status={status} onToggle={(on) => patch({ enabled: on })} onPatch={patch} onDream={dreamNow} dreaming={dreaming} onRetry={load} />
      ) : tab === "review" ? (
        <ReviewList
          flags={flags}
          onOpen={(f) => setOpenFlag({ id: f.id, rewrite: flagNote(f.id, notes)?.rewrite ?? false })}
          onRetry={load}
        />
      ) : (
        <ConnectionList connections={connections} onRetry={load} />
      )}

      <FlagSheet
        open={Boolean(openFlag)}
        state={sheetState}
        onRetry={retry}
        flag={current}
        rewrite={Boolean(openFlag?.rewrite || note?.rewrite)}
        question={note?.question ?? null}
        onClose={closeFlag}
        onSettled={settled}
      />
    </div>
  );
}

// ------------------------------------------------------------ overview
function Overview({
  status,
  onToggle,
  onPatch,
  onDream,
  dreaming,
  onRetry,
}: {
  status: Loaded<ConsolidationStatus>;
  onToggle: (on: boolean) => void;
  onPatch: (body: Record<string, number>) => void;
  onDream: () => void;
  dreaming: boolean;
  onRetry: () => void;
}) {
  const [limitsOpen, setLimitsOpen] = useState(false);
  if (status === "failed") {
    return (
      <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={onRetry}>Try again</Button>}>
        I can&apos;t reach my dreaming settings right now.
      </Notice>
    );
  }
  if (!status) {
    return (
      <Card className="p-4">
        <Skeleton lines={3} />
      </Card>
    );
  }
  const minutes = Math.max(1, Math.round(status.interval_seconds / 60));
  const every = minutes % 60 === 0 ? (minutes === 60 ? "every hour" : `every ${minutes / 60} hours`) : `every ${minutes} minutes`;
  return (
    <div className="space-y-5">
      <Card className="p-4">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-8 w-8 flex-none items-center justify-center rounded-sm bg-terminal-soft text-terminal-strong">
            <Moon className="h-4 w-4" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-callout text-text-primary">Dreaming</p>
            <p className="mt-0.5 text-footnote text-text-muted">
              {status.enabled
                ? `I tidy my memory ${every}: I link what belongs together and flag what disagrees.`
                : "Off. I won't link or flag anything until you turn it back on."}
            </p>
          </div>
          <Switch checked={status.enabled} onChange={onToggle} label="Dreaming" />
        </div>
        <div className="mt-4 flex items-center gap-3 border-t border-border pt-3">
          <p className="min-w-0 flex-1 text-footnote text-text-muted">
            {status.last_run ? `Dreamed ${formatRelative(status.last_run)}` : "I haven't dreamed yet"}
            {status.enabled && nextDreamText(status.next_run) ? ` · ${nextDreamText(status.next_run)}` : ""}
          </p>
          <Button variant="edward" size="sm" onClick={onDream} loading={dreaming} disabled={!status.enabled}>
            Dream now
          </Button>
        </div>
      </Card>

      <div className="grid grid-cols-3 gap-2">
        <Stat label="Dreams" value={status.cycle_count} />
        <Stat label="Connections" value={status.total_connections} />
        <Stat label="Flags" value={status.total_flags} />
      </div>

      <Card className="px-3 py-0">
        <Row
          icon={<SlidersHorizontal />}
          title="Limits"
          detail="How often, how far back, and how much work each dream may do"
          expanded={limitsOpen}
          onClick={() => setLimitsOpen((v) => !v)}
        />
        {limitsOpen ? (
          <div className="grid gap-4 pb-4 pt-1 sm:grid-cols-2">
            <NumberField label="Dream every (minutes)" value={minutes} min={1} max={1440} onSave={(v) => onPatch({ interval_seconds: v * 60 })} />
            <NumberField label="Look back (hours)" value={status.lookback_hours} min={1} max={48} onSave={(v) => onPatch({ lookback_hours: v })} />
            <NumberField label="Largest group" value={status.max_cluster_size} min={2} max={100} onSave={(v) => onPatch({ max_cluster_size: v })} />
            <NumberField label="Pairs per group" value={status.max_pairs_per_cluster} min={1} max={200} onSave={(v) => onPatch({ max_pairs_per_cluster: v })} />
            <NumberField
              label="Model calls per dream"
              hint="Each one spends a little of the week's quota."
              value={status.max_llm_calls_per_cycle}
              min={1}
              max={100}
              onSave={(v) => onPatch({ max_llm_calls_per_cycle: v })}
            />
          </div>
        ) : null}
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card className="px-3 py-2.5">
      <p className="text-caption text-text-muted">{label}</p>
      <p className="mt-0.5 text-title3 tabular-nums text-text-primary">{value.toLocaleString("en-US")}</p>
    </Card>
  );
}

// ------------------------------------------------------------ review
function ReviewList({
  flags,
  onOpen,
  onRetry,
}: {
  flags: Loaded<MemoryFlag[]>;
  onOpen: (f: MemoryFlag) => void;
  onRetry: () => void;
}) {
  if (flags === "failed") {
    return (
      <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={onRetry}>Try again</Button>}>
        I can&apos;t read my flags right now.
      </Notice>
    );
  }
  if (!flags) {
    return (
      <div className="space-y-2">
        {[0, 1].map((i) => (
          <Card key={i} className="p-4">
            <Skeleton lines={2} />
          </Card>
        ))}
      </div>
    );
  }
  const open = flagsNeedingReview(flags);
  if (open.length === 0) {
    return <EmptyState title="Nothing to check.">My memories agree with each other right now.</EmptyState>;
  }
  return (
    <div className="space-y-2">
      {open.map((f) => (
        <Card key={f.id} onClick={() => onOpen(f)} className="p-4">
          <span className="flex items-center gap-2 text-caption text-text-muted">
            <Pill tone={f.flag_type === "contradiction" ? "warn" : "neutral"}>{flagKind(f.flag_type)}</Pill>
            <span>{formatRelative(f.created_at)}</span>
          </span>
          <span className="mt-2 block text-callout text-text-primary">{flagHeadline(f)}</span>
          <span className="mt-0.5 block text-footnote text-text-muted [overflow-wrap:anywhere]">{f.description}</span>
        </Card>
      ))}
    </div>
  );
}

// ------------------------------------------------------------ connections
// Each link reads as a sentence: "<first memory> adds detail to <second>",
// each side opening its memory. The texts come with the list when the
// server attaches them (`with_content`); otherwise the shown rows' memories
// are read by id, a few at a time. Strength is left out: it is fixed per
// kind of link, so it says nothing the words don't.
const CONNECTIONS_PAGE = 15;
const TEXT_FETCHES_AT_ONCE = 4;

type MemoryText = string | "failed";

function useMemoryTexts(ids: string[]): Record<string, MemoryText> {
  const [texts, setTexts] = useState<Record<string, MemoryText>>({});
  const asked = useRef(new Set<string>());
  const key = ids.join(",");
  useEffect(() => {
    const seen = asked.current;
    const todo = ids.filter((id) => !seen.has(id));
    if (todo.length === 0) return;
    todo.forEach((id) => seen.add(id));
    let alive = true;
    let next = 0;
    const worker = async () => {
      while (alive && next < todo.length) {
        const id = todo[next++];
        let text: MemoryText;
        try {
          text = (await getMemoryById(id)).content || "failed";
        } catch {
          text = "failed";
        }
        if (alive) {
          finished.add(id);
          setTexts((t) => ({ ...t, [id]: text }));
        }
      }
    };
    const finished = new Set<string>();
    void Promise.all(Array.from({ length: Math.min(TEXT_FETCHES_AT_ONCE, todo.length) }, worker));
    return () => {
      alive = false;
      // Ids still in flight are asked again by the next run.
      todo.filter((id) => !finished.has(id)).forEach((id) => seen.delete(id));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return texts;
}

function ConnectionList({ connections, onRetry }: { connections: Loaded<MemoryConnection[]>; onRetry: () => void }) {
  const [shown, setShown] = useState(CONNECTIONS_PAGE);
  const rows = Array.isArray(connections) ? connections.slice(0, shown) : [];
  const missing = Array.from(
    new Set(
      rows.flatMap((c) => [
        ...(c.memory_a_content ? [] : [c.memory_id_a]),
        ...(c.memory_b_content ? [] : [c.memory_id_b]),
      ]),
    ),
  );
  const texts = useMemoryTexts(missing);

  if (connections === "failed") {
    return (
      <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={onRetry}>Try again</Button>}>
        I can&apos;t read my connections right now.
      </Notice>
    );
  }
  if (!connections) return <Skeleton lines={4} />;
  if (connections.length === 0) {
    return <EmptyState title="No connections yet.">When I dream, I link memories that belong together. They&apos;ll show up here.</EmptyState>;
  }
  const textOf = (id: string, attached: string | null | undefined): MemoryText | undefined => attached || texts[id];
  return (
    <div className="space-y-2">
      <SectionLabel>Newest links</SectionLabel>
      <Card className="px-4 py-0">
        {rows.map((c) => (
          <div key={c.id} className="flex gap-3 border-b border-border py-3 last:border-b-0">
            <Link2 className="mt-3 h-4 w-4 flex-none text-text-muted" aria-hidden />
            <div className="min-w-0 flex-1">
              <MemorySide id={c.memory_id_a} text={textOf(c.memory_id_a, c.memory_a_content)} />
              <p className="px-2 text-caption text-text-muted">{connectionPhrase(c.connection_type)}</p>
              <MemorySide id={c.memory_id_b} text={textOf(c.memory_id_b, c.memory_b_content)} />
              <p className="mt-1 px-2 text-caption text-text-muted">Linked {formatRelative(c.created_at)}</p>
            </div>
          </div>
        ))}
      </Card>
      {connections.length > shown ? (
        <Button variant="ghost" size="sm" onClick={() => setShown((n) => n + CONNECTIONS_PAGE)}>
          Show more
        </Button>
      ) : null}
    </div>
  );
}

function MemorySide({ id, text }: { id: string; text: MemoryText | undefined }) {
  return (
    <Link
      href={`/settings/memories?id=${encodeURIComponent(id)}`}
      className="-mx-0 flex min-h-[44px] items-center rounded-md px-2 py-1.5 transition-colors duration-fast hover:bg-surface-3 active:bg-surface-3/80"
    >
      {text === undefined ? (
        <span className="w-full" aria-label="Reading this memory">
          <Skeleton lines={1} />
        </span>
      ) : text === "failed" ? (
        <span className="text-footnote text-text-muted">A memory I can&apos;t read right now</span>
      ) : (
        <span className="line-clamp-2 text-footnote text-text-primary [overflow-wrap:anywhere]">
          {/^["“'‘]/.test(text) ? text : `“${text}”`}
        </span>
      )}
    </Link>
  );
}

// ------------------------------------------------------------ the flag sheet
type FlagSheetState = "loading" | "failed" | "settled" | "missing";

const FLAG_SHEET_EMPTY: Record<FlagSheetState, { title: string; body: string | null }> = {
  loading: { title: "One moment", body: null },
  failed: { title: "Can't load it", body: "I can't load that question right now. Nothing has changed — try again in a moment." },
  settled: { title: "Already settled", body: "That one's already settled — nothing left to choose." },
  missing: { title: "Can't find it", body: "I can't find that question any more — it may have been cleared when I tidied my memory." },
};

function FlagSheet({
  open,
  state,
  onRetry,
  flag,
  rewrite,
  question,
  onClose,
  onSettled,
}: {
  open: boolean;
  /** What to say while there is no flag to show. */
  state: FlagSheetState;
  onRetry: () => void;
  flag: MemoryFlag | null;
  rewrite: boolean;
  question: string | null;
  onClose: () => void;
  onSettled: (id: string, toast: string) => void;
}) {
  const { toast } = useToast();
  const [busy, setBusy] = useState<string | null>(null);

  const act = async (label: string, run: () => Promise<unknown>, done: string) => {
    if (!flag) return;
    setBusy(label);
    try {
      await run();
      onSettled(flag.id, done);
    } catch {
      toast({ title: "I couldn't save that just now. Try again?", tone: "bad" });
    } finally {
      setBusy(null);
    }
  };

  if (!flag) {
    const words = FLAG_SHEET_EMPTY[state];
    return (
      <Sheet
        open={open}
        onClose={onClose}
        title={words.title}
        footer={
          state === "failed" ? (
            <div className="flex gap-2">
              <Button variant="ghost" onClick={onClose}>
                Not now
              </Button>
              <Button variant="primary" className="flex-1" onClick={onRetry}>
                Try again
              </Button>
            </div>
          ) : (
            <Button variant="secondary" className="w-full" onClick={onClose}>
              Done
            </Button>
          )
        }
      >
        {state === "loading" ? (
          <Skeleton lines={3} className="pb-4" />
        ) : (
          <p className="pb-4 text-body text-text-secondary">{words.body}</p>
        )}
      </Sheet>
    );
  }

  const choices = flagChoices(flag, { rewrite });
  const texts = [flag.memory_content, flag.related_memory_content].filter((t): t is string => typeof t === "string" && t.length > 0);
  const resolveBoth = () => resolveDreamingFlag(flag.id, true);

  let footer: React.ReactNode;
  if (choices.length) {
    footer = (
      <div className="flex gap-2">
        <Button variant="ghost" onClick={onClose} disabled={Boolean(busy)}>
          Not now
        </Button>
        <Button
          variant="secondary"
          className="flex-1"
          loading={busy === "both"}
          disabled={Boolean(busy)}
          onClick={() => act("both", resolveBoth, "Both kept")}
        >
          Both are fine
        </Button>
      </div>
    );
  } else if (rewrite) {
    const prompt = `One of my memories needs rewording${texts[0] ? `: “${texts[0]}”` : ""}. The right version is: `;
    footer = (
      <div className="flex gap-2">
        <Button
          variant="secondary"
          loading={busy === "leave"}
          disabled={Boolean(busy)}
          onClick={() => act("leave", resolveBoth, "Left as it is")}
        >
          Leave it as is
        </Button>
        <Button className="flex-1" href={`/chat?prompt=${encodeURIComponent(prompt)}`}>
          Tell Edward
        </Button>
      </div>
    );
  } else {
    footer = (
      <div className="flex gap-2">
        <Button variant="ghost" onClick={onClose} disabled={Boolean(busy)}>
          Not now
        </Button>
        <Button
          className="flex-1"
          loading={busy === "fine"}
          disabled={Boolean(busy)}
          onClick={() => act("fine", resolveBoth, "Marked as fine")}
        >
          Looks fine
        </Button>
      </div>
    );
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={flagHeadline(flag, { rewrite })}
      description={question ?? flag.description}
      footer={footer}
    >
      <div className="space-y-3 pb-3">
        {rewrite ? (
          <Notice tone="info">I think one of these is worded wrong. Tell me the right wording and I&apos;ll fix it.</Notice>
        ) : null}
        {choices.length ? (
          choices.map((c, i) => (
            <Card key={c.keep} className="space-y-3 p-4">
              <p className="text-caption font-medium text-text-muted">{i === 0 ? "One memory says" : "Another says"}</p>
              <p className="text-body text-text-primary [overflow-wrap:anywhere]">{c.text}</p>
              <Button
                variant="primary"
                className="w-full"
                loading={busy === c.keep}
                disabled={Boolean(busy)}
                onClick={() => act(c.keep, () => keepDreamingFlagSide(flag.id, c.keep), "Kept it — I've retired the other one")}
              >
                {c.label}
              </Button>
            </Card>
          ))
        ) : texts.length ? (
          texts.map((t, i) => (
            <Card key={i} className="p-4">
              <p className="text-body text-text-primary [overflow-wrap:anywhere]">{t}</p>
            </Card>
          ))
        ) : (
          <p className={cn("text-footnote text-text-muted")}>
            I don&apos;t have the text of this memory to hand. You can find it in Memories.
          </p>
        )}
      </div>
    </Sheet>
  );
}
