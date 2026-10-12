"use client";

// Settings → Work → Scheduled (2026-09-26, Jarvis polish).
//
// Three groups: the owner's reminders; Edward's own wake-ups (a `mind_wake` he set
// with wake_me, and the morning / evening / reflect ticks); and
// housekeeping (the screens pulse, the surplus tick), folded. Repeats are
// said in words ("Every day at 7 am"); what already happened is under Past.
// A row opens its details; cancelling and deleting are undoable. The
// grouping and words live in lib/eventGroups.ts. ?id= (the Work room's
// timeline) opens that row: an upcoming one where it is, a finished one
// under Past, and one on neither page fetched by id.
//
// The list API caps an unfiltered page at 50, oldest first, so Upcoming
// asks for pending and processing rows and Past for the newest page.

import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Bell, Cog, History, Sparkles } from "lucide-react";
import { cancelEvent, deleteEvent, getEvent, listEvents, type ScheduledEventItem } from "@/lib/api";
import { clearUrlParams, readParam } from "@/lib/deepLink";
import { describeCron, eventGroup, eventTitle, groupEvents, resultText, whenLine } from "@/lib/eventGroups";
import { pendingDeletes } from "@/lib/pendingDeletes";
import { scheduleUndoableDelete } from "@/lib/undoableDelete";
import { formatRelative } from "@/lib/time";
import { useUserTimezone } from "@/lib/useTimezone";
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
  useToast,
  type Tone,
} from "@/components/ui";
import { DetailList, type DetailRow } from "./panelBits";

type View = "upcoming" | "past";
const cancelKey = (id: string) => `event-cancel:${id}`;
const deleteKey = (id: string) => `event-delete:${id}`;

const STATUS_PILL: Record<string, { label: string; tone: Tone }> = {
  completed: { label: "Done", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  processing: { label: "Running", tone: "edward" },
};

export interface EventsBrowserProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
  /** Tests: the ?id= deep link without a router. */
  initialId?: string | null;
}

const LIVE = new Set(["pending", "processing"]);

function useHiddenIds(): ReadonlySet<string> {
  const [snap, setSnap] = useState(pendingDeletes.pending());
  useEffect(() => pendingDeletes.subscribe(() => setSnap(pendingDeletes.pending())), []);
  return snap;
}

export function EventsBrowser(props: EventsBrowserProps = {}) {
  return (
    <Suspense
      fallback={
        <Card className="p-4">
          <Skeleton lines={4} />
        </Card>
      }
    >
      <EventsBody initialId={props.initialId} />
    </Suspense>
  );
}

function EventsBody({ initialId }: { initialId?: string | null }) {
  const params = useSearchParams();
  const deepId = initialId ?? readParam(params, "id");
  const tz = useUserTimezone();
  const { toast } = useToast();
  const hidden = useHiddenIds();
  const [view, setView] = useState<View>("upcoming");
  const [live, setLive] = useState<ScheduledEventItem[] | null>(null);
  const [past, setPast] = useState<ScheduledEventItem[] | null>(null);
  const [fetched, setFetched] = useState<ScheduledEventItem | null>(null);
  const [failed, setFailed] = useState(false);
  const [houseOpen, setHouseOpen] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);

  const loadLive = useCallback(async () => {
    setFailed(false);
    try {
      const [pending, running] = await Promise.all([listEvents("pending", "asc"), listEvents("processing", "asc").catch(() => [])]);
      const seen = new Set<string>();
      setLive([...running, ...pending].filter((e) => (seen.has(e.id) ? false : (seen.add(e.id), true))));
    } catch {
      setFailed(true);
    }
  }, []);

  const loadPast = useCallback(async () => {
    try {
      setPast(await listEvents(undefined, "desc"));
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    loadLive();
  }, [loadLive]);

  useEffect(() => {
    if (view === "past" && past === null) loadPast();
  }, [view, past, loadPast]);

  // ?id= opens that row once: where it is when it is still coming up,
  // under Past when it has finished, fetched by id when it is on neither
  // page (the list pages hold 50 rows).
  const openedDeep = useRef<string | null>(null);
  useEffect(() => {
    if (!deepId || openedDeep.current === deepId || live === null) return;
    if (live.some((e) => e.id === deepId)) {
      openedDeep.current = deepId;
      setView("upcoming");
      setOpenId(deepId);
      return;
    }
    if (view !== "past") {
      setView("past");
      return;
    }
    if (past === null) return;
    openedDeep.current = deepId;
    if (past.some((e) => e.id === deepId)) {
      setOpenId(deepId);
      return;
    }
    getEvent(deepId)
      .then((e) => {
        setFetched(e);
        if (LIVE.has(e.status)) setView("upcoming");
        setOpenId(e.id);
      })
      .catch(() => {
        clearUrlParams(["id"]);
        toast({ title: "I can't find that one any more.", tone: "neutral" });
      });
  }, [deepId, live, past, view, toast]);

  const groups = useMemo(() => groupEvents((live ?? []).filter((e) => !hidden.has(cancelKey(e.id)))), [live, hidden]);
  const pastRows = useMemo(
    () => groupEvents((past ?? []).filter((e) => !hidden.has(deleteKey(e.id)))).past,
    [past, hidden],
  );
  const upcomingCount = groups.yours.length + groups.wakes.length;

  const all = [...(live ?? []), ...(past ?? []), ...(fetched ? [fetched] : [])];
  const open = all.find((e) => e.id === openId) ?? null;

  const closeSheet = () => {
    setOpenId(null);
    clearUrlParams(["id"]);
  };

  const cancel = (e: ScheduledEventItem) => {
    closeSheet();
    scheduleUndoableDelete({
      key: cancelKey(e.id),
      commit: () => cancelEvent(e.id).then(() => setPast(null)),
      onCommit: () => setLive((rows) => (rows ? rows.filter((x) => x.id !== e.id) : rows)),
      onError: () => {
        toast({ title: "I couldn't cancel that. It's still scheduled.", tone: "bad" });
        void loadLive();
      },
      toast,
      title: "Cancelled",
      body: eventTitle(e),
      tooLate: "Too late — it's already cancelled.",
    });
  };

  const remove = (e: ScheduledEventItem) => {
    closeSheet();
    scheduleUndoableDelete({
      key: deleteKey(e.id),
      commit: () => deleteEvent(e.id),
      onCommit: () => {
        setPast((rows) => (rows ? rows.filter((x) => x.id !== e.id) : rows));
        setFetched((f) => (f?.id === e.id ? null : f));
      },
      onError: () => {
        toast({ title: "I couldn't delete that. It's back.", tone: "bad" });
        void loadPast();
      },
      toast,
      title: "Deleted",
      body: eventTitle(e),
      tooLate: "Too late — it's already deleted.",
    });
  };

  return (
    <div className="space-y-5">
      <Segment<View>
        label="When"
        value={view}
        onChange={setView}
        options={[
          { value: "upcoming", label: "Upcoming", count: upcomingCount },
          { value: "past", label: "Past" },
        ]}
      />

      {failed ? (
        <Notice
          tone="bad"
          action={
            <Button size="sm" variant="secondary" onClick={() => (view === "past" ? loadPast() : loadLive())}>
              Try again
            </Button>
          }
        >
          I can&apos;t read my schedule right now.
        </Notice>
      ) : null}

      {view === "upcoming" ? (
        live === null && !failed ? (
          <Card className="p-4">
            <Skeleton lines={4} />
          </Card>
        ) : (
          <>
            <section aria-label="Your reminders" className="space-y-2">
              <SectionLabel>Your reminders</SectionLabel>
              {groups.yours.length ? (
                <Card className="px-3 py-0">
                  {groups.yours.map((e) => (
                    <EventRow key={e.id} e={e} tz={tz} onOpen={() => setOpenId(e.id)} />
                  ))}
                </Card>
              ) : (
                <EmptyState className="py-5">Nothing coming up. Ask me to remind you of anything — once or on a schedule.</EmptyState>
              )}
            </section>

            {groups.wakes.length ? (
              <section aria-label="His own wake-ups" className="space-y-2">
                <SectionLabel>His own wake-ups</SectionLabel>
                <Card className="px-3 py-0">
                  {groups.wakes.map((e) => (
                    <EventRow key={e.id} e={e} tz={tz} onOpen={() => setOpenId(e.id)} />
                  ))}
                </Card>
              </section>
            ) : null}

            {groups.housekeeping.length ? (
              <Card className="px-3 py-0">
                <Row
                  icon={<Cog />}
                  title="Housekeeping"
                  detail={`${groups.housekeeping.length} small ${groups.housekeeping.length === 1 ? "job" : "jobs"} I run on a timer`}
                  expanded={houseOpen}
                  onClick={() => setHouseOpen((v) => !v)}
                />
                {houseOpen
                  ? groups.housekeeping.map((e) => <EventRow key={e.id} e={e} tz={tz} onOpen={() => setOpenId(e.id)} />)
                  : null}
              </Card>
            ) : null}
          </>
        )
      ) : past === null && !failed ? (
        <Card className="p-4">
          <Skeleton lines={4} />
        </Card>
      ) : (
        <section aria-label="Past" className="space-y-2">
          {pastRows.length ? (
            <Card className="px-3 py-0">
              {pastRows.map((e) => (
                <EventRow key={e.id} e={e} tz={tz} onOpen={() => setOpenId(e.id)} past />
              ))}
            </Card>
          ) : (
            <EmptyState className="py-5">Nothing has fired yet.</EmptyState>
          )}
        </section>
      )}

      <EventSheet event={open} tz={tz} onClose={closeSheet} onCancel={cancel} onDelete={remove} />
    </div>
  );
}

function EventRow({ e, tz, onOpen, past = false }: { e: ScheduledEventItem; tz: string; onOpen: () => void; past?: boolean }) {
  const group = eventGroup(e);
  const cron = describeCron(e.recurrence_pattern);
  const pill = STATUS_PILL[e.status];
  let detail: string;
  if (past) {
    detail = [whenLine(e.last_fired_at || e.scheduled_at, { tz }), resultText(e.last_result)].filter(Boolean).join(" · ");
  } else if (e.status === "processing") {
    detail = "Running now";
  } else if (e.recurrence_pattern) {
    detail = [cron ?? "Repeats", e.next_fire_at ? `next ${formatRelative(e.next_fire_at)}` : null].filter(Boolean).join(" · ");
  } else {
    detail = whenLine(e.next_fire_at || e.scheduled_at, { tz });
  }
  const icon = past ? <History /> : group === "yours" ? <Bell /> : group === "wakes" ? <Sparkles /> : <Cog />;
  return (
    <Row
      icon={icon}
      tone={!past && group === "wakes" ? "edward" : undefined}
      title={eventTitle(e)}
      detail={detail}
      trailing={past || e.status === "processing" ? pill ? <Pill tone={pill.tone}>{pill.label}</Pill> : null : null}
      onClick={onOpen}
    />
  );
}

function EventSheet({
  event: e,
  tz,
  onClose,
  onCancel,
  onDelete,
}: {
  event: ScheduledEventItem | null;
  tz: string;
  onClose: () => void;
  onCancel: (e: ScheduledEventItem) => void;
  onDelete: (e: ScheduledEventItem) => void;
}) {
  const live = e ? e.status === "pending" || e.status === "processing" : false;
  const rows: DetailRow[] = [];
  if (e) {
    const pill = STATUS_PILL[e.status];
    if (pill) rows.push({ label: "Status", value: pill.label });
    if (live && e.next_fire_at && e.recurrence_pattern) rows.push({ label: "Next", value: whenLine(e.next_fire_at, { tz }) });
    if (!live) rows.push({ label: "When", value: whenLine(e.last_fired_at || e.scheduled_at, { tz }) });
    if (e.recurrence_pattern) {
      const words = describeCron(e.recurrence_pattern);
      rows.push({ label: "Repeats", value: words ?? e.recurrence_pattern, mono: !words });
    }
    rows.push({ label: "Set by", value: e.created_by === "user" ? "You" : "Edward" });
    if (e.delivery_channel) rows.push({ label: "Reaches you by", value: e.delivery_channel });
    if (e.fire_count) {
      rows.push({
        label: "Has run",
        value: `${e.fire_count} ${e.fire_count === 1 ? "time" : "times"}${e.last_fired_at ? ` · last ${formatRelative(e.last_fired_at)}` : ""}`,
      });
    }
  }
  return (
    <Sheet
      open={Boolean(e)}
      onClose={onClose}
      title={e ? eventTitle(e) : ""}
      description={e && !e.recurrence_pattern && live ? whenLine(e.next_fire_at || e.scheduled_at, { tz }) : undefined}
      footer={
        e ? (
          <div className="flex gap-2">
            {live ? (
              <Button variant="ghost" className="text-bad hover:text-bad" onClick={() => onCancel(e)}>
                Cancel it
              </Button>
            ) : (
              <Button variant="ghost" className="text-bad hover:text-bad" onClick={() => onDelete(e)}>
                Delete
              </Button>
            )}
            <div className="flex-1" />
            <Button variant="secondary" onClick={onClose}>
              Done
            </Button>
          </div>
        ) : null
      }
    >
      {e ? (
        <div className="space-y-4 pb-2">
          {eventTitle(e) !== e.description ? (
            <p className="text-footnote text-text-muted [overflow-wrap:anywhere]">{e.description}</p>
          ) : null}
          <DetailList rows={rows} />
          {resultText(e.last_result) ? (
            <div className="space-y-1.5">
              <SectionLabel>What happened last time</SectionLabel>
              <p className="rounded-md bg-surface-3 px-3 py-2.5 text-footnote text-text-secondary [overflow-wrap:anywhere]">
                {resultText(e.last_result)}
              </p>
            </div>
          ) : null}
          {e.conversation_id ? (
            <Button variant="secondary" className="w-full" href={`/chat?c=${encodeURIComponent(e.conversation_id)}`}>
              Open the conversation
            </Button>
          ) : null}
        </div>
      ) : null}
    </Sheet>
  );
}
