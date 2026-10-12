"use client";

// The conversation list (2026-09-26 chat chrome): the desktop sidebar and
// the phone's history drawer share one list.
//
// Rows are links (/chat?c=…) so they are keyboard-reachable and cmd-click
// opens a tab. Yours groups by date (Today · Yesterday · This week open,
// Older folded); Edward's keeps his sections (Actions first). Deleting is
// never one always-visible trash can: a "⋯" menu on hover (desktop) and a
// swipe left (touch), both optimistic with a six-second Undo
// (lib/pendingDeletes.ts). Both offer Rename too — the swipe shows it
// beside Delete (2026-09-27: rename used to be desktop-only). Edward's long-lived threads and his journal
// entries are never offered (sidebarGroups.canDelete).
//
// The drawer opens from the shell (its isOpen prop) or from anywhere via
// openChatHistory() — the window event "edward:open-history".

import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AnimatePresence, LazyMotion, animate, m, useMotionValue, useTransform } from "framer-motion";
import { MOTION_FEATURES } from "@/components/ui/motionFeatures";
import type { PanInfo } from "framer-motion";
import { ArrowUpRight, ChevronRight, Mic, MoreHorizontal, PanelLeftClose, Pencil, Search, SquarePen, Trash2, X, Zap } from "lucide-react";

import { cn } from "@/lib/utils";
import { useChatList } from "@/lib/ChatContext";
import type { Conversation } from "@/lib/api";
import { shouldShowUnreadDot } from "@/lib/unreadDot";
import { pendingDeletes, UNDO_WINDOW_MS } from "@/lib/pendingDeletes";
import { forgetDraftFiles } from "@/lib/draftFiles";
import { renameConversation } from "@/lib/conversationActions";
import { useUserTimezone } from "@/lib/useTimezone";
import { Button, Segment, Skeleton, trapTab, useToast } from "@/components/ui";
import {
  DURATION,
  EASE_IN,
  EXIT_FACTOR,
  REDUCED_FADE,
  SPRING_SHEET,
  useIsClient,
  useMediaQuery,
  usePrefersReducedMotion,
} from "@/components/ui/motion";
import {
  SIDEBAR_FILTERS,
  actionMarker,
  canDelete,
  conversationTime,
  displayTitle,
  drawerDragCloses,
  groupByDate,
  groupEdwards,
  listPhase,
  matchesSidebarFilter,
  normalizeSidebarFilter,
  sourceMark,
  swipeSettle,
} from "./sidebarGroups";

export const OPEN_HISTORY_EVENT = "edward:open-history";

/** Open the phone's conversation drawer from anywhere (the shell's presence bar). */
export function openChatHistory(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new window.Event(OPEN_HISTORY_EVENT));
}

// ---- deletes and renames --------------------------------------------------

function useHiddenIds(): ReadonlySet<string> {
  return useSyncExternalStore(pendingDeletes.subscribe, pendingDeletes.pending, pendingDeletes.pending);
}

/** Delete with a six-second Undo. Leaving the conversation on screen goes to a new chat. */
function useDeleteWithUndo() {
  const { conversationId, deleteConversation } = useChatList();
  const router = useRouter();
  const { toast } = useToast();
  return useCallback(
    (c: Conversation) => {
      if (!canDelete(c)) return;
      const wasOpen = c.id === conversationId;
      // A refused DELETE rejects (ChatContext puts the row back): say so.
      // A committed one takes the thread's kept attachments with it; a
      // refused one leaves them, since the thread is back (lib/draftFiles).
      pendingDeletes.schedule(c.id, () => deleteConversation(c.id).then(() => forgetDraftFiles(c.id)), {
        onError: () => toast({ title: "I couldn't delete that conversation. It's back.", tone: "bad" }),
      });
      if (wasOpen) router.replace("/chat");
      toast({
        title: "Conversation deleted",
        body: displayTitle(c),
        duration: UNDO_WINDOW_MS,
        undo: () => {
          if (pendingDeletes.undo(c.id) && wasOpen) router.push(`/chat?c=${encodeURIComponent(c.id)}`);
        },
      });
    },
    [conversationId, deleteConversation, router, toast],
  );
}

// ---- one row --------------------------------------------------------------

const ACTION_W = 88;
/** The swipe's buttons on touch: Rename, then Delete at the edge. */
const SWIPE_ACTIONS = 2;
const SWIPE_OPEN_W = ACTION_W * SWIPE_ACTIONS;

interface RowProps {
  conversation: Conversation;
  active: boolean;
  /** Show Edward's source mark (mixed lists: search). */
  edwardMark?: boolean;
  touch: boolean;
  tz: string;
  onNavigate?: () => void;
  onDelete: (c: Conversation) => void;
  /** The row whose Delete is showing (one at a time). */
  swipedId: string | null;
  setSwipedId: (id: string | null) => void;
}

/** Unread first; then where the thread came from. Edward's own mark is
 *  shown only where lists mix (search) — in his group every row is his. */
function LeadMark({ c, edwardMark }: { c: Conversation; edwardMark: boolean }) {
  if (shouldShowUnreadDot(c)) {
    return <span className="h-2 w-2 rounded-full bg-terminal shadow-[0_0_8px_rgb(var(--terminal-rgb)/0.6)]" aria-label="New" />;
  }
  const mark = sourceMark(c);
  if (!mark || (mark.kind === "edward" && !edwardMark)) return null;
  if (mark.kind === "voice") return <Mic className="h-3 w-3 text-text-muted" aria-label={mark.label} />;
  if (mark.kind === "edward") return <span className="h-1.5 w-1.5 rounded-full bg-terminal/70" aria-label={mark.label} title={mark.label} />;
  return <span className="h-1.5 w-1.5 rounded-full border border-text-muted" aria-label={mark.label} title={mark.label} />;
}

// Memoised: a row re-renders when its own props change — not on every
// list render, and never on a streamed token (it reads useChatList).
const ConversationRow = React.memo(function ConversationRow({ conversation: c, active, edwardMark = false, touch, tz, onNavigate, onDelete, swipedId, setSwipedId }: RowProps) {
  const reduced = usePrefersReducedMotion();
  const deletable = canDelete(c);
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState(() => displayTitle(c));
  const { toast } = useToast();
  const { refreshConversations } = useChatList();
  useEffect(() => setTitle(displayTitle(c)), [c]);

  const rowRef = useRef<HTMLDivElement>(null);
  const x = useMotionValue(0);
  const actionOpacity = useTransform(x, [-ACTION_W, -24, 0], [1, 0.4, 0]);
  const renameOpacity = useTransform(x, [-SWIPE_OPEN_W, -ACTION_W - 24, -ACTION_W], [1, 0.4, 0]);
  const settle = useCallback(
    (to: number) => {
      animate(x, to, reduced ? { duration: 0.12 } : { type: "spring", stiffness: 520, damping: 42 });
    },
    [x, reduced],
  );
  // Another row opened (or the list scrolled a row away): close this one.
  useEffect(() => {
    if (swipedId !== c.id && x.get() !== 0) settle(0);
  }, [swipedId, c.id, settle, x]);

  const marker = actionMarker(c);
  const time = conversationTime(c, undefined, tz);
  const unread = shouldShowUnreadDot(c);

  const saveRename = async (next: string) => {
    setRenaming(false);
    const prev = displayTitle(c);
    if (!next.trim() || next.trim() === prev) {
      setTitle(prev);
      return;
    }
    setTitle(next.trim());
    try {
      await renameConversation(c.id, next);
      void refreshConversations();
    } catch {
      setTitle(prev);
      toast({ title: "I couldn't rename that", body: "Try again in a moment.", tone: "bad" });
    }
  };

  const body = (
    <>
      <span className="flex w-2.5 flex-none items-center justify-center" aria-hidden={!unread}>
        <LeadMark c={c} edwardMark={edwardMark} />
      </span>
      <span className="min-w-0 flex-1 truncate">{title}</span>
      {marker ? (
        <span className="inline-flex flex-none text-terminal-strong" title="Reached out" aria-label="Reached out">
          <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
        </span>
      ) : null}
      <span
        className={cn(
          "flex-none text-caption font-normal tabular-nums text-text-muted",
          deletable && !touch && "transition-opacity duration-fast md:group-hover/row:opacity-0 md:group-focus-within/row:opacity-0",
          menuOpen && "md:opacity-0",
        )}
      >
        {time}
      </span>
    </>
  );

  const rowClass = cn(
    "relative flex min-h-[44px] w-full items-center gap-2 rounded-md px-2.5 py-2 text-[14px] leading-5 outline-none",
    "transition-[background-color,color] duration-fast",
    "focus-visible:ring-2 focus-visible:ring-terminal/60 focus-visible:ring-inset",
    active
      ? "bg-surface-3 font-medium text-text-primary"
      : unread
        ? "font-semibold text-text-primary hover:bg-surface-3/60"
        : "text-text-secondary hover:bg-surface-3/60 hover:text-text-primary",
  );

  if (renaming) {
    return (
      <div className="px-1 py-0.5">
        <input
          autoFocus
          defaultValue={title}
          aria-label="Rename conversation"
          maxLength={120}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing) return;
            if (e.key === "Enter") void saveRename((e.target as HTMLInputElement).value);
            if (e.key === "Escape") {
              e.stopPropagation();
              setRenaming(false);
            }
          }}
          onBlur={(e) => void saveRename(e.target.value)}
          className="h-10 w-full rounded-md border border-terminal/50 bg-surface px-2.5 text-[16px] text-text-primary outline-none md:text-[14px]"
        />
      </div>
    );
  }

  const link = (
    <Link
      href={`/chat?c=${encodeURIComponent(c.id)}`}
      className={rowClass}
      aria-current={active ? "page" : undefined}
      title={displayTitle(c)}
      draggable={false}
      onClick={(e) => {
        if (swipedId === c.id) {
          e.preventDefault(); // a tap on a swiped row closes it
          setSwipedId(null);
          return;
        }
        if (!e.metaKey && !e.ctrlKey && !e.shiftKey && e.button === 0) onNavigate?.();
      }}
    >
      {body}
    </Link>
  );

  return (
    <div ref={rowRef} className="group/row relative" data-conversation-row={c.id}>
      {touch && deletable ? (
        <>
          <m.button
            type="button"
            style={{ opacity: renameOpacity }}
            onClick={() => {
              setSwipedId(null);
              x.set(0);
              setRenaming(true);
            }}
            className="absolute inset-y-0 right-[88px] flex w-[88px] items-center justify-center gap-1.5 rounded-md bg-surface-3 text-footnote font-semibold text-text-primary"
            aria-label={`Rename ${title}`}
            tabIndex={swipedId === c.id ? 0 : -1}
          >
            <Pencil className="h-4 w-4" aria-hidden /> Rename
          </m.button>
          <m.button
            type="button"
            style={{ opacity: actionOpacity }}
            onClick={() => {
              setSwipedId(null);
              onDelete(c);
            }}
            className="absolute inset-y-0 right-0 flex w-[88px] items-center justify-center gap-1.5 rounded-md bg-bad text-footnote font-semibold text-primary-bg"
            aria-label={`Delete ${title}`}
            tabIndex={swipedId === c.id ? 0 : -1}
          >
            <Trash2 className="h-4 w-4" aria-hidden /> Delete
          </m.button>
          <m.div
            className="relative rounded-md bg-primary-bg md:bg-transparent"
            style={{ x, touchAction: "pan-y" }}
            drag="x"
            dragDirectionLock
            dragConstraints={{ left: -(SWIPE_OPEN_W + ACTION_W * 1.4), right: 0 }}
            dragElastic={{ left: 0.08, right: 0 }}
            dragMomentum={false}
            onDragStart={() => setSwipedId(c.id)}
            onDragEnd={(_: unknown, info: PanInfo) => {
              // The row's own width sets how far a full swipe runs, so a
              // phone's narrow drawer can still delete in one gesture.
              const where = swipeSettle(x.get(), info.velocity.x, ACTION_W, SWIPE_ACTIONS, rowRef.current?.offsetWidth);
              if (where === "delete") {
                setSwipedId(null);
                settle(0);
                onDelete(c);
              } else if (where === "open") {
                setSwipedId(c.id);
                settle(-SWIPE_OPEN_W);
              } else {
                if (swipedId === c.id) setSwipedId(null);
                settle(0);
              }
            }}
          >
            {link}
          </m.div>
        </>
      ) : (
        link
      )}

      {!touch && deletable ? (
        <RowMenu
          open={menuOpen}
          setOpen={setMenuOpen}
          title={title}
          onRename={() => setRenaming(true)}
          onDelete={() => onDelete(c)}
        />
      ) : null}
    </div>
  );
});

/** Desktop: "⋯" on hover or focus, with Rename and Delete. */
function RowMenu({
  open,
  setOpen,
  title,
  onRename,
  onDelete,
}: {
  open: boolean;
  setOpen: (v: boolean) => void;
  title: string;
  onRename: () => void;
  onDelete: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, setOpen]);

  const item =
    "flex h-9 w-full items-center gap-2.5 rounded-sm px-2.5 text-left text-footnote outline-none transition-colors duration-fast focus-visible:bg-surface-3 hover:bg-surface-3";
  return (
    <div ref={ref} className="absolute inset-y-0 right-1 flex items-center">
      <button
        ref={buttonRef}
        type="button"
        aria-label={`More for ${title}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className={cn(
          "inline-flex h-7 w-7 items-center justify-center rounded-sm text-text-muted outline-none transition-[opacity,background-color,color] duration-fast",
          "hover:bg-surface-elevated hover:text-text-primary focus-visible:ring-2 focus-visible:ring-terminal/60",
          open ? "bg-surface-elevated text-text-primary opacity-100" : "opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100 group-focus-within/row:opacity-100",
        )}
      >
        <MoreHorizontal className="h-4 w-4" aria-hidden />
      </button>
      {open ? (
        <div
          role="menu"
          aria-label={`Actions for ${title}`}
          className="absolute right-0 top-full z-30 mt-1 w-44 origin-top-right animate-scale-in rounded-md border border-border bg-surface-elevated p-1 shadow-e2"
        >
          <button
            type="button"
            role="menuitem"
            autoFocus
            className={cn(item, "text-text-primary")}
            onClick={() => {
              setOpen(false);
              onRename();
            }}
          >
            <Pencil className="h-3.5 w-3.5 text-text-muted" aria-hidden /> Rename
          </button>
          <button
            type="button"
            role="menuitem"
            className={cn(item, "text-bad")}
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden /> Delete
          </button>
        </div>
      ) : null}
    </div>
  );
}

// ---- the list -------------------------------------------------------------

function GroupHeader({
  label,
  count,
  open,
  onToggle,
  tone = "neutral",
}: {
  label: string;
  count?: number;
  open?: boolean;
  onToggle?: () => void;
  tone?: "neutral" | "edward";
}) {
  const inner = (
    <>
      {tone === "edward" ? <Zap className="h-3 w-3" aria-hidden /> : null}
      <span>{label}</span>
      {count != null ? <span className="font-normal tabular-nums tracking-normal opacity-70">{count}</span> : null}
      {onToggle ? (
        <ChevronRight className={cn("ml-auto h-3.5 w-3.5 transition-transform duration-fast", open && "rotate-90")} aria-hidden />
      ) : null}
    </>
  );
  const cls = cn(
    "flex min-h-[32px] w-full items-center gap-1.5 rounded-sm px-2.5 pb-1 pt-3 text-overline",
    tone === "edward" ? "text-terminal-strong" : "text-text-muted",
  );
  if (!onToggle) return <h3 className={cls}>{inner}</h3>;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className={cn(cls, "outline-none transition-colors duration-fast hover:text-text-secondary focus-visible:ring-2 focus-visible:ring-terminal/60")}
    >
      {inner}
    </button>
  );
}

function ListBody({ onNavigate }: { onNavigate?: () => void }) {
  const {
    conversations,
    conversationId,
    sourceFilter,
    searchQuery,
    searchResults,
    hasMore,
    isLoadingMore,
    loadMoreConversations,
  } = useChatList();
  const hidden = useHiddenIds();
  const touch = useMediaQuery("(pointer: coarse)");
  const tz = useUserTimezone();
  const onDelete = useDeleteWithUndo();
  const [swipedId, setSwipedId] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const filter = normalizeSidebarFilter(sourceFilter);

  const visible = useMemo(() => conversations.filter((c) => !hidden.has(c.id)), [conversations, hidden]);
  const rowProps = { touch, tz, onNavigate, onDelete, swipedId, setSwipedId };
  const row = (c: Conversation, edwardMark = false) => (
    <ConversationRow key={c.id} conversation={c} active={c.id === conversationId} edwardMark={edwardMark} {...rowProps} />
  );

  if (searchResults !== null) {
    const found = searchResults.filter((c) => !hidden.has(c.id));
    if (found.length === 0) {
      return (
        <p className="px-4 py-10 text-center text-footnote text-text-muted">
          I couldn&apos;t find a conversation matching &ldquo;{searchQuery.trim()}&rdquo;.
        </p>
      );
    }
    return <div className="space-y-px">{found.map((c) => row(c, true))}</div>;
  }

  const mine = visible.filter((c) => matchesSidebarFilter(c, filter));
  if (listPhase(visible.length, mine.length) === "loading") {
    return (
      <div className="space-y-3 px-2.5 pt-4" aria-busy="true" aria-label="Loading conversations">
        <Skeleton lines={5} lineClassName="h-4" />
      </div>
    );
  }
  const more = hasMore ? (
    <div className="px-1 pb-2 pt-3">
      <Button variant="ghost" size="sm" className="w-full" loading={isLoadingMore} onClick={() => void loadMoreConversations()}>
        {isLoadingMore ? "Loading…" : "Show older"}
      </Button>
    </div>
  ) : null;

  if (filter === "edwards") {
    const sections = groupEdwards(mine);
    if (sections.length === 0) {
      return <p className="px-4 py-10 text-center text-footnote text-text-muted">I haven&apos;t started anything on my own yet.</p>;
    }
    return (
      <>
        {sections.map((sec) => {
          const isOpen = !sec.collapsible || (open[sec.key] ?? !sec.collapsed);
          return (
            <section key={sec.key} aria-label={sec.label}>
              <GroupHeader
                label={sec.label}
                count={sec.items.length}
                tone={sec.key === "actions" ? "edward" : "neutral"}
                open={isOpen}
                onToggle={sec.collapsible ? () => setOpen((o) => ({ ...o, [sec.key]: !isOpen })) : undefined}
              />
              {isOpen ? <div className="space-y-px">{sec.items.map((c) => row(c))}</div> : null}
            </section>
          );
        })}
        {more}
      </>
    );
  }

  const groups = groupByDate(mine, undefined, tz);
  if (groups.length === 0) {
    return (
      <p className="px-4 py-10 text-center text-footnote text-text-muted">
        Nothing here yet. Say hi and I&apos;ll keep our conversations here.
      </p>
    );
  }
  return (
    <>
      {groups.map((g) => {
        const isOpen = open[g.key] ?? !g.collapsed;
        return (
          <section key={g.key} aria-label={g.label}>
            <GroupHeader
              label={g.label}
              count={g.collapsed ? g.items.length : undefined}
              open={isOpen}
              onToggle={g.collapsed ? () => setOpen((o) => ({ ...o, [g.key]: !isOpen })) : undefined}
            />
            {isOpen ? <div className="space-y-px">{g.items.map((c) => row(c))}</div> : null}
          </section>
        );
      })}
      {more}
    </>
  );
}

function SearchField() {
  const { searchQuery, setSearchQuery, clearSearch } = useChatList();
  return (
    <div className="relative">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" aria-hidden />
      <input
        type="search"
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        placeholder="Search conversations"
        aria-label="Search conversations"
        enterKeyHint="search"
        className="h-10 w-full rounded-md border border-border bg-surface-3/60 pl-9 pr-9 text-[16px] text-text-primary outline-none transition-[border-color,background-color] duration-fast placeholder:text-text-muted focus:border-text-muted/40 focus:bg-surface md:h-9 md:text-[14px] [&::-webkit-search-cancel-button]:hidden"
      />
      {searchQuery ? (
        <button
          type="button"
          onClick={clearSearch}
          aria-label="Clear search"
          className="absolute right-1 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-sm text-text-muted hover:text-text-primary"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

function GroupSwitch() {
  const { sourceFilter, setSourceFilter, searchResults } = useChatList();
  if (searchResults !== null) return null;
  return (
    <Segment
      label="Conversation group"
      value={normalizeSidebarFilter(sourceFilter)}
      onChange={(v) => setSourceFilter(v)}
      options={SIDEBAR_FILTERS.map((f) => ({ value: f.value, label: f.label }))}
    />
  );
}

/** Kept for callers of the old two-group bar. */
export function SourceFilterBar({ className }: { className?: string } & Record<string, unknown>) {
  return (
    <div className={cn("px-3 py-2", className as string)}>
      <GroupSwitch />
    </div>
  );
}

function useNewChat(after?: () => void) {
  const { startNewChat } = useChatList();
  const router = useRouter();
  return useCallback(() => {
    startNewChat();
    router.push("/chat");
    after?.();
  }, [startNewChat, router, after]);
}

// ---- desktop sidebar ------------------------------------------------------

export function ChatSidebar({ className }: { className?: string }) {
  const { sidebarCollapsed, toggleSidebar } = useChatList();
  const fresh = useNewChat();
  return (
    <LazyMotion features={MOTION_FEATURES} strict>
      <aside
        aria-label="Conversations"
        aria-hidden={sidebarCollapsed || undefined}
        {...({ inert: sidebarCollapsed ? "" : undefined } as Record<string, unknown>)}
        className={cn(
          "hidden flex-none overflow-hidden border-r bg-primary-bg transition-[width,border-color] duration-base ease-in-out motion-reduce:transition-none md:flex",
          sidebarCollapsed ? "w-0 border-transparent" : "w-[18.5rem] border-border",
          className,
        )}
      >
        <div className="flex h-full w-[18.5rem] flex-none flex-col">
          <div className="flex items-center gap-1.5 px-3 pb-2 pt-3">
            <Button variant="secondary" size="sm" className="flex-1 justify-start" icon={<SquarePen aria-hidden />} onClick={fresh} title="New chat (⌘⇧O)">
              New chat
            </Button>
            <Button variant="ghost" size="icon" onClick={toggleSidebar} aria-label="Hide conversations" title="Hide conversations (⌘\)">
              <PanelLeftClose className="h-4 w-4" aria-hidden />
            </Button>
          </div>
          <div className="space-y-2 px-3 pb-2">
            <SearchField />
            <GroupSwitch />
          </div>
          <nav aria-label="Conversation list" className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-4">
            <ListBody />
          </nav>
        </div>
      </aside>
    </LazyMotion>
  );
}

// ---- phone drawer ---------------------------------------------------------

let drawerLocks = 0;
let savedOverflow = "";

export function MobileChatDrawer({ isOpen = false, onClose }: { isOpen?: boolean; onClose?: () => void }) {
  const [summoned, setSummoned] = useState(false);
  const pathname = usePathname();
  const isClient = useIsClient();
  const open = (isOpen || summoned) && pathname === "/chat";

  useEffect(() => {
    const on = () => setSummoned(true);
    window.addEventListener(OPEN_HISTORY_EVENT, on);
    return () => window.removeEventListener(OPEN_HISTORY_EVENT, on);
  }, []);

  const close = useCallback(() => {
    setSummoned(false);
    onClose?.();
  }, [onClose]);

  if (!isClient) return null;
  return createPortal(
    <LazyMotion features={MOTION_FEATURES} strict>
      <AnimatePresence>{open ? <DrawerPanel key="drawer" onClose={close} /> : null}</AnimatePresence>
    </LazyMotion>,
    document.body,
  );
}

function DrawerPanel({ onClose }: { onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement>(null);
  const reduced = usePrefersReducedMotion();
  const fresh = useNewChat(onClose);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  // Esc, Tab trap, focus in and back, body scroll lock.
  useEffect(() => {
    const returnTo = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (drawerLocks++ === 0) {
      savedOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    panelRef.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing) return;
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
      } else if (e.key === "Tab" && panelRef.current) {
        trapTab(e, panelRef.current);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (drawerLocks > 0 && --drawerLocks === 0) document.body.style.overflow = savedOverflow;
      if (returnTo?.isConnected) returnTo.focus({ preventScroll: true });
    };
  }, []);

  const exit = { duration: DURATION.base * EXIT_FACTOR, ease: EASE_IN };
  const panelMotion = reduced
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0, transition: REDUCED_FADE }, transition: REDUCED_FADE }
    : { initial: { x: "-100%" }, animate: { x: 0 }, exit: { x: "-100%", transition: exit }, transition: SPRING_SHEET };

  return (
    <div className="fixed inset-0 z-50 md:hidden">
      <m.div
        aria-hidden
        data-drawer-scrim=""
        className="absolute inset-0 bg-primary-bg/70 backdrop-blur-[3px]"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0, transition: { duration: DURATION.base * EXIT_FACTOR } }}
        transition={reduced ? REDUCED_FADE : { duration: DURATION.base }}
        onClick={onClose}
      />
      <m.div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Conversations"
        tabIndex={-1}
        className="absolute inset-y-0 left-0 flex w-[86vw] max-w-[340px] flex-col border-r border-border bg-primary-bg shadow-e2 outline-none"
        style={{
          paddingTop: "max(env(safe-area-inset-top), 8px)",
          paddingBottom: "max(env(safe-area-inset-bottom), 8px)",
          paddingLeft: "env(safe-area-inset-left)",
        }}
        {...panelMotion}
        drag={reduced ? false : "x"}
        dragDirectionLock
        dragConstraints={{ left: -400, right: 0 }}
        dragElastic={{ left: 0.25, right: 0 }}
        dragMomentum={false}
        onDragEnd={(_: unknown, info: PanInfo) => {
          const w = panelRef.current?.offsetWidth ?? 320;
          if (drawerDragCloses(info.offset.x, info.velocity.x, w)) onClose();
        }}
      >
        <div className="flex items-center gap-1.5 px-3 pb-2 pt-1">
          <h2 className="flex-1 pl-1 text-title3 text-text-primary">Conversations</h2>
          <Button variant="ghost" size="icon" onClick={fresh} aria-label="New chat">
            <SquarePen className="h-5 w-5" aria-hidden />
          </Button>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close conversations">
            <X className="h-5 w-5" aria-hidden />
          </Button>
        </div>
        <div className="space-y-2 px-3 pb-2">
          <SearchField />
          <GroupSwitch />
        </div>
        <nav aria-label="Conversation list" className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-2">
          <ListBody onNavigate={onClose} />
        </nav>
      </m.div>
    </div>
  );
}
