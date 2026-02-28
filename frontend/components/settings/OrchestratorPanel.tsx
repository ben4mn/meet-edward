"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  ChevronDown,
  ChevronRight,
  GitBranch,
  RefreshCw,
  Settings2,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  Ban,
  ExternalLink,
  Terminal,
} from "lucide-react";
import {
  getOrchestratorStatus,
  updateOrchestratorConfig,
  cancelOrchestratorTask,
  streamTaskEvents,
  OrchestratorStatus,
  OrchestratorTask,
  OrchestratorConfig,
  CCEvent,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  running: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  completed: "bg-green-500/20 text-green-400 border-green-500/30",
  failed: "bg-red-500/20 text-red-400 border-red-500/30",
  cancelled: "bg-zinc-500/20 text-zinc-400 border-zinc-500/30",
};

const DOT_COLORS: Record<string, string> = {
  pending: "border-yellow-400 bg-yellow-400/30",
  running: "border-blue-400 bg-blue-400/30 animate-pulse",
  completed: "border-green-400 bg-green-400/30",
  failed: "border-red-400 bg-red-400/30",
  cancelled: "border-zinc-400 bg-zinc-400/30",
};

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return "never";
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start) return "-";
  const s = new Date(start);
  const e = end ? new Date(end) : new Date();
  const diffMs = e.getTime() - s.getTime();
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainSec = seconds % 60;
  return `${minutes}m ${remainSec}s`;
}

const MODEL_LABELS: Record<string, string> = {
  "claude-haiku-4-5-20251001": "Haiku 4.5",
  "claude-sonnet-4-6": "Sonnet 4.6",
  "claude-opus-4-6": "Opus 4.6",
  "claude-sonnet-4-5-20250929": "Sonnet 4.5",
};

function modelLabel(model: string): string {
  return MODEL_LABELS[model] || model;
}

interface OrchestratorPanelProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function OrchestratorPanel({ isExpanded: initialExpanded = false, hideHeader = false }: OrchestratorPanelProps) {
  const [isExpanded, setIsExpanded] = useState(initialExpanded || hideHeader);
  const [status, setStatus] = useState<OrchestratorStatus | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  const [expandedTask, setExpandedTask] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      setIsLoading(true);
      const data = await getOrchestratorStatus();
      setStatus(data);
    } catch (err) {
      console.error("Failed to fetch orchestrator status:", err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isExpanded) {
      fetchStatus();
      // Auto-refresh every 5s when expanded
      const interval = setInterval(fetchStatus, 5000);
      return () => clearInterval(interval);
    }
  }, [isExpanded, fetchStatus]);

  const handleToggleEnabled = async () => {
    if (!status) return;
    try {
      await updateOrchestratorConfig({ enabled: !status.config.enabled });
      await fetchStatus();
    } catch (err) {
      console.error("Failed to toggle orchestrator:", err);
    }
  };

  const handleConfigChange = async (field: keyof OrchestratorConfig, value: number | string) => {
    try {
      await updateOrchestratorConfig({ [field]: value });
      await fetchStatus();
    } catch (err) {
      console.error("Failed to update config:", err);
    }
  };

  const handleCancelTask = async (taskId: string) => {
    try {
      await cancelOrchestratorTask(taskId);
      await fetchStatus();
    } catch (err) {
      console.error("Failed to cancel task:", err);
    }
  };

  const activeTasks = status?.recent_tasks.filter(t => t.status === "running" || t.status === "pending") || [];
  const completedTasks = status?.recent_tasks.filter(t => t.status !== "running" && t.status !== "pending") || [];

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      {!hideHeader && (
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full flex items-center justify-between p-4 hover:bg-surface transition-colors"
        >
          <div className="flex items-center gap-3">
            <GitBranch className="w-5 h-5 text-purple-400" />
            <div className="text-left">
              <div className="flex items-center gap-2">
                <span className="font-medium text-text-primary">Orchestrator</span>
                {status && status.active_count > 0 && (
                  <span className="px-1.5 py-0.5 rounded-full text-xs font-medium bg-blue-500/20 text-blue-400">
                    {status.active_count} active
                  </span>
                )}
              </div>
              <span className="text-xs text-text-muted">Parallel worker agents</span>
            </div>
          </div>
          {isExpanded ? (
            <ChevronDown className="w-4 h-4 text-text-muted" />
          ) : (
            <ChevronRight className="w-4 h-4 text-text-muted" />
          )}
        </button>
      )}

      {isExpanded && (
        <div className={cn("space-y-4", hideHeader ? "" : "px-4 pb-4")}>
          {/* Enable toggle + refresh */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <button
                onClick={handleToggleEnabled}
                className={cn(
                  "relative inline-flex h-6 w-11 items-center rounded-full transition-colors",
                  status?.config.enabled ? "bg-purple-500" : "bg-zinc-600"
                )}
              >
                <span
                  className={cn(
                    "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
                    status?.config.enabled ? "translate-x-6" : "translate-x-1"
                  )}
                />
              </button>
              <span className="text-sm text-text-secondary">
                {status?.config.enabled ? "Enabled" : "Disabled"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowConfig(!showConfig)}
                className="p-1.5 rounded hover:bg-surface transition-colors"
                title="Configuration"
              >
                <Settings2 className="w-4 h-4 text-text-muted" />
              </button>
              <button
                onClick={fetchStatus}
                className="p-1.5 rounded hover:bg-surface transition-colors"
                title="Refresh"
                disabled={isLoading}
              >
                <RefreshCw className={cn("w-4 h-4 text-text-muted", isLoading && "animate-spin")} />
              </button>
            </div>
          </div>

          {/* Config section */}
          {showConfig && status && (
            <div className="space-y-3 p-3 rounded-lg bg-surface/50 border border-border">
              <div className="flex items-center justify-between">
                <label className="text-xs text-text-muted">Max Workers</label>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={status.config.max_concurrent_workers}
                  onChange={(e) => handleConfigChange("max_concurrent_workers", parseInt(e.target.value) || 5)}
                  className="w-16 px-2 py-1 text-sm rounded bg-primary-bg border border-border text-text-primary text-right"
                />
              </div>
              <div className="flex items-center justify-between">
                <label className="text-xs text-text-muted">Max CC Sessions</label>
                <input
                  type="number"
                  min={1}
                  max={5}
                  value={status.config.max_concurrent_cc_sessions}
                  onChange={(e) => handleConfigChange("max_concurrent_cc_sessions", parseInt(e.target.value) || 2)}
                  className="w-16 px-2 py-1 text-sm rounded bg-primary-bg border border-border text-text-primary text-right"
                />
              </div>
              <div className="flex items-center justify-between">
                <label className="text-xs text-text-muted">Default Worker Model</label>
                <select
                  value={status.config.default_worker_model}
                  onChange={(e) => handleConfigChange("default_worker_model", e.target.value)}
                  className="px-2 py-1 text-sm rounded bg-primary-bg border border-border text-text-primary"
                >
                  <option value="claude-haiku-4-5-20251001">Haiku 4.5 (fast)</option>
                  <option value="claude-sonnet-4-6">Sonnet 4.6 (balanced)</option>
                  <option value="claude-opus-4-6">Opus 4.6 (powerful)</option>
                </select>
              </div>
              <div className="flex items-center justify-between">
                <label className="text-xs text-text-muted">Default Worker Timeout (s)</label>
                <input
                  type="number"
                  min={30}
                  max={1800}
                  value={status.config.default_worker_timeout}
                  onChange={(e) => handleConfigChange("default_worker_timeout", parseInt(e.target.value) || 300)}
                  className="w-20 px-2 py-1 text-sm rounded bg-primary-bg border border-border text-text-primary text-right"
                />
              </div>
            </div>
          )}

          {/* Active tasks */}
          {activeTasks.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-medium text-text-muted uppercase tracking-wider">Active Workers</h3>
              {activeTasks.map((task) => (
                <TaskRow
                  key={task.id}
                  task={task}
                  isExpanded={expandedTask === task.id}
                  onToggle={() => setExpandedTask(expandedTask === task.id ? null : task.id)}
                  onCancel={() => handleCancelTask(task.id)}
                />
              ))}
            </div>
          )}

          {/* Completed tasks */}
          {completedTasks.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-medium text-text-muted uppercase tracking-wider">Recent History</h3>
              {completedTasks.map((task) => (
                <TaskRow
                  key={task.id}
                  task={task}
                  isExpanded={expandedTask === task.id}
                  onToggle={() => setExpandedTask(expandedTask === task.id ? null : task.id)}
                />
              ))}
            </div>
          )}

          {/* Empty state */}
          {status && status.recent_tasks.length === 0 && (
            <div className="text-center py-6 text-text-muted text-sm">
              No worker tasks yet. Edward will spawn workers when handling complex tasks.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TaskRow({
  task,
  isExpanded,
  onToggle,
  onCancel,
}: {
  task: OrchestratorTask;
  isExpanded: boolean;
  onToggle: () => void;
  onCancel?: () => void;
}) {
  const router = useRouter();
  const [ccEvents, setCcEvents] = useState<CCEvent[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const logContainerRef = useRef<HTMLDivElement>(null);

  // Subscribe to CC events when expanded and task is a running CC session
  useEffect(() => {
    if (!isExpanded) return;
    if (task.task_type !== "cc_session") return;
    if (task.status !== "running") return;

    const abortController = new AbortController();
    setIsStreaming(true);

    (async () => {
      try {
        for await (const event of streamTaskEvents(task.id, abortController.signal)) {
          if (event.event_type === "stream_end") {
            setIsStreaming(false);
            break;
          }
          setCcEvents((prev) => [...prev, event]);
        }
      } catch {
        // Aborted or network error
      } finally {
        setIsStreaming(false);
      }
    })();

    return () => {
      abortController.abort();
      setIsStreaming(false);
    };
  }, [isExpanded, task.id, task.task_type, task.status]);

  // Auto-scroll event log
  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [ccEvents]);

  const statusIcon = () => {
    switch (task.status) {
      case "running": return <Loader2 className="w-3.5 h-3.5 text-blue-400 animate-spin" />;
      case "pending": return <Clock className="w-3.5 h-3.5 text-yellow-400" />;
      case "completed": return <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />;
      case "failed": return <XCircle className="w-3.5 h-3.5 text-red-400" />;
      case "cancelled": return <Ban className="w-3.5 h-3.5 text-zinc-400" />;
      default: return <Clock className="w-3.5 h-3.5 text-zinc-400" />;
    }
  };

  const showViewLink = task.worker_conversation_id && task.task_type !== "cc_session";

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 p-2.5 hover:bg-surface/50 transition-colors text-left"
      >
        {statusIcon()}
        <span className="flex-1 text-sm text-text-primary truncate">{task.task_description}</span>
        {task.task_type === "cc_session" && (
          <span className="text-xs px-1.5 py-0.5 rounded bg-orange-500/20 text-orange-400 border border-orange-500/30 flex items-center gap-1">
            <Terminal className="w-3 h-3" />CC
          </span>
        )}
        <span className="text-xs px-1.5 py-0.5 rounded bg-surface text-text-muted">
          {task.model ? modelLabel(task.model) : "CC"}
        </span>
        <span className="text-xs text-text-muted">
          {formatDuration(task.started_at, task.completed_at)}
        </span>
        {isExpanded ? (
          <ChevronDown className="w-3.5 h-3.5 text-text-muted flex-shrink-0" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-text-muted flex-shrink-0" />
        )}
      </button>

      {isExpanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-border bg-surface/30">
          <div className="pt-2 space-y-1.5 text-xs">
            <div className="flex justify-between">
              <span className="text-text-muted">Status</span>
              <span className={cn(
                "px-1.5 py-0.5 rounded border text-xs",
                STATUS_COLORS[task.status] || STATUS_COLORS.pending
              )}>
                {task.status}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">Context</span>
              <span className="text-text-secondary">{task.context_mode}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">Created</span>
              <span className="text-text-secondary">{timeAgo(task.created_at)}</span>
            </div>
            {showViewLink && (
              <div className="flex justify-between items-center">
                <span className="text-text-muted">Conversation</span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    router.push(`/chat?c=${task.worker_conversation_id}`);
                  }}
                  className="text-purple-400 hover:text-purple-300 flex items-center gap-1"
                >
                  View <ExternalLink className="w-3 h-3" />
                </button>
              </div>
            )}
          </div>

          {/* Live CC event log */}
          {task.task_type === "cc_session" && (ccEvents.length > 0 || isStreaming) && (
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="text-xs text-text-muted">Live Output</span>
                {isStreaming && (
                  <span className="flex items-center gap-1 text-xs text-blue-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                    streaming
                  </span>
                )}
              </div>
              <div
                ref={logContainerRef}
                className="font-mono text-xs bg-zinc-950 border border-zinc-800 rounded p-2 max-h-48 overflow-y-auto space-y-0.5"
              >
                {ccEvents.map((event, i) => (
                  <CCEventLine key={i} event={event} />
                ))}
              </div>
            </div>
          )}

          {task.result_summary && (
            <div className="space-y-1">
              <span className="text-xs text-text-muted">Result</span>
              <p className="text-xs text-text-secondary bg-primary-bg p-2 rounded max-h-32 overflow-y-auto whitespace-pre-wrap">
                {task.result_summary}
              </p>
            </div>
          )}

          {task.error && (
            <div className="space-y-1">
              <span className="text-xs text-red-400">Error</span>
              <p className="text-xs text-red-300 bg-red-500/10 p-2 rounded max-h-32 overflow-y-auto whitespace-pre-wrap">
                {task.error}
              </p>
            </div>
          )}

          {onCancel && (task.status === "running" || task.status === "pending") && (
            <button
              onClick={(e) => { e.stopPropagation(); onCancel(); }}
              className="w-full mt-1 px-3 py-1.5 text-xs rounded bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors"
            >
              Cancel Worker
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function CCEventLine({ event }: { event: CCEvent }) {
  switch (event.event_type) {
    case "cc_text":
      return <div className="text-green-300 whitespace-pre-wrap">{event.text}</div>;
    case "cc_tool_use":
      return (
        <div className="flex items-center gap-1.5">
          <span className="px-1 py-0.5 rounded bg-blue-500/20 text-blue-400 text-[10px]">
            {event.tool_name}
          </span>
        </div>
      );
    case "cc_tool_result":
      return <div className="text-zinc-500 truncate">{event.text || "(result)"}</div>;
    case "cc_error":
      return <div className="text-red-400">{event.error}</div>;
    case "cc_started":
      return <div className="text-zinc-500 italic">Session started</div>;
    case "cc_done":
      return <div className="text-zinc-500 italic">Session {event.status || "completed"}</div>;
    default:
      return null;
  }
}
