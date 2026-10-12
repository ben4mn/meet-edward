"use client";

// Settings → Work → Wishlist. Edward logs features he wants the owner to
// build; the owner reads, reprioritises, moves them along and adds their own. Grouping,
// order and wording live in lib/wishlist.ts. A delete is undoable: the wish
// leaves at once and the DELETE waits out the Undo window. ?id= (the Work
// room's timeline) unfolds that wish's section, scrolls to it and marks it.

import { setVisibleInterval } from "@/lib/visibleInterval";
import React, { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Lightbulb, MessageSquare, Plus, Trash2 } from "lucide-react";
import { createWish, deleteWish, getWishlist, updateWish } from "@/lib/api";
import { useBuildReloadHold } from "@/lib/buildReload";
import { clearUrlParams, readParam } from "@/lib/deepLink";
import {
  cyclePriority,
  describeWish,
  foldsDetail,
  groupWishes,
  PRIORITY_LABEL,
  STATUS_OPTIONS,
  type WishItem,
  type WishPriority,
  type WishStatus,
} from "@/lib/wishlist";
import {
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Notice,
  Pill,
  Row,
  SectionLabel,
  Segment,
  Sheet,
  Skeleton,
  TOAST_UNDO_DURATION_MS,
  cn,
  useToast,
  type Tone,
} from "@/components/ui";

const POLL_MS = 30_000;

const STATUS_TONE: Record<WishStatus, Tone> = {
  open: "edward",
  planned: "info",
  building: "warn",
  done: "ok",
  declined: "neutral",
};

const PRIORITY_OPTIONS: { value: "1" | "2" | "3"; label: string }[] = [
  { value: "1", label: "High" },
  { value: "2", label: "Normal" },
  { value: "3", label: "Low" },
];

export interface WishlistPanelProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
  undoMs?: number;
  /** Tests: the ?id= deep link without a router. */
  initialId?: string | null;
}

export function WishlistPanel(props: WishlistPanelProps) {
  return (
    <Suspense
      fallback={
        <Card>
          <Skeleton lines={3} />
        </Card>
      }
    >
      <WishlistBody {...props} />
    </Suspense>
  );
}

const wishDomId = (id: string) => `wish-${id}`;
/** How long a deep-linked wish stays marked. */
const HIGHLIGHT_MS = 4000;

function WishlistBody({ undoMs = TOAST_UNDO_DURATION_MS, initialId }: WishlistPanelProps) {
  const params = useSearchParams();
  const deepId = initialId ?? readParam(params, "id");
  const [highlight, setHighlight] = useState<string | null>(null);
  const { toast } = useToast();
  const [items, setItems] = useState<WishItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [openSections, setOpenSections] = useState<Set<string>>(() => new Set());
  const pending = useRef(new Map<string, { timer: ReturnType<typeof setTimeout>; commit: () => void }>());

  const load = useCallback(async () => {
    try {
      setItems(await getWishlist());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
    return setVisibleInterval(load, POLL_MS);
  }, [load]);

  // Leaving the page commits what is pending rather than losing it.
  useEffect(() => {
    const map = pending.current;
    return () => {
      map.forEach((p) => {
        clearTimeout(p.timer);
        p.commit();
      });
      map.clear();
    };
  }, []);

  const patch = async (id: string, change: Partial<Pick<WishItem, "status" | "priority">>) => {
    setItems((prev) => prev?.map((i) => (i.id === id ? { ...i, ...change } : i)) ?? prev);
    try {
      await updateWish(id, change);
    } catch (e) {
      toast({ title: "I couldn't update that wish.", body: e instanceof Error ? e.message : undefined, tone: "bad" });
    } finally {
      load();
    }
  };

  const unhide = (id: string) =>
    setHidden((h) => {
      const next = new Set(h);
      next.delete(id);
      return next;
    });

  const remove = (item: WishItem) => {
    setHidden((h) => new Set(h).add(item.id));
    const commit = () => {
      pending.current.delete(item.id);
      deleteWish(item.id)
        .then(() => {
          setItems((prev) => prev?.filter((i) => i.id !== item.id) ?? prev);
          unhide(item.id);
        })
        .catch(() => {
          unhide(item.id);
          toast({ title: "I couldn't delete that wish.", tone: "bad" });
        });
    };
    const timer = setTimeout(commit, undoMs);
    pending.current.set(item.id, { timer, commit });
    toast({
      title: "Wish deleted",
      body: item.title,
      duration: undoMs,
      undo: () => {
        const p = pending.current.get(item.id);
        if (p) clearTimeout(p.timer);
        pending.current.delete(item.id);
        unhide(item.id);
      },
    });
  };

  // ?id= once: unfold its section, mark it, scroll to it.
  const openedDeep = useRef<string | null>(null);
  useEffect(() => {
    if (!deepId || !items || openedDeep.current === deepId) return;
    openedDeep.current = deepId;
    clearUrlParams(["id"]);
    const section = groupWishes(items).find((sec) => sec.items.some((i) => i.id === deepId));
    if (!section) {
      toast({ title: "That wish isn't on my list any more.", tone: "neutral" });
      return;
    }
    if (section.folded) setOpenSections((o) => new Set(o).add(section.id));
    setHighlight(deepId);
  }, [deepId, items, toast]);

  // Scroll once the marked wish is on the page (its section may have just
  // unfolded), then let the mark fade.
  const scrolledTo = useRef<string | null>(null);
  useEffect(() => {
    if (!highlight || scrolledTo.current === highlight) return;
    const el = document.getElementById(wishDomId(highlight));
    if (el) {
      scrolledTo.current = highlight;
      el.scrollIntoView?.({ block: "center", behavior: "smooth" });
    }
  });

  useEffect(() => {
    if (!highlight) return;
    const t = setTimeout(() => setHighlight(null), HIGHLIGHT_MS);
    return () => clearTimeout(t);
  }, [highlight]);

  const visible = (items ?? []).filter((i) => !hidden.has(i.id));
  const sections = groupWishes(visible);
  const openCount = visible.filter((i) => i.status === "open").length;

  return (
    <div className="space-y-6">
      <AddWish onAdded={load} />

      {failed && !items ? (
        <Notice
          tone="bad"
          title="I can't read my wishlist right now."
          action={
            <Button variant="ghost" size="sm" onClick={load}>
              Try again
            </Button>
          }
        />
      ) : items === null ? (
        <div className="space-y-2">
          <Card>
            <Skeleton lines={3} />
          </Card>
          <Card>
            <Skeleton lines={2} />
          </Card>
        </div>
      ) : sections.length === 0 ? (
        <Card>
          <EmptyState icon={<Lightbulb />}>Nothing yet. When I wish I could do something, it lands here.</EmptyState>
        </Card>
      ) : (
        sections.map((s) => {
          const folded = s.folded && !openSections.has(s.id);
          return (
            <section key={s.id} className="space-y-2">
              {s.folded ? (
                <Card className="px-3 py-0">
                  <Row
                    title={s.label}
                    trailing={s.items.length}
                    expanded={!folded}
                    onClick={() =>
                      setOpenSections((o) => {
                        const next = new Set(o);
                        if (next.has(s.id)) next.delete(s.id);
                        else next.add(s.id);
                        return next;
                      })
                    }
                  />
                </Card>
              ) : (
                <SectionLabel action={s.id === "open" ? openCount : s.items.length}>{s.label}</SectionLabel>
              )}
              {folded
                ? null
                : s.items.map((i) => (
                    <div
                      key={i.id}
                      id={wishDomId(i.id)}
                      data-wish-id={i.id}
                      data-highlight={highlight === i.id ? "true" : undefined}
                      className={cn(
                        "scroll-mt-4 rounded-lg transition-shadow duration-slow",
                        highlight === i.id && "ring-2 ring-terminal/50 ring-offset-2 ring-offset-primary-bg",
                      )}
                    >
                      <WishCard item={i} onPatch={patch} onDelete={() => remove(i)} />
                    </div>
                  ))}
            </section>
          );
        })
      )}
    </div>
  );
}

function WishCard({
  item,
  onPatch,
  onDelete,
}: {
  item: WishItem;
  onPatch: (id: string, change: Partial<Pick<WishItem, "status" | "priority">>) => void;
  onDelete: () => void;
}) {
  const [showDetail, setShowDetail] = useState(false);
  const closed = item.status === "done" || item.status === "declined";
  const folds = foldsDetail(item.detail);
  return (
    <Card as="article" className={cn("space-y-3 p-4", closed && "opacity-80")}>
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-callout text-text-primary [overflow-wrap:anywhere]">{item.title}</span>
          {item.priority === 1 ? <Pill tone="warn">high</Pill> : null}
        </div>
        <p className="text-footnote text-text-secondary [overflow-wrap:anywhere]">{item.why}</p>
        {item.detail ? (
          folds && !showDetail ? (
            <button
              type="button"
              onClick={() => setShowDetail(true)}
              className="-mx-1 inline-flex min-h-[36px] items-center rounded-sm px-1 text-footnote font-medium text-text-secondary hover:text-text-primary"
            >
              Show the detail
            </button>
          ) : (
            <p className="whitespace-pre-wrap text-footnote text-text-secondary [overflow-wrap:anywhere]">{item.detail}</p>
          )
        ) : null}
        <div className="flex flex-wrap items-center gap-x-2 text-caption text-text-muted">
          {closed ? <Pill tone={STATUS_TONE[item.status]}>{item.status}</Pill> : null}
          <span>{describeWish(item)}</span>
          {item.conversation_id ? (
            <Link
              href={`/chat?c=${encodeURIComponent(item.conversation_id)}`}
              className="inline-flex min-h-[32px] items-center gap-1 font-medium text-text-secondary hover:text-text-primary"
            >
              <MessageSquare className="h-3 w-3" /> open the chat
            </Link>
          ) : null}
        </div>
      </div>

      <Segment<WishStatus>
        label={`Status of ${item.title}`}
        value={item.status}
        onChange={(status) => onPatch(item.id, { status })}
        options={STATUS_OPTIONS}
        className="text-caption [&>button]:px-1"
      />

      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" className="-ml-3" onClick={() => onPatch(item.id, { priority: cyclePriority(item.priority) })}>
          Priority: {PRIORITY_LABEL[item.priority]}
        </Button>
        <Button variant="ghost" size="sm" icon={<Trash2 />} className="-mr-3 ml-auto hover:text-bad" onClick={onDelete} aria-label={`Delete ${item.title}`}>
          Delete
        </Button>
      </div>
    </Card>
  );
}

function AddWish({ onAdded }: { onAdded: () => void }) {
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [why, setWhy] = useState("");
  const [priority, setPriority] = useState<WishPriority>(2);
  const [busy, setBusy] = useState(false);
  const ready = title.trim().length > 0 && why.trim().length > 0;
  // A deploy's reload waits while a wish is half written in the open sheet
  // (lib/buildReload.ts): its fields are one-line inputs, which count only
  // while focused, and the phone drops focus in the background (2026-09-27).
  useBuildReloadHold(open && (title.trim() !== "" || why.trim() !== ""));

  const submit = async () => {
    if (!ready || busy) return;
    setBusy(true);
    try {
      await createWish({ title: title.trim(), why: why.trim(), priority });
      setTitle("");
      setWhy("");
      setPriority(2);
      setOpen(false);
      toast({ title: "Added to my wishlist", tone: "ok" });
      onAdded();
    } catch (e) {
      toast({ title: "I couldn't add that wish.", body: e instanceof Error ? e.message : undefined, tone: "bad" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button variant="secondary" size="sm" icon={<Plus />} onClick={() => setOpen(true)}>
        Add a wish
      </Button>
      <Sheet
        open={open}
        onClose={() => setOpen(false)}
        title="Add a wish"
        description="Something you'd like me to be able to do."
        footer={
          <Button variant="primary" className="w-full" onClick={submit} disabled={!ready} loading={busy} aria-label="Add wish">
            Add
          </Button>
        }
      >
        <div className="space-y-4">
          <Field label="What should I be able to do?">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Watch the garage camera" />
          </Field>
          <Field label="Why it matters">
            <Input
              value={why}
              onChange={(e) => setWhy(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
              placeholder="So I can tell you when a package lands"
            />
          </Field>
          <Field label="Priority">
            <Segment<"1" | "2" | "3">
              label="Priority"
              value={String(priority) as "1" | "2" | "3"}
              onChange={(v) => setPriority(Number(v) as WishPriority)}
              options={PRIORITY_OPTIONS}
            />
          </Field>
        </div>
      </Sheet>
    </>
  );
}
