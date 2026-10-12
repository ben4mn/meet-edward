"use client";

// Settings → Attention → Heartbeat: what Edward watched today and what he
// decided. Three views, one URL each:
//   /settings/heartbeat             one status sentence + the day's digest
//   /settings/heartbeat?view=log    every event, filterable, with its detail
//   /settings/heartbeat?view=config what he listens to, how often, and who
//                                   cannot @mention him
// Message text is hidden everywhere until the one "Show message text"
// switch says otherwise (lib/showMessageText.ts). With the mind on, an
// escalation is "handed to his mind" and there is no triage-cycle card.
// Wording and "today" live in heartbeatDigestModel.ts.

import { setVisibleInterval } from "@/lib/visibleInterval";
import React, { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Calendar, Mail, MessageCircle, Plus, X } from "lucide-react";
import {
  getHeartbeatEvents,
  getHeartbeatStatus,
  getRecentSenders,
  getTriageCycles,
  updateHeartbeatConfig,
  type AllowedSender,
  type HeartbeatConfig,
  type HeartbeatEvent,
  type HeartbeatStatus,
  type TriageCycle,
} from "@/lib/api";
import { formatClock, formatDateTime, formatRelative } from "@/lib/time";
import { useUserTimezone } from "@/lib/useTimezone";
import { useShowMessageText } from "@/lib/showMessageText";
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
  Switch,
  cn,
  useToast,
} from "@/components/ui";
import { HeartbeatDigest, ShowTextRow } from "./HeartbeatDigest";
import { NumberField } from "./NumberField";
import {
  heartbeatStatusLine,
  heartbeatView,
  humanStatus,
  reasonFor,
  todaysEvents,
  toneFor,
  whoOf,
  withCurrentOption,
  senderSubline,
  type DigestOpts,
  type HeartbeatView,
} from "./heartbeatDigestModel";

const BASE = "/settings/heartbeat";
const STATUS_POLL_MS = 30_000;
const LOG_PAGE = 100;

const INTERVALS = [
  { label: "1m", value: "60" },
  { label: "5m", value: "300" },
  { label: "15m", value: "900" },
  { label: "30m", value: "1800" },
  { label: "1h", value: "3600" },
];
const POLLS = [
  { label: "10s", value: "10" },
  { label: "30s", value: "30" },
  { label: "1m", value: "60" },
  { label: "5m", value: "300" },
  { label: "15m", value: "900" },
];

export function HeartbeatPanel({ view }: { isExpanded?: boolean; hideHeader?: boolean; view?: HeartbeatView }) {
  return (
    <Suspense
      fallback={
        <Card>
          <Skeleton lines={3} />
        </Card>
      }
    >
      <HeartbeatViews view={view} />
    </Suspense>
  );
}

function HeartbeatViews({ view: forced }: { view?: HeartbeatView }) {
  const params = useSearchParams();
  const view = forced ?? heartbeatView(params);
  const [status, setStatus] = useState<HeartbeatStatus | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await getHeartbeatStatus());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
    return setVisibleInterval(load, STATUS_POLL_MS);
  }, [load]);

  const opts: DigestOpts = { mindEnabled: Boolean(status?.mind_enabled) };

  if (view === "log") return <LogView opts={opts} />;
  if (view === "config") return <ConfigView status={status} onChanged={load} failed={failed} />;
  return <DigestView status={status} failed={failed} opts={opts} onRetry={load} />;
}

// ---- the day ------------------------------------------------------------------

function StatusCard({ status }: { status: HeartbeatStatus }) {
  const dot = status.running ? "bg-ok" : status.enabled ? "bg-warn" : "bg-text-muted";
  return (
    <Card className="flex items-start gap-3 p-4">
      <span aria-hidden className={cn("mt-2 h-2 w-2 flex-none rounded-full", dot)} />
      <p className="min-w-0 flex-1 text-body text-text-primary">{heartbeatStatusLine(status)}</p>
    </Card>
  );
}

function DigestView({
  status,
  failed,
  opts,
  onRetry,
}: {
  status: HeartbeatStatus | null;
  failed: boolean;
  opts: DigestOpts;
  onRetry: () => void;
}) {
  const [cycle, setCycle] = useState<TriageCycle | null>(null);
  const [todayCount, setTodayCount] = useState<number | null>(null);
  const tz = useUserTimezone();
  const mind = Boolean(opts.mindEnabled);

  useEffect(() => {
    if (!status || mind) return;
    getTriageCycles(1)
      .then((rows) => setCycle(rows[0] ?? null))
      .catch(() => {});
  }, [status, mind]);

  useEffect(() => {
    getHeartbeatEvents(500, 0)
      .then((rows) => setTodayCount(todaysEvents(rows, tz).length))
      .catch(() => {});
  }, [tz]);

  return (
    <div className="space-y-6">
      {status ? (
        <StatusCard status={status} />
      ) : failed ? (
        <Notice
          tone="bad"
          title="I can't read my listeners right now."
          action={
            <Button variant="ghost" size="sm" onClick={onRetry}>
              Try again
            </Button>
          }
        />
      ) : (
        <Card>
          <Skeleton lines={2} />
        </Card>
      )}

      {status ? <HeartbeatDigest mindEnabled={mind} waitingCount={status.pending_count} /> : null}

      {!mind && cycle ? (
        <Card className="px-3 py-0">
          <Row
            title="Last look at new messages"
            detail={`${cycle.events_total} ${cycle.events_total === 1 ? "message" : "messages"} · took ${(cycle.duration_ms / 1000).toFixed(1)}s · ${formatRelative(cycle.created_at)}`}
          />
        </Card>
      ) : null}

      <Card className="px-3 py-0">
        <Row
          href={`${BASE}?view=log`}
          title="Full log"
          detail="Every message and event, and what I did with it"
          trailing={todayCount == null ? null : `${todayCount} today`}
        />
        <Row href={`${BASE}?view=config`} title="Configuration" detail="What I listen to, how often, who can't @mention me" />
      </Card>
    </div>
  );
}

// ---- the full log ----------------------------------------------------------------

type SourceFilter = "all" | "imessage" | "calendar" | "email";

const SOURCE_OPTIONS: { value: SourceFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "imessage", label: "Messages" },
  { value: "calendar", label: "Calendar" },
  { value: "email", label: "Email" },
];

function SourceIcon({ source }: { source: string }) {
  if (source === "calendar") return <Calendar />;
  if (source === "email" || source === "gmail") return <Mail />;
  return <MessageCircle />;
}

function LogView({ opts }: { opts: DigestOpts }) {
  const tz = useUserTimezone();
  const [source, setSource] = useState<SourceFilter>("all");
  const [events, setEvents] = useState<HeartbeatEvent[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [showText, setShowText] = useShowMessageText();

  useEffect(() => {
    let cancelled = false;
    setEvents(null);
    getHeartbeatEvents(LOG_PAGE, 0, undefined, source === "all" ? undefined : source)
      .then((rows) => {
        if (cancelled) return;
        setEvents(rows);
        setHasMore(rows.length >= LOG_PAGE);
        setFailed(false);
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [source]);

  const more = async () => {
    if (!events) return;
    setLoadingMore(true);
    try {
      const rows = await getHeartbeatEvents(LOG_PAGE, events.length, undefined, source === "all" ? undefined : source);
      const seen = new Set(events.map((e) => e.id));
      setEvents([...events, ...rows.filter((e) => !seen.has(e.id))]);
      setHasMore(rows.length >= LOG_PAGE);
    } catch {
      /* the button stays; try again */
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <div className="space-y-4">
      <Segment<SourceFilter> label="Show" value={source} onChange={setSource} options={SOURCE_OPTIONS} />
      <ShowTextRow showText={showText} onChange={setShowText} />
      {events === null ? (
        failed ? (
          <Notice tone="bad" title="I can't read the log right now." />
        ) : (
          <Card>
            <Skeleton lines={5} />
          </Card>
        )
      ) : events.length === 0 ? (
        <Card>
          <EmptyState>Nothing here yet — I&apos;ll log messages as they arrive.</EmptyState>
        </Card>
      ) : (
        <Card className="px-3 py-0">
          {events.map((e) => {
            const isOpen = open === e.id;
            const status = humanStatus(e.triage_status, opts);
            const text = showText && e.summary ? ` · ${e.is_from_user ? "you: " : ""}${e.summary}` : "";
            return (
              <div key={e.id} className="border-b border-border last:border-b-0">
                <Row
                  icon={<SourceIcon source={e.source} />}
                  tone={toneFor(e.triage_status, opts)}
                  title={whoOf(e)}
                  detail={`${status}${text}`}
                  trailing={formatClock(e.created_at, { tz })}
                  expanded={isOpen}
                  onClick={() => setOpen(isOpen ? null : e.id)}
                  className="border-b-0"
                />
                {isOpen ? <EventDetail event={e} opts={opts} showText={showText} /> : null}
              </div>
            );
          })}
        </Card>
      )}
      {hasMore ? (
        <Button variant="secondary" className="w-full" loading={loadingMore} onClick={more}>
          Load more
        </Button>
      ) : null}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-caption text-text-muted">{label}</dt>
      <dd className="text-footnote text-text-primary [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

function EventDetail({ event: e, opts, showText }: { event: HeartbeatEvent; opts: DigestOpts; showText: boolean }) {
  const tz = useUserTimezone();
  const raw = (e.raw_data ?? {}) as Record<string, unknown>;
  const str = (k: string) => (typeof raw[k] === "string" ? (raw[k] as string) : "");
  const words = e.source === "email" ? [str("subject"), str("body")].filter(Boolean).join("\n\n") : str("text") || e.summary || "";
  return (
    <div className="mb-3 ml-11 space-y-3 rounded-md bg-surface-3/50 p-3">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
        <Fact label="When">{formatDateTime(e.created_at, { tz })}</Fact>
        <Fact label="From">
          {e.is_from_user ? "You" : e.contact_name || e.chat_name || e.sender || "—"}
          {e.sender && e.sender !== e.contact_name && !e.is_from_user ? (
            <span className="ml-1 font-mono text-caption text-text-muted">{e.sender}</span>
          ) : null}
        </Fact>
        <Fact label="What I did">
          <Pill tone={toneFor(e.triage_status, opts)}>{humanStatus(e.triage_status, opts)}</Pill>
        </Fact>
        {e.chat_name ? <Fact label="Chat">{e.chat_name}</Fact> : null}
      </dl>
      {reasonFor(e, opts) ? <p className="text-footnote text-text-secondary">{reasonFor(e, opts)}</p> : null}
      {e.source === "calendar" ? (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
          {str("title") ? <Fact label="Event">{str("title")}</Fact> : null}
          {str("start_date") ? <Fact label="Starts">{formatDateTime(str("start_date"), { tz })}</Fact> : null}
          {str("location") ? <Fact label="Where">{str("location")}</Fact> : null}
        </dl>
      ) : words ? (
        showText ? (
          <p className="max-h-48 overflow-y-auto whitespace-pre-wrap border-l-2 border-border pl-2.5 text-footnote text-text-primary [overflow-wrap:anywhere]">
            {words}
          </p>
        ) : (
          <p className="text-footnote italic text-text-muted">Message hidden — turn on Show message text to read it.</p>
        )
      ) : null}
    </div>
  );
}

// ---- configuration -----------------------------------------------------------------

function configFrom(s: HeartbeatStatus): HeartbeatConfig {
  return {
    enabled: s.enabled,
    triage_interval_seconds: s.triage_interval_seconds,
    digest_token_cap: 800,
    allowed_senders: s.allowed_senders || [],
    imessage_enabled: s.imessage_enabled ?? false,
    imessage_poll_seconds: s.imessage_poll_seconds ?? 10,
    imessage_thread_limit_direct: s.imessage_thread_limit_direct ?? 30,
    imessage_thread_limit_group: s.imessage_thread_limit_group ?? 75,
    email_enabled: s.email_enabled ?? false,
    email_poll_seconds: s.email_poll_seconds ?? 300,
    gmail_enabled: s.gmail_enabled ?? false,
    gmail_poll_seconds: s.gmail_poll_seconds ?? 120,
  };
}

function ConfigView({ status, onChanged, failed }: { status: HeartbeatStatus | null; onChanged: () => void; failed: boolean }) {
  const { toast } = useToast();
  const [config, setConfig] = useState<HeartbeatConfig | null>(null);
  const [blocking, setBlocking] = useState(false);

  useEffect(() => {
    if (status) setConfig(configFrom(status));
  }, [status]);

  const patch = async (change: Partial<HeartbeatConfig>) => {
    setConfig((c) => (c ? { ...c, ...change } : c));
    try {
      await updateHeartbeatConfig(change);
    } catch {
      toast({ title: "I couldn't save that.", body: "Try again in a moment.", tone: "bad" });
    } finally {
      onChanged();
    }
  };

  if (!config || !status) {
    return (
      <div className="space-y-4">
        {failed ? (
          <Notice tone="bad" title="I can't read my settings right now." />
        ) : (
          <Card>
            <Skeleton lines={4} />
          </Card>
        )}
      </div>
    );
  }

  const trackState = (k: string) => status.tracks?.[k]?.status;
  const blocked = config.allowed_senders;

  return (
    <div className="space-y-6">
      <Card className="flex items-center gap-3 p-4">
        <div className="min-w-0 flex-1">
          <p className="text-callout text-text-primary">Watch your messages</p>
          <p className="text-footnote text-text-muted">
            {config.enabled ? "I read what arrives and decide what matters." : "I'm not reading anything that arrives."}
          </p>
        </div>
        <Switch label="Watch your messages" checked={config.enabled} onChange={(v) => patch({ enabled: v })} />
      </Card>

      <section className="space-y-2">
        <SectionLabel>How often I look at what&apos;s new</SectionLabel>
        <Segment<string>
          label="How often I look"
          value={String(config.triage_interval_seconds)}
          onChange={(v) => patch({ triage_interval_seconds: Number(v) })}
          options={withCurrentOption(INTERVALS, config.triage_interval_seconds)}
        />
      </section>

      <section className="space-y-2">
        <SectionLabel>What I listen to</SectionLabel>
        <TrackCard
          icon={<MessageCircle />}
          name="Messages"
          state={trackState("imessage")}
          enabled={config.imessage_enabled}
          onToggle={(v) => patch({ imessage_enabled: v })}
        >
          <Field label="Check every">
            <Segment<string>
              label="Check messages every"
              value={String(config.imessage_poll_seconds)}
              onChange={(v) => patch({ imessage_poll_seconds: Number(v) })}
              options={withCurrentOption(POLLS, config.imessage_poll_seconds)}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <NumberField
              label="Lines I read (1:1)"
              value={config.imessage_thread_limit_direct}
              min={1}
              max={500}
              onSave={(v) => patch({ imessage_thread_limit_direct: v })}
            />
            <NumberField
              label="Lines I read (group)"
              value={config.imessage_thread_limit_group}
              min={1}
              max={500}
              onSave={(v) => patch({ imessage_thread_limit_group: v })}
            />
          </div>
        </TrackCard>
        <TrackCard
          icon={<Mail />}
          name="Email"
          state={trackState("email")}
          enabled={config.email_enabled}
          onToggle={(v) => patch({ email_enabled: v })}
        >
          <Field label="Check every">
            <Segment<string>
              label="Check email every"
              value={String(config.email_poll_seconds)}
              onChange={(v) => patch({ email_poll_seconds: Number(v) })}
              options={withCurrentOption(POLLS, config.email_poll_seconds)}
            />
          </Field>
        </TrackCard>
      </section>

      <section className="space-y-2">
        <SectionLabel action={blocked.length || null}>Who can&apos;t @mention me</SectionLabel>
        <Card className="px-3 py-0">
          {blocked.length === 0 ? (
            <p className="py-3 text-footnote text-text-muted">Anyone can @mention me in a message.</p>
          ) : (
            blocked.map((s) => (
              <div key={s.identifier} className="flex min-h-[48px] items-center gap-3 border-b border-border last:border-b-0">
                <div className="min-w-0 flex-1 py-2">
                  <p className="truncate text-callout text-text-primary">{s.label}</p>
                  {senderSubline(s.identifier, s.label) ? (
                    <p className="truncate text-caption text-text-muted">{senderSubline(s.identifier, s.label)}</p>
                  ) : null}
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Let ${s.label} @mention me again`}
                  onClick={() => patch({ allowed_senders: blocked.filter((b) => b.identifier !== s.identifier) })}
                >
                  <X />
                </Button>
              </div>
            ))
          )}
        </Card>
        <Button variant="secondary" size="sm" icon={<Plus />} onClick={() => setBlocking(true)}>
          Block someone
        </Button>
        <BlockSheet
          open={blocking}
          onClose={() => setBlocking(false)}
          blocked={blocked}
          onAdd={(s) => {
            if (!blocked.some((b) => b.identifier === s.identifier)) patch({ allowed_senders: [...blocked, s] });
            setBlocking(false);
          }}
        />
      </section>
    </div>
  );
}

function TrackCard({
  icon,
  name,
  state,
  enabled,
  onToggle,
  children,
}: {
  icon: React.ReactNode;
  name: string;
  state?: string;
  enabled: boolean;
  onToggle: (v: boolean) => void;
  children: React.ReactNode;
}) {
  const note = !enabled ? "Off" : state === "error" ? "Not answering" : state === "running" ? "Listening" : "Starting";
  return (
    <Card className="space-y-4 p-4">
      <div className="flex items-center gap-3">
        <span className="flex h-8 w-8 flex-none items-center justify-center rounded-sm bg-surface-3 text-text-secondary [&_svg]:h-4 [&_svg]:w-4">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-callout text-text-primary">{name}</p>
          <p className={cn("text-footnote", enabled && state === "error" ? "text-bad" : "text-text-muted")}>{note}</p>
        </div>
        <Switch label={name} checked={enabled} onChange={onToggle} />
      </div>
      {enabled ? <div className="space-y-4">{children}</div> : null}
    </Card>
  );
}

function BlockSheet({
  open,
  onClose,
  blocked,
  onAdd,
}: {
  open: boolean;
  onClose: () => void;
  blocked: AllowedSender[];
  onAdd: (s: AllowedSender) => void;
}) {
  const [recent, setRecent] = useState<AllowedSender[] | null>(null);
  const [identifier, setIdentifier] = useState("");
  const [label, setLabel] = useState("");

  useEffect(() => {
    if (!open) return;
    setIdentifier("");
    setLabel("");
    getRecentSenders(30)
      .then((rows) => setRecent(rows.filter((r) => !blocked.some((b) => b.identifier === r.identifier))))
      .catch(() => setRecent([]));
  }, [open, blocked]);

  const addManual = () => {
    const id = identifier.trim();
    if (id) onAdd({ identifier: id, label: label.trim() || id });
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Block someone"
      description="They can still text you; an @mention from them just won't reach me."
      footer={
        <Button variant="primary" className="w-full" disabled={!identifier.trim()} onClick={addManual}>
          Block
        </Button>
      }
    >
      <div className="space-y-4">
        {recent === null ? (
          <Skeleton lines={3} />
        ) : recent.length ? (
          <div className="space-y-1">
            <p className="text-overline text-text-muted">Recent senders</p>
            <div>
              {recent.map((r) => (
                <Row key={r.identifier} title={r.label} detail={r.identifier !== r.label ? r.identifier : undefined} onClick={() => onAdd(r)} />
              ))}
            </div>
          </div>
        ) : null}
        <Field label="Phone or email">
          <Input value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder="+1 555 555 0142" autoComplete="off" />
        </Field>
        <Field label="Name (optional)">
          <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Parker" autoComplete="off" />
        </Field>
      </div>
    </Sheet>
  );
}
