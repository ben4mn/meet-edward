"use client";

// Settings → Work → Orchestrator: the helpers Edward spins up for big jobs
// (workers inside his process, and Codex coding sessions). One switch, the
// limits, the helper model from the live catalog, and what is running or
// ran lately. A running coding session streams its output when opened.
// ?id= (the Work room's timeline) opens that helper's card and scrolls to
// it; one no longer in the recent list is fetched by id.

import { setVisibleInterval } from "@/lib/visibleInterval";
import React, { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Ban, CheckCircle2, Clock, Terminal, XCircle } from "lucide-react";
import {
  cancelOrchestratorTask,
  getOrchestratorStatus,
  getOrchestratorTask,
  getProviders,
  streamTaskEvents,
  updateOrchestratorConfig,
  type CCEvent,
  type OrchestratorConfig,
  type OrchestratorStatus,
  type OrchestratorTask,
} from "@/lib/api";
import { clearUrlParams, readParam } from "@/lib/deepLink";
import { formatRelative } from "@/lib/time";
import { modelName, modelOptions, parseCatalog, type Catalog } from "@/lib/modelCatalog";
import { NumberField } from "./NumberField";
import {
  Button,
  Card,
  ConfirmSheet,
  EmptyState,
  Field,
  Notice,
  Pill,
  SectionLabel,
  Select,
  Skeleton,
  Switch,
  cn,
  useToast,
  type Tone,
} from "@/components/ui";

const POLL_MS = 5_000;

const STATUS_TONE: Record<string, Tone> = {
  pending: "warn",
  running: "info",
  completed: "ok",
  failed: "bad",
  cancelled: "neutral",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "Waiting",
  running: "Running",
  completed: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function formatTaskDuration(start: string | null, end: string | null, now = Date.now()): string {
  if (!start) return "";
  const s = Date.parse(start);
  const e = end ? Date.parse(end) : now;
  if (!Number.isFinite(s) || !Number.isFinite(e)) return "";
  const secs = Math.max(0, Math.floor((e - s) / 1000));
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  return m < 60 ? `${m}m ${secs % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function OrchestratorPanel(props: { isExpanded?: boolean; hideHeader?: boolean; initialId?: string | null }) {
  return (
    <Suspense
      fallback={
        <Card>
          <Skeleton lines={3} />
        </Card>
      }
    >
      <OrchestratorBody initialId={props.initialId} />
    </Suspense>
  );
}

const taskDomId = (id: string) => `helper-${id}`;

function OrchestratorBody({ initialId }: { initialId?: string | null }) {
  const params = useSearchParams();
  const deepId = initialId ?? readParam(params, "id");
  const { toast } = useToast();
  const [status, setStatus] = useState<OrchestratorStatus | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [fetched, setFetched] = useState<OrchestratorTask | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await getOrchestratorStatus());
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    load();
    getProviders()
      .then((p) => setCatalog(parseCatalog(p)))
      .catch(() => setCatalog(parseCatalog(null)));
    return setVisibleInterval(load, POLL_MS);
  }, [load]);

  // ?id= opens that helper once: in the list when it is recent, else fetched.
  const openedDeep = useRef<string | null>(null);
  useEffect(() => {
    if (!deepId || !status || openedDeep.current === deepId) return;
    openedDeep.current = deepId;
    const reveal = (id: string) => {
      setOpen(id);
      clearUrlParams(["id"]);
      requestAnimationFrame(() => document.getElementById(taskDomId(id))?.scrollIntoView?.({ block: "start", behavior: "smooth" }));
    };
    if (status.recent_tasks.some((t) => t.id === deepId)) {
      reveal(deepId);
      return;
    }
    getOrchestratorTask(deepId)
      .then((t) => {
        setFetched(t);
        reveal(t.id);
      })
      .catch(() => {
        clearUrlParams(["id"]);
        toast({ title: "I can't find that helper any more.", tone: "neutral" });
      });
  }, [deepId, status, toast]);

  const patch = async (change: Partial<OrchestratorConfig>) => {
    setStatus((s) => (s ? { ...s, config: { ...s.config, ...change } } : s));
    try {
      await updateOrchestratorConfig(change);
    } catch {
      toast({ title: "I couldn't save that.", body: "Try again in a moment.", tone: "bad" });
    } finally {
      load();
    }
  };

  const cancel = async (taskId: string) => {
    try {
      await cancelOrchestratorTask(taskId);
    } catch {
      toast({ title: "I couldn't stop that helper.", tone: "bad" });
    } finally {
      load();
    }
  };

  if (!status) {
    return error ? (
      <Notice
        tone="bad"
        title="I can't read my helpers right now."
        action={
          <Button variant="ghost" size="sm" onClick={load}>
            Try again
          </Button>
        }
      />
    ) : (
      <div className="space-y-3">
        <Card>
          <Skeleton lines={2} />
        </Card>
        <Card>
          <Skeleton lines={3} />
        </Card>
      </div>
    );
  }

  const cfg = status.config;
  const tasks = fetched && !status.recent_tasks.some((t) => t.id === fetched.id) ? [fetched, ...status.recent_tasks] : status.recent_tasks;
  const active = tasks.filter((t) => t.status === "running" || t.status === "pending");
  const done = tasks.filter((t) => t.status !== "running" && t.status !== "pending");

  return (
    <div className="space-y-6">
      <Card className="flex items-center gap-3 p-4">
        <div className="min-w-0 flex-1">
          <p className="text-callout text-text-primary">Helpers</p>
          <p className="mt-0.5 text-footnote text-text-muted">
            {cfg.enabled
              ? active.length
                ? `${active.length} working right now.`
                : "When a job is big, I can hand parts of it to helpers."
              : "I do everything myself while this is off."}
          </p>
        </div>
        <Switch label="Helpers" checked={cfg.enabled} onChange={(v) => patch({ enabled: v })} />
      </Card>

      {active.length ? (
        <section className="space-y-2">
          <SectionLabel action={active.length}>Working now</SectionLabel>
          {active.map((t) => (
            <TaskCard
              key={t.id}
              task={t}
              catalog={catalog}
              open={open === t.id}
              onToggle={() => setOpen(open === t.id ? null : t.id)}
              onCancel={() => cancel(t.id)}
            />
          ))}
        </section>
      ) : null}

      <section className="space-y-2">
        <SectionLabel action={done.length || null}>Lately</SectionLabel>
        {done.length === 0 ? (
          active.length === 0 ? (
            <Card>
              <EmptyState>I haven&apos;t needed any helpers yet.</EmptyState>
            </Card>
          ) : null
        ) : (
          done.map((t) => (
            <TaskCard
              key={t.id}
              task={t}
              catalog={catalog}
              open={open === t.id}
              onToggle={() => setOpen(open === t.id ? null : t.id)}
            />
          ))
        )}
      </section>

      <section className="space-y-2">
        <SectionLabel>Limits</SectionLabel>
        <Card className="space-y-4 p-4">
          <Field label="Helper model" hint="The model a helper thinks with unless I choose another.">
            <Select value={cfg.default_worker_model} onChange={(e) => patch({ default_worker_model: e.target.value })}>
              {(catalog ? modelOptions(catalog, cfg.default_worker_model) : [{ value: cfg.default_worker_model, label: cfg.default_worker_model }]).map(
                (o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ),
              )}
            </Select>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <NumberField
              label="Helpers at once"
              value={cfg.max_concurrent_workers}
              min={1}
              max={20}
              onSave={(v) => patch({ max_concurrent_workers: v })}
            />
            <NumberField
              label="Coding sessions at once"
              value={cfg.max_concurrent_cc_sessions}
              min={1}
              max={5}
              onSave={(v) => patch({ max_concurrent_cc_sessions: v })}
            />
          </div>
          <NumberField
            label="Give a helper up after (seconds)"
            value={cfg.default_worker_timeout}
            min={30}
            max={1800}
            onSave={(v) => patch({ default_worker_timeout: v })}
          />
        </Card>
      </section>
    </div>
  );
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "running":
      // Static: nothing loops inside a list (design system §7).
      return (
        <span
          role="img"
          aria-label="Running"
          className="h-2 w-2 rounded-full bg-info shadow-[0_0_0_3px_rgb(var(--info-rgb)/0.18)]"
        />
      );
    case "completed":
      return <CheckCircle2 className="h-4 w-4 text-ok" aria-label="Done" />;
    case "failed":
      return <XCircle className="h-4 w-4 text-bad" aria-label="Failed" />;
    case "cancelled":
      return <Ban className="h-4 w-4 text-text-muted" aria-label="Cancelled" />;
    default:
      return <Clock className="h-4 w-4 text-warn" aria-label="Waiting" />;
  }
}

function TaskCard({
  task,
  catalog,
  open,
  onToggle,
  onCancel,
}: {
  task: OrchestratorTask;
  catalog: Catalog | null;
  open: boolean;
  onToggle: () => void;
  onCancel?: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const isCoding = task.task_type === "cc_session";
  const who = isCoding ? "Coding session" : task.model ? (catalog ? modelName(catalog, task.model) : task.model) : "Helper";
  const duration = formatTaskDuration(task.started_at, task.completed_at);
  const live = task.status === "running" || task.status === "pending";
  return (
    <div id={taskDomId(task.id)} className="scroll-mt-4">
      <Card className="overflow-hidden p-0">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="flex min-h-[56px] w-full items-start gap-3 px-4 py-3 text-left transition-colors duration-fast hover:bg-surface-3/50 active:bg-surface-3/80"
        >
          <span className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center">
            <StatusIcon status={task.status} />
          </span>
          <span className="min-w-0 flex-1">
            <span className={cn("block text-callout text-text-primary", !open && "line-clamp-2")}>{task.task_description}</span>
            <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-footnote text-text-muted">
              {isCoding ? <Terminal className="h-3.5 w-3.5" aria-hidden /> : null}
              <span>{who}</span>
              {duration ? <span>· {duration}</span> : null}
              {!live ? <span>· {formatRelative(task.completed_at || task.updated_at || task.created_at)}</span> : null}
            </span>
          </span>
        </button>
        {open ? (
          <div className="space-y-3 border-t border-border px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <Pill tone={STATUS_TONE[task.status] ?? "neutral"}>{STATUS_LABEL[task.status] ?? task.status}</Pill>
              <span className="text-caption text-text-muted">context: {task.context_mode}</span>
              <span className="text-caption text-text-muted">asked {formatRelative(task.created_at)}</span>
            </div>
            {isCoding ? <CodingLog task={task} /> : null}
            {task.result_summary ? (
              <p className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md bg-surface-3/60 p-3 text-footnote text-text-secondary">
                {task.result_summary}
              </p>
            ) : null}
            {task.error ? (
              <Notice tone="bad" title="It stopped with an error">
                <span className="whitespace-pre-wrap font-mono text-caption">{task.error}</span>
              </Notice>
            ) : null}
            <div className="flex flex-wrap items-center gap-2">
              {task.worker_conversation_id && !isCoding ? (
                <Button variant="secondary" size="sm" href={`/chat?c=${encodeURIComponent(task.worker_conversation_id)}`}>
                  Open its conversation
                </Button>
              ) : null}
              {onCancel && live ? (
                <Button variant="ghost" size="sm" className="ml-auto text-bad hover:text-bad" onClick={() => setConfirm(true)}>
                  Stop this helper
                </Button>
              ) : null}
            </div>
            {onCancel ? (
              <ConfirmSheet
                open={confirm}
                onClose={() => setConfirm(false)}
                onConfirm={async () => {
                  await onCancel();
                  setConfirm(false);
                }}
                title="Stop this helper?"
                body="What it has done so far stays; the rest is dropped."
                confirmLabel="Stop it"
              />
            ) : null}
          </div>
        ) : null}
      </Card>
    </div>
  );
}

function CodingLog({ task }: { task: OrchestratorTask }) {
  const [events, setEvents] = useState<CCEvent[]>([]);
  const [streaming, setStreaming] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (task.status !== "running") return;
    const abort = new AbortController();
    setStreaming(true);
    (async () => {
      try {
        for await (const ev of streamTaskEvents(task.id, abort.signal)) {
          if (ev.event_type === "stream_end") break;
          setEvents((prev) => [...prev, ev]);
        }
      } catch {
        /* aborted or dropped */
      } finally {
        setStreaming(false);
      }
    })();
    return () => abort.abort();
  }, [task.id, task.status]);

  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [events]);

  if (!events.length && !streaming) return null;
  return (
    <div className="space-y-1.5">
      <p className="flex items-center gap-2 text-caption text-text-muted">
        Output {streaming ? <Pill tone="info">live</Pill> : null}
      </p>
      <div
        ref={box}
        className="max-h-48 space-y-0.5 overflow-y-auto rounded-md border border-border bg-primary-bg p-2 font-mono text-caption"
      >
        {events.map((ev, i) => (
          <CodingLine key={i} event={ev} />
        ))}
      </div>
    </div>
  );
}

function CodingLine({ event }: { event: CCEvent }) {
  switch (event.event_type) {
    case "cc_text":
      return <div className="whitespace-pre-wrap text-text-primary">{event.text}</div>;
    case "cc_tool_use":
      return (
        <div>
          <Pill tone="info">{event.tool_name}</Pill>
        </div>
      );
    case "cc_tool_result":
      return <div className="truncate text-text-muted">{event.text || "(result)"}</div>;
    case "cc_error":
      return <div className="text-bad">{event.error}</div>;
    case "cc_started":
      return <div className="italic text-text-muted">Session started</div>;
    case "cc_done":
      return <div className="italic text-text-muted">Session {event.status || "completed"}</div>;
    default:
      return null;
  }
}
