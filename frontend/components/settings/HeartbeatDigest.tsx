"use client";

// The heartbeat's day, decisions first: only what Edward decided (noted,
// watching, handed to his mind, handled by you…) with a one-line reason
// and a "right call?"; everything set aside folded into per-person counts.
// Message bodies stay hidden until the one "Show message text" switch
// (lib/showMessageText.ts) says otherwise — this page is other people's
// words.
//
// The digest fetches its own, unfiltered day. It used to be computed from
// the full log's list, so a source filter or an unloaded page changed what
// "today" said.

import { setVisibleInterval } from "@/lib/visibleInterval";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import type { HeartbeatEvent } from "@/lib/api";
import { getHeartbeatEvents, getHeartbeatFeedback, sendHeartbeatFeedback } from "@/lib/api";
import { formatClock } from "@/lib/time";
import { useUserTimezone } from "@/lib/useTimezone";
import { useShowMessageText } from "@/lib/showMessageText";
import {
  Button,
  Card,
  EmptyState,
  Pill,
  Row,
  SectionLabel,
  Segment,
  Skeleton,
  Switch,
  useToast,
} from "@/components/ui";
import {
  buildDigest,
  feedbackOptions,
  humanStatus,
  reasonFor,
  todaysEvents,
  toneFor,
  whoOf,
} from "./heartbeatDigestModel";

/** A busy day's messages fit; the digest never pages. */
const DAY_LIMIT = 500;
const MAX_SHOWN = 10;
const POLL_MS = 60_000;

export function HeartbeatDigest({
  mindEnabled = false,
  waitingCount,
}: {
  mindEnabled?: boolean;
  waitingCount?: number;
}) {
  const tz = useUserTimezone();
  const { toast } = useToast();
  const [events, setEvents] = useState<HeartbeatEvent[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [given, setGiven] = useState<Record<string, string>>({});
  const [showAll, setShowAll] = useState(false);
  const [asideOpen, setAsideOpen] = useState(false);
  const [openPerson, setOpenPerson] = useState<string | null>(null);
  const [showText, setShowText] = useShowMessageText();

  const load = useCallback(async () => {
    try {
      setEvents(await getHeartbeatEvents(DAY_LIMIT, 0));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
    const stop = setVisibleInterval(load, POLL_MS);
    getHeartbeatFeedback(300)
      .then((rows) => {
        const map: Record<string, string> = {};
        // newest first from the API; keep the latest verdict per event
        for (const r of rows) if (!(r.event_id in map)) map[r.event_id] = r.verdict;
        setGiven(map);
      })
      .catch(() => {});
    return stop;
  }, [load]);

  const today = useMemo(() => todaysEvents(events ?? [], tz), [events, tz]);
  const digest = useMemo(() => buildDigest(today), [today]);
  const opts = { mindEnabled };

  const vote = async (eventId: string, verdict: string) => {
    const before = given[eventId];
    setGiven((g) => ({ ...g, [eventId]: verdict }));
    try {
      await sendHeartbeatFeedback(eventId, verdict);
    } catch {
      setGiven((g) => {
        const next = { ...g };
        if (before) next[eventId] = before;
        else delete next[eventId];
        return next;
      });
      toast({
        title: "I couldn't save that verdict.",
        body: "Try again in a moment.",
        tone: "bad",
      });
    }
  };

  if (events === null) {
    return (
      <div className="space-y-2">
        <SectionLabel>What I decided today</SectionLabel>
        {failed ? (
          <Card>
            <EmptyState
              title="I can't read today's messages right now."
              action={
                <Button variant="secondary" size="sm" onClick={load}>
                  Try again
                </Button>
              }
            />
          </Card>
        ) : (
          <>
            <Card>
              <Skeleton lines={2} />
            </Card>
            <Card>
              <Skeleton lines={2} />
            </Card>
          </>
        )}
      </div>
    );
  }

  const waiting = waitingCount ?? digest.waiting;
  const shown = showAll ? digest.decisions : digest.decisions.slice(0, MAX_SHOWN);

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <SectionLabel action={digest.decisions.length || null}>What I decided today</SectionLabel>
        {digest.decisions.length === 0 ? (
          <Card>
            <EmptyState>
              Nothing needed a decision today
              {digest.setAsideTotal ? ` — I set ${digest.setAsideTotal} aside` : ""}.
            </EmptyState>
          </Card>
        ) : (
          <div className="space-y-2">
            <Card className="px-4 py-0">
              {shown.map((e) => {
                const choices = feedbackOptions(e.triage_status, opts);
                return (
                  <article
                    key={e.id}
                    className="border-b border-border py-3.5 last:border-b-0 md:grid md:grid-cols-[minmax(0,1fr)_auto] md:items-center md:gap-x-8"
                  >
                    <div className="min-w-0">
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="min-w-0 truncate text-callout text-text-primary">{whoOf(e)}</span>
                        <span className="flex-none text-caption tabular-nums text-text-muted">
                          {formatClock(e.created_at, { tz })}
                        </span>
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                        <Pill tone={toneFor(e.triage_status, opts)}>{humanStatus(e.triage_status, opts)}</Pill>
                        <span className="text-footnote text-text-secondary">{reasonFor(e, opts)}</span>
                      </div>
                      {showText && e.summary ? (
                        <p className="mt-2 border-l-2 border-border pl-2.5 text-footnote italic text-text-muted [overflow-wrap:anywhere]">
                          {e.summary}
                        </p>
                      ) : null}
                    </div>
                    <div className="mt-2.5 flex items-center gap-3 md:mt-0 md:w-[272px] md:justify-end">
                      <span className="flex-1 text-footnote text-text-muted md:sr-only">Right call?</span>
                      <Segment<string>
                        label={`Right call on ${whoOf(e)}?`}
                        value={given[e.id] ?? ""}
                        onChange={(v) => {
                          if (v && v !== given[e.id]) vote(e.id, v);
                        }}
                        options={choices.map((c, i) => ({
                          value: c.verdict,
                          label:
                            i === 0 ? (
                              <>
                                <span aria-hidden>Yes</span>
                                <span className="sr-only">{c.label}</span>
                              </>
                            ) : (
                              c.label
                            ),
                        }))}
                        className="flex-none text-caption [&>button]:whitespace-nowrap [&>button]:px-3 md:[&>button]:min-h-[32px]"
                      />
                    </div>
                  </article>
                );
              })}
            </Card>
            {digest.decisions.length > MAX_SHOWN ? (
              <Button variant="ghost" className="w-full" onClick={() => setShowAll((v) => !v)}>
                {showAll ? "Show fewer" : `Show all ${digest.decisions.length}`}
              </Button>
            ) : null}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <Card className="px-3 py-0">
          <Row
            title="Set aside"
            detail={
              digest.setAsideTotal
                ? `${digest.setAsideTotal} from ${digest.setAside.length} ${digest.setAside.length === 1 ? "person" : "people"}`
                : "Nothing set aside today"
            }
            expanded={asideOpen}
            onClick={() => setAsideOpen((o) => !o)}
          />
          {asideOpen
            ? digest.setAside.map((p) => {
                const open = openPerson === p.who;
                return (
                  <div key={p.who} className="border-b border-border last:border-b-0">
                    <Row
                      title={p.who}
                      trailing={p.count}
                      expanded={open}
                      onClick={() => setOpenPerson(open ? null : p.who)}
                      className="border-b-0 pl-3"
                    />
                    {open ? (
                      <ul className="mb-3 ml-3 space-y-1 border-l-2 border-border pl-3 text-footnote text-text-muted">
                        {p.events.map((e) => (
                          <li key={e.id} className="flex gap-2">
                            <span className="flex-none tabular-nums">{formatClock(e.created_at, { tz })}</span>
                            <span className="[overflow-wrap:anywhere]">
                              {showText ? e.summary || "" : "message hidden"}
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                );
              })
            : null}
        </Card>
        <ShowTextRow showText={showText} onChange={setShowText} />
        {waiting > 0 ? (
          <p className="px-0.5 text-footnote text-text-muted">
            {waiting} message{waiting === 1 ? "" : "s"} waiting for my next look.
          </p>
        ) : null}
      </section>
    </div>
  );
}

/** The one privacy switch, shared by the digest and the full log. */
export function ShowTextRow({ showText, onChange }: { showText: boolean; onChange: (v: boolean) => void }) {
  return (
    <Card className="flex items-center gap-3 px-4 py-3">
      <div className="min-w-0 flex-1">
        <p className="text-callout text-text-primary">Show message text</p>
        <p className="text-footnote text-text-muted">Off by default — this page is other people&apos;s words.</p>
      </div>
      <Switch label="Show message text" checked={showText} onChange={onChange} />
    </Card>
  );
}
