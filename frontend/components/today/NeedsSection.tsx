"use client";

// "For you" (UX pass 2026-09-26): the cards that need the owner, capped with
// "+N more", resolving with a height collapse (a plain fade under reduced
// motion), then what the backend held back past its own caps.
//
// Review 2026-09-26: a card on its way out takes no taps, and for a moment
// after one leaves the list takes none either — the card sliding up under
// the owner's finger must not catch the second tap of a double tap (a second
// dose logged, a thread opened by accident).

import React, { useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { AnimatePresence, m, useIsPresent } from "framer-motion";
import { CheckCheck, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, EmptyState, SectionLabel } from "@/components/ui/primitives";
import { DURATION, EASE_IN_OUT, EASE_OUT, EXIT_FACTOR, REDUCED_FADE, usePrefersReducedMotion } from "@/components/ui/motion";
import { cn } from "@/lib/utils";
import { NeedCard } from "./NeedCard";
import {
  heldBackNotes,
  overflowLabel,
  visibleNeeds,
  type HeldBackRow,
  type LiveNote,
  type TodayAction,
  type TodayNeed,
  type TodayOverflow,
} from "./todayModel";

/** A card leaving: still drawn for its exit, never tappable. */
function Leaving({ children, ...props }: React.ComponentProps<typeof m.div>) {
  const present = useIsPresent();
  return (
    <m.div {...props} style={present ? undefined : { pointerEvents: "none" }} aria-hidden={present ? undefined : true}>
      {children}
    </m.div>
  );
}

export interface NeedsSectionProps {
  needs: TodayNeed[];
  overflow: TodayOverflow | undefined;
  resolving: ReadonlySet<string>;
  busy: Readonly<Record<string, string>>;
  errors: Readonly<Record<string, string>>;
  onAction: (need: TodayNeed, action: TodayAction) => void;
  /** Card title links, in order (j / k focus them). */
  linkRefs: React.MutableRefObject<(HTMLAnchorElement | null)[]>;
  onShownChange?: (shown: TodayNeed[]) => void;
  now: Date;
  tz: string | undefined;
}

export function NeedsSection({ needs, overflow, resolving, busy, errors, onAction, linkRefs, onShownChange, now, tz }: NeedsSectionProps) {
  const [expanded, setExpanded] = useState(false);
  const reduced = usePrefersReducedMotion();
  const { shown, more } = visibleNeeds(needs, resolving, expanded);
  const live = needs.filter((n) => !resolving.has(n.id)).length;
  const held = overflowLabel(overflow);

  // Tell the page what is on screen (the keyboard moves through these).
  const shownKey = shown.map((n) => n.id).join("|");
  React.useEffect(() => {
    onShownChange?.(shown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shownKey]);

  const exitS = reduced ? REDUCED_FADE.duration : DURATION.base * EXIT_FACTOR * 1.3;
  const exit = reduced
    ? { opacity: 0, transition: REDUCED_FADE }
    : {
        opacity: 0,
        height: 0,
        paddingBottom: 0,
        transition: { duration: exitS, ease: EASE_IN_OUT },
      };

  // When a card starts to leave, the list settles before it takes taps again.
  const [settling, setSettling] = useState(false);
  const settlingRef = useRef(false);
  const seenResolving = useRef<ReadonlySet<string>>(resolving);
  const settleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useLayoutEffect(() => {
    const grew = Array.from(resolving).some((id) => !seenResolving.current.has(id));
    seenResolving.current = resolving;
    if (!grew) return;
    settlingRef.current = true;
    setSettling(true);
    if (settleTimer.current) clearTimeout(settleTimer.current);
    settleTimer.current = setTimeout(() => {
      settleTimer.current = null;
      settlingRef.current = false;
      setSettling(false);
    }, exitS * 1000 + 150);
  }, [resolving, exitS]);
  useLayoutEffect(
    () => () => {
      if (settleTimer.current) clearTimeout(settleTimer.current);
    },
    [],
  );
  const onCardAction = (need: TodayNeed, a: TodayAction) => {
    if (settlingRef.current) return;
    onAction(need, a);
  };

  return (
    <section aria-labelledby="today-for-you">
      <SectionLabel action={live ? String(live) : null}>
        <span id="today-for-you">For you</span>
      </SectionLabel>
      <div className="mt-2">
        {live === 0 ? (
          <Card className="px-4">
            <EmptyState icon={<CheckCheck />} title="Nothing needs you" className="py-6">
              I&apos;ll put things here when they do.
            </EmptyState>
          </Card>
        ) : (
          <div className={cn(settling && "pointer-events-none")}>
          <AnimatePresence initial={false}>
            {shown.map((n, i) => (
              <Leaving
                key={n.id}
                layout={reduced ? false : "position"}
                initial={reduced ? { opacity: 0 } : { opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0, transition: reduced ? REDUCED_FADE : { duration: DURATION.fast, ease: EASE_OUT } }}
                exit={exit}
                className="overflow-hidden pb-2 [&:has(:focus-visible)]:overflow-visible"
              >
                <NeedCard
                  ref={(el) => {
                    linkRefs.current[i] = el;
                  }}
                  need={n}
                  busyAction={busy[n.id] ?? null}
                  error={errors[n.id] ?? null}
                  onAction={onCardAction}
                  now={now}
                  tz={tz}
                />
              </Leaving>
            ))}
          </AnimatePresence>
          </div>
        )}
        {more > 0 || expanded ? (
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="flex min-h-[44px] w-full items-center justify-center gap-1.5 rounded-md text-footnote font-medium text-text-secondary transition-[background-color,transform] duration-press hover:bg-surface-3/60 hover:text-text-primary active:scale-[.98]"
            aria-expanded={expanded}
          >
            {expanded ? "Show fewer" : `Show ${more} more`}
            <ChevronDown className={cn("h-4 w-4 transition-transform duration-base ease-in-out", expanded && "rotate-180")} aria-hidden />
          </button>
        ) : null}
        {held ? <HeldBack label={held} overflow={overflow!} needs={needs} /> : null}
      </div>
    </section>
  );
}

/** "and 3 more notes": the notes past the backend's cap, fetched on demand —
 *  only those that need the owner and are not a card above (review 2026-09-26). */
function HeldBack({ label, overflow, needs }: { label: string; overflow: TodayOverflow; needs: TodayNeed[] }) {
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState<HeldBackRow[] | null>(null);
  const [failed, setFailed] = useState(false);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (!next || notes || !overflow.note) return;
    setFailed(false);
    try {
      const res = await fetch("/api/notes?hours=24&limit=60", { credentials: "include" });
      if (!res.ok) throw new Error(String(res.status));
      const body = (await res.json()) as { notes?: LiveNote[] };
      setNotes(heldBackNotes(Array.isArray(body.notes) ? body.notes : [], needs));
    } catch {
      setFailed(true);
    }
  };

  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex min-h-[44px] w-full items-center gap-1.5 rounded-md px-1 text-left text-footnote text-text-muted transition-colors duration-fast hover:text-text-primary"
      >
        <span className="flex-1">And {label}</span>
        <ChevronDown className={cn("h-4 w-4 transition-transform duration-base", open && "rotate-180")} aria-hidden />
      </button>
      {open ? (
        <div className="animate-fade-in space-y-1 pb-2">
          {overflow.heartbeat ? (
            <Button variant="ghost" size="sm" href="/settings/heartbeat">
              See every thread
            </Button>
          ) : null}
          {failed ? <p className="px-1 text-footnote text-text-muted">I couldn&apos;t load them just now.</p> : null}
          {notes && notes.length === 0 && overflow.note ? (
            <p className="px-1 text-footnote text-text-muted">They&apos;ve been cleared since.</p>
          ) : null}
          {notes?.map((n) => (
            <Link
              key={n.id}
              href={n.href}
              className="block rounded-md px-3 py-2 text-footnote transition-colors hover:bg-surface-3/60"
            >
              <span className="block text-callout text-text-primary">
                {n.title}
                {n.count > 1 ? <span className="text-text-muted"> · {n.count} notes</span> : null}
              </span>
              {n.body ? <span className="line-clamp-2 text-text-muted">{n.body}</span> : null}
            </Link>
          ))}
        </div>
      ) : null}
    </div>
  );
}
