"use client";

// Today — the PWA's front door (UX pass 2026-09-26): a briefing that shows
// Edward has been working for the owner. His presence and greeting first,
// then what needs the owner, what he did and the owner's threads.
// Paints at once from the last feed, revalidates quietly, and never spins.
//
// Desktop (lg): two panes — hero and For you on the left; What I did and
// conversations on the right, sticky. (Goals and ideas left Today with
// unit G of the proactivity rebuild, 2026-10-11.) j / k move between
// cards, e runs the primary action, x dismisses.
//
// Review 2026-09-26: one action per card (a double tap, or a tap on a card
// already leaving, does nothing); a resolved card stays gone until a feed
// fetched after it agrees; Done waits out an Undo before it is sent; an
// action on a feed painted from the cache is checked against a fresh one
// first; a held send is "Sent" only when the server says it was delivered.

import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { LazyMotion, m } from "framer-motion";
import { MOTION_FEATURES } from "@/components/ui/motionFeatures";
import { RefreshCw, WifiOff } from "lucide-react";
// Leaf imports, not the components/ui barrel: the barrel carries Sheet,
// Switch and PanelShell onto Today's first load.
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/primitives";
import { EdwardPulse } from "@/components/ui/Pulse";
import { TOAST_EXIT_MS, useToast } from "@/components/ui/toastContext";
import { DURATION, EASE_OUT, REDUCED_FADE, usePrefersReducedMotion } from "@/components/ui/motion";
import { SCROLL_TOP_EVENT } from "@/components/layout/scrollTopEvent";
import { postSignal, runTodayAction } from "@/lib/api";
import { buildTodayActionSignal } from "@/lib/signals";
import { useUserTimezone } from "@/lib/timezoneContext";
import { lazyComponent } from "@/lib/lazyComponent";
import { cn } from "@/lib/utils";
import { NeedsSection } from "./NeedsSection";
import { TodayHero, useMinuteClock } from "./TodayHero";
import {
  ConversationsSkeleton,
  DidSkeleton,
  ListSkeleton,
  NeedsSkeleton,
} from "./TodaySkeleton";
import { pullContentOffset, pullPipY, pullProgress } from "./pullToRefresh";
import { RESOLVED_HOLD_MS, staleNote, retryDelayMs, stillHidden } from "./todayFeedState";
import { focusedCardIndex, keyAction, todayKeyCommand } from "./todayKeys";
import {
  afterAction,
  dismissAction,
  isDeferredDone,
  receiptFor,
  splitDid,
  undoFor,
  type TodayAction,
  type TodayNeed,
} from "./todayModel";
import { rememberScroll, useTodayFeed } from "./useTodayFeed";
import { useStickyTop } from "./stickySide";
import { usePullToRefresh } from "./usePullToRefresh";


const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

// Below the fold (2026-09-26 perf pass): what I did and the
// conversations are one chunk of their own, off Today's first load. The
// fetch starts as this module runs, so by the time the feed paints they are
// almost always here and render at once; if not, a skeleton of the same
// shape stands in (lib/lazyComponent). __tests__/firstLoadBundle.test.ts pins
// that TodayView does not import them statically.
const belowFold = () => import("./belowFold");
const DidSection = lazyComponent(() => belowFold().then((mod) => mod.DidSection), DidSkeleton);
const ConversationsSection = lazyComponent(() => belowFold().then((mod) => mod.ConversationsSection), ConversationsSkeleton);
if (typeof window !== "undefined") {
  for (const section of [DidSection, ConversationsSection]) {
    section.preload().catch(() => {
      /* retried when the section mounts */
    });
  }
}

function isEditable(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
}

/** First paint: each section fades up 180 ms, 30 ms after the one before. */
function Reveal({ i, reduced, children, className }: { i: number; reduced: boolean; children: React.ReactNode; className?: string }) {
  return (
    <m.div
      className={className}
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? REDUCED_FADE : { duration: 0.18, delay: i * 0.03, ease: EASE_OUT }}
    >
      {children}
    </m.div>
  );
}

function ColdError({ failures, onRetry, retrying }: { failures: number; onRetry: () => void; retrying: boolean }) {
  const secs = Math.round(retryDelayMs(failures) / 1000);
  return (
    <Card className="flex items-start gap-3 p-4">
      <span aria-hidden className="mt-0.5 flex h-9 w-9 flex-none items-center justify-center rounded-md bg-surface-3 text-text-secondary">
        <WifiOff className="h-[18px] w-[18px]" />
      </span>
      <div className="min-w-0 flex-1" role="status">
        <p className="text-callout text-text-primary">I can&apos;t reach the Mac right now — trying again</p>
        <p className="mt-0.5 text-footnote text-text-muted">
          {retrying ? "Trying now…" : `Next try in ${secs < 60 ? `${secs} s` : "a minute"}.`}
        </p>
        <Button variant="secondary" className="mt-3" loading={retrying} onClick={onRetry}>
          Retry
        </Button>
      </div>
    </Card>
  );
}

/** How long Done can be taken back before it is sent. */
export const DONE_UNDO_MS = 5000;

type Outcome = { ok: true; result: unknown } | { ok: false };

export function TodayView({ undoWindowMs = DONE_UNDO_MS }: { undoWindowMs?: number } = {}) {
  const tz = useUserTimezone();
  const router = useRouter();
  const { toast } = useToast();
  const reduced = usePrefersReducedMotion();
  const now = useMinuteClock();
  const { state, phase, load, patch, reload, restoreScrollTop } = useTodayFeed();
  const feed = state.feed;
  const confirmedRef = useRef(state.confirmed);
  confirmedRef.current = state.confirmed;

  const scrollRef = useRef<HTMLDivElement>(null);
  const asideRef = useRef<HTMLElement>(null);
  const asideTop = useStickyTop(scrollRef as React.RefObject<HTMLElement>, asideRef as React.RefObject<HTMLElement>);
  const linkRefs = useRef<(HTMLAnchorElement | null)[]>([]);
  const shownRef = useRef<TodayNeed[]>([]);

  // Needs the owner has acted on: id → when the server confirmed it (ms), or
  // Infinity while the action is in flight or waiting out its Undo. They
  // stay hidden until a later feed agrees (todayFeedState.stillHidden).
  const [hidden, setHiddenState] = useState<ReadonlyMap<string, number>>(() => new Map());
  const hiddenRef = useRef<ReadonlyMap<string, number>>(hidden);
  const updateHidden = useCallback((fn: (m: ReadonlyMap<string, number>) => ReadonlyMap<string, number>) => {
    const next = fn(hiddenRef.current);
    if (next === hiddenRef.current) return;
    hiddenRef.current = next;
    setHiddenState(next);
  }, []);
  const hideAs = useCallback(
    (id: string, at: number | null) =>
      updateHidden((m) => {
        const next = new Map(m);
        if (at === null) next.delete(id);
        else next.set(id, at);
        return next;
      }),
    [updateHidden],
  );
  const resolving = useMemo<ReadonlySet<string>>(() => new Set(hidden.keys()), [hidden]);
  // Cards busy with a request that keeps them on screen (a held send, a check).
  const working = useRef(new Set<string>());
  // Done taps waiting out their Undo.
  const pending = useRef(new Map<string, { need: TodayNeed; a: TodayAction; timer: ReturnType<typeof setTimeout> }>());
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  // ---- scroll memory: back where the owner left it, painted from the cache ----
  const restored = useRef(false);
  useIsoLayoutEffect(() => {
    if (restored.current || !feed || !scrollRef.current) return;
    restored.current = true;
    if (restoreScrollTop > 0) scrollRef.current.scrollTop = restoreScrollTop;
  }, [feed, restoreScrollTop]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        rememberScroll(el.scrollTop);
      });
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
      rememberScroll(el.scrollTop, true);
    };
  }, []);

  // ---- scroll to top + refresh when the dock asks -----------------------
  useEffect(() => {
    const onTop = () => {
      scrollRef.current?.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
      void load();
    };
    window.addEventListener(SCROLL_TOP_EVENT, onTop);
    return () => window.removeEventListener(SCROLL_TOP_EVENT, onTop);
  }, [load, reduced]);

  // ---- what the owner resolved: settled against every new feed ------------
  useEffect(() => {
    const ids = new Set((feed?.needs ?? []).map((n) => n.id));
    const settle = () => updateHidden((m) => stillHidden(m, ids, state.okAt, Date.now()));
    settle();
    if (!hiddenRef.current.size) return;
    const t = setTimeout(settle, RESOLVED_HOLD_MS + 500);
    return () => clearTimeout(t);
  }, [feed, state.okAt, hidden, updateHidden]);

  // Needs that were here and are not any more (the greeting stops naming them).
  const seen = useRef(new Map<string, TodayNeed>());
  const { gone, live } = useMemo(() => {
    const liveNeeds = (feed?.needs ?? []).filter((n) => !resolving.has(n.id));
    for (const n of feed?.needs ?? []) seen.current.set(n.id, n);
    const liveIds = new Set(liveNeeds.map((n) => n.id));
    return { live: liveNeeds, gone: Array.from(seen.current.values()).filter((n) => !liveIds.has(n.id)) };
  }, [feed, resolving]);

  // ---- actions -----------------------------------------------------------
  const setFor = <T,>(set: React.Dispatch<React.SetStateAction<Record<string, T>>>, id: string, v: T | null) =>
    set((prev) => {
      const next = { ...prev };
      if (v === null) delete next[id];
      else next[id] = v;
      return next;
    });

  /** Send one action and settle the card: gone for good on success, back
   *  with the error on failure. A held send stays on screen while it goes. */
  const commit = useCallback(
    async (need: TodayNeed, a: TodayAction): Promise<Outcome> => {
      const waits = need.kind === "approval";
      setFor(setErrors, need.id, null);
      if (waits) {
        working.current.add(need.id);
        setFor(setBusy, need.id, a.id);
      } else hideAs(need.id, Infinity);
      try {
        const result = await runTodayAction(a);
        const answered = need.choice && (a.id === "both" || need.choice.options.some((o) => o.action_id === a.id));
        if (answered) {
          const done = dismissAction(need);
          if (done) void runTodayAction(done).catch(() => undefined);
        }
        postSignal(buildTodayActionSignal(need, a, true));
        hideAs(need.id, Date.now());
        patch((f) => ({ ...f, needs: afterAction(f.needs, need.id) }));
        return { ok: true, result };
      } catch (e) {
        const text = e instanceof Error && e.message ? e.message : "That didn't go through.";
        postSignal(buildTodayActionSignal(need, a, false, text));
        hideAs(need.id, null);
        setFor(setErrors, need.id, `That didn't go through. ${text}`.trim());
        return { ok: false };
      } finally {
        working.current.delete(need.id);
        setFor(setBusy, need.id, null);
      }
    },
    [hideAs, patch],
  );

  /** A Done whose Undo window has closed (or the page is going away): send it. */
  const flushDone = useCallback(
    (id: string) => {
      const p = pending.current.get(id);
      if (!p) return;
      clearTimeout(p.timer);
      pending.current.delete(id);
      void commit(p.need, p.a).then((out) => {
        if (out.ok) setTimeout(() => void load(), 1500);
      });
    },
    [commit, load],
  );
  const flushRef = useRef(flushDone);
  flushRef.current = flushDone;

  // Leaving Today, or the app going to the background, sends what waits.
  useEffect(() => {
    const flushAll = () => Array.from(pending.current.keys()).forEach((id) => flushRef.current(id));
    const onHidden = () => {
      if (document.visibilityState === "hidden") flushAll();
    };
    document.addEventListener("visibilitychange", onHidden);
    window.addEventListener("pagehide", flushAll);
    return () => {
      document.removeEventListener("visibilitychange", onHidden);
      window.removeEventListener("pagehide", flushAll);
      flushAll();
    };
  }, []);

  const act = useCallback(
    async (need: TodayNeed, a: TodayAction) => {
      if (a.method === "GET") {
        router.push(a.url);
        return;
      }
      // One action per card: a double tap, or a tap on a card already on
      // its way out, does nothing.
      if (hiddenRef.current.has(need.id) || working.current.has(need.id)) return;

      // A feed painted from the cache (or kept through a long sleep) may be
      // last night's: check the card is still there, as it was, first.
      if (!confirmedRef.current) {
        working.current.add(need.id);
        setFor(setBusy, need.id, a.id);
        const fresh = await load();
        working.current.delete(need.id);
        setFor(setBusy, need.id, null);
        const still = fresh?.needs.find((n) => n.id === need.id);
        const same = still?.actions.find((x) => x.id === a.id && x.method === a.method && x.url === a.url);
        if (!fresh) {
          setFor<string>(setErrors, need.id, "I couldn't check this is still current. Try again in a moment.");
          return;
        }
        if (!still || !same) {
          toast({ title: "That changed — here's the latest", tone: "neutral" });
          return;
        }
        need = still;
        a = same;
      }

      // Done waits out an Undo before it is sent (design §1.7).
      if (isDeferredDone(need, a)) {
        hideAs(need.id, Infinity);
        const receipt = receiptFor(need, a);
        const id = need.id;
        pending.current.set(id, { need, a, timer: setTimeout(() => flushRef.current(id), undoWindowMs + TOAST_EXIT_MS) });
        toast({
          title: receipt.title,
          body: receipt.body,
          tone: receipt.tone,
          duration: undoWindowMs,
          undo: () => {
            const p = pending.current.get(id);
            if (!p) {
              toast({ title: "Too late to undo that one", tone: "neutral" });
              return;
            }
            clearTimeout(p.timer);
            pending.current.delete(id);
            hideAs(id, null);
          },
        });
        return;
      }

      const out = await commit(need, a);
      if (!out.ok) return;
      const receipt = receiptFor(need, a, out.result);
      const undo = undoFor(need, a);
      const open = receipt.open;
      toast({
        title: receipt.title,
        body: receipt.body,
        tone: receipt.tone,
        ...(receipt.tone === "bad" ? { duration: 9000 } : {}),
        undo: undo
          ? () => {
              runTodayAction(undo)
                .then(() => {
                  hideAs(need.id, null);
                  return reload();
                })
                .catch(() => toast({ title: "I couldn't undo that", tone: "bad" }));
            }
          : undefined,
        action: open ? { label: "Open", onClick: () => router.push(open) } : undefined,
      });
      setTimeout(() => void load(), 1500);
    },
    [commit, hideAs, load, reload, router, toast, undoWindowMs],
  );

  // ---- keyboard: j / k / e / x ------------------------------------------
  // The focused card is read from the DOM at each keypress (review
  // 2026-09-26): once focus leaves a card, e and x do nothing, and no key
  // ever answers a held send.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const cards = linkRefs.current.map((l) =>
        l ? { link: l, contains: (n: unknown) => !!l.closest("article")?.contains(n as Node) } : null,
      );
      const cmd = todayKeyCommand(
        { key: e.key, metaKey: e.metaKey, ctrlKey: e.ctrlKey, altKey: e.altKey, editable: isEditable(e.target) },
        { focused: focusedCardIndex(cards, document.activeElement), count: shownRef.current.length },
      );
      if (!cmd) return;
      if (cmd.kind === "focus") {
        e.preventDefault();
        const link = linkRefs.current[cmd.index];
        link?.focus();
        link?.closest("article")?.scrollIntoView?.({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
        return;
      }
      const need = shownRef.current[cmd.index];
      const target = need ? keyAction(need, cmd.kind) : null;
      if (!need || !target) return;
      e.preventDefault();
      void act(need, target);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [act, reduced]);

  // ---- pull to refresh ----------------------------------------------------
  const pull = usePullToRefresh(scrollRef as React.RefObject<HTMLElement>, load);
  const pullShown = pull.offset > 0 || pull.refreshing;
  // While the refresh runs the page keeps a gap at the top for the pip.
  const contentOffset = pullContentOffset(pull.offset, pull.refreshing);

  const wakes = feed ? splitDid(feed.did).wakes : null;
  const cold = phase === "cold";
  const errorCold = phase === "error";

  return (
    <LazyMotion features={MOTION_FEATURES} strict>
      <div
        ref={scrollRef}
        data-today-scroll=""
        className="relative min-h-0 flex-1 overflow-y-auto overscroll-y-none bg-primary-bg [-webkit-tap-highlight-color:transparent]"
      >
        {/* Pull to refresh: Edward's pip, filling as the finger pulls. */}
        <div
          aria-hidden={!pull.refreshing}
          className={cn(
            "pointer-events-none absolute inset-x-0 top-0 z-10 flex justify-center transition-opacity duration-fast",
            pullShown ? "opacity-100" : "opacity-0",
          )}
          style={{ transform: `translateY(${pullPipY(pull.offset, pull.refreshing)}px)`, ...(pull.refreshing ? {} : { opacity: pullProgress(pull.offset) }) }}
        >
          <span
            className="flex h-8 w-8 items-center justify-center rounded-full border border-border bg-surface-elevated shadow-e2"
            style={{ transform: pull.refreshing ? undefined : `scale(${0.6 + 0.4 * pullProgress(pull.offset)})` }}
          >
            {pull.refreshing ? (
              <EdwardPulse label="Refreshing" size="sm" />
            ) : (
              <RefreshCw
                className="h-3.5 w-3.5 text-text-secondary"
                style={{ transform: `rotate(${pullProgress(pull.offset) * 270}deg)` }}
              />
            )}
          </span>
        </div>

        <div
          className={cn("mx-auto w-full max-w-2xl px-4 pb-10 pt-4 md:pt-8 lg:max-w-6xl lg:px-8", !pull.dragging && "transition-transform duration-base ease-out")}
          style={contentOffset ? { transform: `translateY(${contentOffset}px)` } : undefined}
        >
          <h1 className="sr-only">Today</h1>
          <div className="lg:grid lg:grid-cols-12 lg:gap-x-10">
            <div className="space-y-7 lg:col-span-7">
              <Reveal i={0} reduced={reduced}>
                <TodayHero
                  presence={feed?.presence ?? null}
                  wakes={wakes?.count ?? null}
                  actions={feed?.did ?? []}
                  tz={tz}
                  gone={gone}
                  live={live}
                />
                {phase === "stale" && state.okAt ? (
                  <button
                    type="button"
                    onClick={() => void load()}
                    className="mt-3 inline-flex min-h-[32px] items-center gap-1.5 rounded-full bg-surface-3/70 px-3 text-caption text-text-muted transition-colors hover:text-text-primary"
                  >
                    <span className="h-1.5 w-1.5 rounded-full bg-warn" aria-hidden />
                    <span suppressHydrationWarning>{staleNote(state.okAt, now.getTime())}</span>
                    <span className="text-text-secondary">· Retry</span>
                  </button>
                ) : null}
              </Reveal>

              {errorCold ? (
                <Reveal i={1} reduced={reduced}>
                  <ColdError failures={state.failures} onRetry={() => void load()} retrying={state.refreshing} />
                </Reveal>
              ) : cold ? (
                <div role="status" aria-label="Loading Today">
                  <NeedsSkeleton />
                </div>
              ) : feed ? (
                <>
                  <Reveal i={1} reduced={reduced}>
                    <NeedsSection
                      needs={feed.needs}
                      overflow={feed.overflow}
                      resolving={resolving}
                      busy={busy}
                      errors={errors}
                      onAction={act}
                      linkRefs={linkRefs}
                      onShownChange={(shown) => {
                        shownRef.current = shown;
                        linkRefs.current.length = shown.length;
                      }}
                      now={now}
                      tz={tz}
                    />
                  </Reveal>
                </>
              ) : null}
            </div>

            <aside
              ref={asideRef}
              className="mt-7 space-y-7 lg:sticky lg:col-span-5 lg:mt-0 lg:self-start lg:[top:var(--aside-top)]"
              style={{ ["--aside-top" as string]: `${asideTop}px` }}
              aria-label="What Edward did"
            >
              {cold ? (
                <>
                  <ListSkeleton label="What I did" rows={4} />
                </>
              ) : feed ? (
                <>
                  <Reveal i={4} reduced={reduced}>
                    <DidSection did={feed.did} now={now} tz={tz} />
                  </Reveal>
                  <Reveal i={5} reduced={reduced}>
                    <ConversationsSection conversations={feed.conversations} thoughts={feed.thoughts_today} now={now} />
                  </Reveal>
                </>
              ) : null}
            </aside>
          </div>
        </div>
      </div>
    </LazyMotion>
  );
}
