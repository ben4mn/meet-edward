"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Calendar,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  HeartPulse,
  Loader2,
  Mail,
  MessageCircle,
  RefreshCw,
  Settings2,
  Shield,
  X,
  Plus,
  UserPlus,
} from "lucide-react";
import {
  getHeartbeatStatus,
  getHeartbeatEvents,
  getTriageCycles,
  getRecentSenders,
  updateHeartbeatConfig,
  HeartbeatEvent,
  HeartbeatStatus,
  HeartbeatConfig,
  AllowedSender,
  TriageCycle,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const TRIAGE_COLORS: Record<string, string> = {
  pending: "bg-green-500/20 text-green-400 border-green-500/30",
  dismissed: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  noted: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  acted: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  escalated: "bg-red-500/20 text-red-400 border-red-500/30",
  calendar_urgent: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  mention: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
};

const DOT_COLORS: Record<string, string> = {
  pending: "border-green-400 bg-green-400/30",
  dismissed: "border-gray-400 bg-gray-400/30",
  noted: "border-blue-400 bg-blue-400/30",
  acted: "border-purple-400 bg-purple-400/30",
  escalated: "border-red-400 bg-red-400/30",
  calendar_urgent: "border-orange-400 bg-orange-400/30",
  mention: "border-yellow-400 bg-yellow-400/30",
};

const INTERVAL_OPTIONS = [
  { label: "1m", value: 60 },
  { label: "5m", value: 300 },
  { label: "15m", value: 900 },
  { label: "30m", value: 1800 },
  { label: "60m", value: 3600 },
];

const POLL_OPTIONS = [
  { label: "10s", value: 10 },
  { label: "30s", value: 30 },
  { label: "1m", value: 60 },
  { label: "5m", value: 300 },
  { label: "15m", value: 900 },
];

const LOOKAHEAD_OPTIONS = [
  { label: "15m", value: 15 },
  { label: "30m", value: 30 },
  { label: "60m", value: 60 },
  { label: "2h", value: 120 },
];

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return "never";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "--";
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

function timeUntil(dateStr: string | null): string {
  if (!dateStr) return "--";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "--";
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  if (diffMs <= 0) return "now";
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return "<1m";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

function formatTime(dateStr: string | null): string {
  if (!dateStr) return "";
  const date = new Date(dateStr);
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

const TRIAGE_EXPLANATIONS: Record<string, string> = {
  pending: "Awaiting triage",
  dismissed: "Not important enough to act on",
  noted: "Stored for context in next conversation",
  acted: "Edward responded or took action",
  escalated: "Urgent — Edward notified you",
  calendar_urgent: "Calendar event starting soon",
  mention: "@edward mention detected",
};

function formatFullTime(dateStr: string): string {
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return dateStr;
  return date.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

function EventDetail({ event }: { event: HeartbeatEvent }) {
  const raw = event.raw_data;

  const renderContent = () => {
    if (!raw) return null;

    if (event.source === "imessage" || event.source === "sms" || event.source === "whatsapp") {
      const text = raw.text as string | undefined;
      if (text) {
        return (
          <div>
            <span className="text-xs text-text-muted block mb-1">Message</span>
            <pre className="text-xs text-text-primary bg-primary-bg rounded p-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-words border border-input-border">
              {text}
            </pre>
          </div>
        );
      }
    }

    if (event.source === "email") {
      const subject = raw.subject as string | undefined;
      const body = raw.body as string | undefined;
      return (
        <div className="space-y-2">
          {subject && (
            <div>
              <span className="text-xs text-text-muted">Subject:</span>{" "}
              <span className="text-xs text-text-primary">{subject}</span>
            </div>
          )}
          {body && (
            <div>
              <span className="text-xs text-text-muted block mb-1">Body</span>
              <pre className="text-xs text-text-primary bg-primary-bg rounded p-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-words border border-input-border">
                {body}
              </pre>
            </div>
          )}
        </div>
      );
    }

    if (event.source === "calendar") {
      const start = raw.start_date as string | undefined;
      const end = raw.end_date as string | undefined;
      const allDay = raw.all_day as boolean | undefined;
      const title = raw.title as string | undefined;
      return (
        <div className="space-y-1">
          {title && (
            <div>
              <span className="text-xs text-text-muted">Event:</span>{" "}
              <span className="text-xs text-text-primary">{title}</span>
            </div>
          )}
          {start && (
            <div>
              <span className="text-xs text-text-muted">Start:</span>{" "}
              <span className="text-xs text-text-primary">{formatFullTime(start)}</span>
            </div>
          )}
          {end && (
            <div>
              <span className="text-xs text-text-muted">End:</span>{" "}
              <span className="text-xs text-text-primary">{formatFullTime(end)}</span>
            </div>
          )}
          {allDay && (
            <span className="inline-block text-xs px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 border border-blue-500/30">
              All day
            </span>
          )}
        </div>
      );
    }

    // Fallback: JSON dump
    return (
      <div>
        <span className="text-xs text-text-muted block mb-1">Raw data</span>
        <pre className="text-xs text-text-primary bg-primary-bg rounded p-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-words border border-input-border font-mono">
          {JSON.stringify(raw, null, 2)}
        </pre>
      </div>
    );
  };

  return (
    <div className="ml-0 mb-2 p-3 bg-surface rounded-lg border border-input-border space-y-2 text-xs">
      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        <div>
          <span className="text-text-muted">Time</span>
          <div className="text-text-primary">{formatFullTime(event.created_at)}</div>
        </div>
        <div>
          <span className="text-text-muted">Source / Type</span>
          <div className="text-text-primary">{event.source} / {event.event_type}</div>
        </div>
        <div>
          <span className="text-text-muted">Contact</span>
          <div className="text-text-primary">
            {event.contact_name || "Unknown"}
            {event.sender && event.sender !== event.contact_name && (
              <span className="text-text-muted ml-1 font-mono">({event.sender})</span>
            )}
          </div>
        </div>
        <div>
          <span className="text-text-muted">Triage</span>
          <div className="flex items-center gap-1.5">
            <span
              className={cn(
                "px-1.5 py-0.5 rounded text-xs font-mono border",
                TRIAGE_COLORS[event.triage_status] || TRIAGE_COLORS.pending,
              )}
            >
              {event.triage_status}
            </span>
            <span className="text-text-muted">
              {TRIAGE_EXPLANATIONS[event.triage_status] || ""}
            </span>
          </div>
        </div>
        <div>
          <span className="text-text-muted">Briefed</span>
          <div className="text-text-primary">{event.briefed ? "Yes" : "No"}</div>
        </div>
        {event.is_from_user && (
          <div>
            <span className="text-text-muted">Direction</span>
            <div className="text-text-primary">Sent by you</div>
          </div>
        )}
      </div>
      {renderContent()}
    </div>
  );
}

interface HeartbeatPanelProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function HeartbeatPanel({ isExpanded: initialExpanded = false, hideHeader = false }: HeartbeatPanelProps) {
  const [isExpanded, setIsExpanded] = useState(initialExpanded || hideHeader);
  const [status, setStatus] = useState<HeartbeatStatus | null>(null);
  const [events, setEvents] = useState<HeartbeatEvent[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  const [config, setConfig] = useState<HeartbeatConfig>({
    enabled: true,
    triage_interval_seconds: 900,
    digest_token_cap: 800,
    allowed_senders: [],
    imessage_enabled: true,
    imessage_poll_seconds: 10,
    calendar_enabled: false,
    calendar_poll_seconds: 300,
    calendar_lookahead_minutes: 30,
    email_enabled: false,
    email_poll_seconds: 300,
  });
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);
  const [recentSenders, setRecentSenders] = useState<AllowedSender[]>([]);
  const [showAddSender, setShowAddSender] = useState(false);
  const [manualIdentifier, setManualIdentifier] = useState("");
  const [manualLabel, setManualLabel] = useState("");
  const [sourceFilter, setSourceFilter] = useState<string | undefined>(undefined);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [lastTriageCycle, setLastTriageCycle] = useState<TriageCycle | null>(null);
  const [showTriageCycle, setShowTriageCycle] = useState(false);

  const PAGE_SIZE = 50;

  const loadData = useCallback(async (source?: string) => {
    setIsLoading(true);
    try {
      const [statusData, eventsData, triageCycles] = await Promise.all([
        getHeartbeatStatus(),
        getHeartbeatEvents(PAGE_SIZE, 0, undefined, source),
        getTriageCycles(1),
      ]);
      setStatus(statusData);
      setEvents(eventsData);
      setHasMore(eventsData.length >= PAGE_SIZE);
      setLastTriageCycle(triageCycles.length > 0 ? triageCycles[0] : null);
      setConfig({
        enabled: statusData.enabled,
        triage_interval_seconds: statusData.triage_interval_seconds,
        digest_token_cap: 800, // Not returned from status, keep current
        allowed_senders: statusData.allowed_senders || [],
        imessage_enabled: statusData.imessage_enabled ?? true,
        imessage_poll_seconds: statusData.imessage_poll_seconds ?? 10,
        calendar_enabled: statusData.calendar_enabled ?? false,
        calendar_poll_seconds: statusData.calendar_poll_seconds ?? 300,
        calendar_lookahead_minutes: statusData.calendar_lookahead_minutes ?? 30,
        email_enabled: statusData.email_enabled ?? false,
        email_poll_seconds: statusData.email_poll_seconds ?? 300,
      });
    } catch (err) {
      console.error("Failed to load heartbeat data:", err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadMore = useCallback(async () => {
    setIsLoadingMore(true);
    try {
      const moreEvents = await getHeartbeatEvents(PAGE_SIZE, events.length, undefined, sourceFilter);
      if (moreEvents.length < PAGE_SIZE) setHasMore(false);
      setEvents((prev) => {
        const existingIds = new Set(prev.map((e) => e.id));
        return [...prev, ...moreEvents.filter((e) => !existingIds.has(e.id))];
      });
    } catch (err) {
      console.error("Failed to load more events:", err);
    } finally {
      setIsLoadingMore(false);
    }
  }, [events.length, sourceFilter]);

  useEffect(() => {
    if (isExpanded) {
      loadData(sourceFilter);
    }
  }, [isExpanded, loadData, sourceFilter]);

  const handleToggleEnabled = async () => {
    const newEnabled = !config.enabled;
    try {
      await updateHeartbeatConfig({ enabled: newEnabled });
      setConfig((prev) => ({ ...prev, enabled: newEnabled }));
      // Refresh status after toggle
      const newStatus = await getHeartbeatStatus();
      setStatus(newStatus);
    } catch (err) {
      console.error("Failed to toggle heartbeat:", err);
    }
  };

  const handleIntervalChange = async (seconds: number) => {
    try {
      await updateHeartbeatConfig({ triage_interval_seconds: seconds });
      setConfig((prev) => ({ ...prev, triage_interval_seconds: seconds }));
    } catch (err) {
      console.error("Failed to update interval:", err);
    }
  };

  const handleAddSender = async (sender: AllowedSender) => {
    // Avoid duplicates
    if (config.allowed_senders.some((s) => s.identifier === sender.identifier)) return;
    const updated = [...config.allowed_senders, sender];
    try {
      await updateHeartbeatConfig({ allowed_senders: updated });
      setConfig((prev) => ({ ...prev, allowed_senders: updated }));
      setShowAddSender(false);
      setManualIdentifier("");
      setManualLabel("");
    } catch (err) {
      console.error("Failed to add allowed sender:", err);
    }
  };

  const handleRemoveSender = async (identifier: string) => {
    const updated = config.allowed_senders.filter((s) => s.identifier !== identifier);
    try {
      await updateHeartbeatConfig({ allowed_senders: updated });
      setConfig((prev) => ({ ...prev, allowed_senders: updated }));
    } catch (err) {
      console.error("Failed to remove allowed sender:", err);
    }
  };

  const handleOpenAddSender = async () => {
    setShowAddSender(true);
    try {
      const senders = await getRecentSenders(30);
      // Filter out already-allowed senders
      const filtered = senders.filter(
        (s) => !config.allowed_senders.some((a) => a.identifier === s.identifier),
      );
      setRecentSenders(filtered);
    } catch (err) {
      console.error("Failed to load recent senders:", err);
    }
  };

  const handleSourceFilter = (source: string | undefined) => {
    setSourceFilter(source);
    setEvents([]);
    setHasMore(true);
  };

  // Compute today's stats from events (filtered to today only)
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todayEvents = events.filter((e) => new Date(e.created_at) >= todayStart);
  const stats = {
    total: todayEvents.length,
    dismissed: todayEvents.filter((e) => e.triage_status === "dismissed").length,
    noted: todayEvents.filter((e) => e.triage_status === "noted").length,
    escalated: todayEvents.filter((e) => e.triage_status === "escalated" || e.triage_status === "acted").length,
  };

  const statusSummary = status
    ? status.running
      ? `listening -- last triage ${timeAgo(status.last_triage_at)}`
      : status.enabled
        ? "paused"
        : "disabled"
    : "";

  return (
    <div className={cn("border border-input-border rounded-lg overflow-hidden", !hideHeader && "mt-8")}>
      {!hideHeader && (
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full px-4 py-3 flex items-center justify-between bg-surface hover:bg-surface/80 transition-colors"
        >
          <div className="flex items-center gap-2 text-text-primary">
            <HeartPulse
              className={cn(
                "w-4 h-4",
                status?.running ? "text-terminal animate-pulse" : "text-text-muted",
              )}
            />
            <span className="font-medium text-sm">Heartbeat</span>
            {statusSummary && (
              <span className="text-xs text-text-muted">({statusSummary})</span>
            )}
          </div>
          {isExpanded ? (
            <ChevronDown className="w-4 h-4 text-text-muted" />
          ) : (
            <ChevronRight className="w-4 h-4 text-text-muted" />
          )}
        </button>
      )}

      {(isExpanded || hideHeader) && (
        <div className="p-4 space-y-4 bg-primary-bg border-t border-input-border">
          {/* 1. Pulse Status Bar */}
          <div className="flex items-center gap-3 p-3 bg-surface rounded-lg border border-input-border">
            {status?.running ? (
              <span className="relative flex h-3 w-3">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-terminal opacity-75" />
                <span className="relative inline-flex rounded-full h-3 w-3 bg-terminal" />
              </span>
            ) : status?.enabled ? (
              <span className="relative flex h-3 w-3">
                <span className="relative inline-flex rounded-full h-3 w-3 bg-yellow-400" />
              </span>
            ) : (
              <span className="relative flex h-3 w-3">
                <span className="relative inline-flex rounded-full h-3 w-3 bg-gray-500" />
              </span>
            )}
            <span className="text-sm text-text-primary">
              {status?.running ? "Listening" : status?.enabled ? "Paused" : "Disabled"}
            </span>
            <span className="text-xs text-text-muted">|</span>
            <span className="text-xs text-text-muted">
              Last triage: {timeAgo(status?.last_triage_at ?? null)}
            </span>
            {status?.next_triage_at && (
              <>
                <span className="text-xs text-text-muted">|</span>
                <span className="text-xs text-text-muted">
                  Next in {timeUntil(status.next_triage_at)}
                </span>
              </>
            )}
            <span className="text-xs text-text-muted">|</span>
            <span className="text-xs font-mono text-terminal">
              {status?.pending_count ?? 0} pending
            </span>
            <div className="flex-1" />
            <button
              onClick={() => loadData(sourceFilter)}
              disabled={isLoading}
              className="p-1 rounded text-text-muted hover:text-text-primary transition-colors"
            >
              <RefreshCw className={cn("w-3.5 h-3.5", isLoading && "animate-spin")} />
            </button>
          </div>

          {/* 2. Today's Stats */}
          <div>
            <h3 className="text-xs font-medium text-text-muted uppercase tracking-wide mb-2">Today</h3>
            {stats.total === 0 ? (
              <div className="text-center py-4 text-text-muted text-sm bg-surface rounded-lg border border-input-border">
                No events today yet
              </div>
            ) : (
              <div className="grid grid-cols-4 gap-3">
                <div className="p-3 bg-surface rounded-lg border border-input-border text-center">
                  <div className="text-2xl font-mono text-text-primary">{stats.total}</div>
                  <div className="text-xs text-text-muted mt-1">Events</div>
                </div>
                <div className="p-3 bg-surface rounded-lg border border-input-border text-center">
                  <div className="text-2xl font-mono text-text-muted">{stats.dismissed}</div>
                  <div className="text-xs text-text-muted mt-1">Dismissed</div>
                </div>
                <div className="p-3 bg-surface rounded-lg border border-input-border text-center">
                  <div className="text-2xl font-mono text-blue-400">{stats.noted}</div>
                  <div className="text-xs text-text-muted mt-1">Noted</div>
                </div>
                <div className="p-3 bg-surface rounded-lg border border-input-border text-center">
                  <div className="text-2xl font-mono text-red-400">{stats.escalated}</div>
                  <div className="text-xs text-text-muted mt-1">Escalated</div>
                </div>
              </div>
            )}
          </div>

          {/* 2.5 Last Triage Cycle */}
          {lastTriageCycle && (
            <div className="border border-input-border rounded-lg overflow-hidden">
              <button
                onClick={() => setShowTriageCycle(!showTriageCycle)}
                className="w-full px-3 py-2 flex items-center gap-2 bg-surface hover:bg-surface/80 transition-colors text-xs"
              >
                <span className="text-text-muted">Last triage cycle</span>
                <span className="text-text-muted font-mono">#{lastTriageCycle.cycle_number}</span>
                <span className="text-text-muted">·</span>
                <span className="text-text-muted">{timeAgo(lastTriageCycle.created_at)}</span>
                <div className="flex-1" />
                {showTriageCycle ? (
                  <ChevronDown className="w-3 h-3 text-text-muted" />
                ) : (
                  <ChevronRight className="w-3 h-3 text-text-muted" />
                )}
              </button>
              {showTriageCycle && (
                <div className="grid grid-cols-4 gap-2 p-3 bg-primary-bg border-t border-input-border">
                  <div className="p-2 bg-surface rounded border border-input-border text-center">
                    <div className="text-lg font-mono text-text-primary">{lastTriageCycle.events_total}</div>
                    <div className="text-[10px] text-text-muted">Processed</div>
                  </div>
                  <div className="p-2 bg-surface rounded border border-input-border text-center">
                    <div className="text-lg font-mono text-text-primary">L{lastTriageCycle.layer_reached}</div>
                    <div className="text-[10px] text-text-muted">Layer reached</div>
                  </div>
                  <div className="p-2 bg-surface rounded border border-input-border text-center">
                    <div className="text-lg font-mono text-text-primary">{lastTriageCycle.duration_ms}</div>
                    <div className="text-[10px] text-text-muted">ms</div>
                  </div>
                  <div className="p-2 bg-surface rounded border border-input-border text-center">
                    <div className="text-lg font-mono text-text-primary">{lastTriageCycle.haiku_input_tokens + lastTriageCycle.haiku_output_tokens}</div>
                    <div className="text-[10px] text-text-muted">Tokens</div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* 3. Source Filter Pills */}
          <div className="flex gap-1.5">
            {([
              { label: "All", value: undefined },
              { label: "Messages", value: "imessage" },
              { label: "Calendar", value: "calendar" },
              { label: "Email", value: "email" },
            ] as { label: string; value: string | undefined }[]).map((opt) => (
              <button
                key={opt.label}
                onClick={() => handleSourceFilter(opt.value)}
                className={cn(
                  "px-3 py-1.5 rounded text-xs font-medium border transition-colors",
                  sourceFilter === opt.value
                    ? "bg-terminal/20 text-terminal border-terminal/30"
                    : "bg-surface text-text-muted border-input-border hover:border-text-muted",
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* 4. Recent Activity Timeline */}
          <div>
            <h3 className="text-xs font-medium text-text-muted uppercase tracking-wide mb-3">
              Recent Activity
            </h3>
            {events.length === 0 ? (
              <div className="text-center py-6 text-text-muted text-sm">
                No events yet -- waiting for messages
              </div>
            ) : (
              <>
                <div className="space-y-0">
                  {events.map((event, i) => {
                    const isOpen = expandedEventId === event.id;
                    return (
                      <div key={event.id} className="relative pl-8">
                        {/* Connector line */}
                        {i < events.length - 1 && (
                          <div className="absolute left-[7px] top-6 bottom-0 w-px bg-input-border" />
                        )}
                        {/* Dot */}
                        <div
                          className={cn(
                            "absolute left-0 top-3 w-[15px] h-[15px] rounded-full border-2",
                            DOT_COLORS[event.triage_status] || DOT_COLORS.pending,
                          )}
                        />
                        {/* Clickable row */}
                        <button
                          onClick={() => setExpandedEventId(isOpen ? null : event.id)}
                          className="w-full text-left py-2 flex items-center gap-2 hover:bg-surface/50 rounded transition-colors"
                        >
                          <span className="text-xs text-text-muted font-mono w-14 shrink-0">
                            {formatTime(event.created_at)}
                          </span>
                          {event.source === "calendar" ? (
                            <Calendar className="w-3 h-3 text-text-muted shrink-0" />
                          ) : event.source === "email" ? (
                            <Mail className="w-3 h-3 text-text-muted shrink-0" />
                          ) : (
                            <MessageCircle className="w-3 h-3 text-text-muted shrink-0" />
                          )}
                          <span className="text-sm text-text-primary truncate max-w-[120px]">
                            {event.contact_name || event.sender || "Unknown"}
                          </span>
                          <span className="text-sm text-text-muted truncate flex-1">
                            {event.is_from_user ? "(you) " : ""}
                            {event.summary || ""}
                          </span>
                          <span
                            className={cn(
                              "px-2 py-0.5 rounded text-xs font-mono border shrink-0",
                              TRIAGE_COLORS[event.triage_status] || TRIAGE_COLORS.pending,
                            )}
                          >
                            {event.triage_status}
                          </span>
                          {isOpen ? (
                            <ChevronUp className="w-3 h-3 text-text-muted shrink-0" />
                          ) : (
                            <ChevronDown className="w-3 h-3 text-text-muted shrink-0" />
                          )}
                        </button>
                        {/* Detail panel */}
                        {isOpen && <EventDetail event={event} />}
                      </div>
                    );
                  })}
                </div>
                {hasMore && (
                  <button
                    onClick={loadMore}
                    disabled={isLoadingMore}
                    className="w-full mt-3 py-2 rounded text-xs text-text-muted hover:text-text-primary border border-input-border hover:border-text-muted bg-surface transition-colors flex items-center justify-center gap-2"
                  >
                    {isLoadingMore ? (
                      <>
                        <Loader2 className="w-3 h-3 animate-spin" />
                        Loading...
                      </>
                    ) : (
                      "Load more"
                    )}
                  </button>
                )}
              </>
            )}
          </div>

          {/* 4. Configuration */}
          <div className="border-t border-input-border pt-4">
            <button
              onClick={() => setShowConfig(!showConfig)}
              className="flex items-center gap-2 text-text-muted hover:text-text-primary transition-colors text-sm"
            >
              <Settings2 className="w-3.5 h-3.5" />
              <span>Configuration</span>
              {showConfig ? (
                <ChevronDown className="w-3.5 h-3.5" />
              ) : (
                <ChevronRight className="w-3.5 h-3.5" />
              )}
            </button>

            {showConfig && (
              <div className="mt-3 space-y-4">
                {/* Enable/Disable toggle */}
                <div className="flex items-center justify-between">
                  <span className="text-sm text-text-primary">Heartbeat enabled</span>
                  <button
                    onClick={handleToggleEnabled}
                    className={cn(
                      "relative inline-flex h-6 w-11 items-center rounded-full transition-colors",
                      config.enabled ? "bg-terminal" : "bg-gray-600",
                    )}
                  >
                    <span
                      className={cn(
                        "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
                        config.enabled ? "translate-x-6" : "translate-x-1",
                      )}
                    />
                  </button>
                </div>

                {/* Triage interval */}
                <div>
                  <span className="text-sm text-text-primary block mb-2">
                    Triage interval
                  </span>
                  <div className="flex gap-1">
                    {INTERVAL_OPTIONS.map((opt) => (
                      <button
                        key={opt.value}
                        onClick={() => handleIntervalChange(opt.value)}
                        className={cn(
                          "px-3 py-1.5 rounded text-xs font-mono border transition-colors",
                          config.triage_interval_seconds === opt.value
                            ? "bg-terminal/20 text-terminal border-terminal/30"
                            : "bg-surface text-text-muted border-input-border hover:border-text-muted",
                        )}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Per-Track Configuration */}
                <div className="space-y-3">
                  <span className="text-xs font-medium text-text-muted uppercase tracking-wide">
                    Listener Tracks
                  </span>

                  {/* iMessage Track */}
                  <div className="p-3 bg-surface rounded-lg border border-input-border space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <MessageCircle className="w-3.5 h-3.5 text-text-muted" />
                        <span className="text-sm text-text-primary">iMessage</span>
                        <span
                          className={cn(
                            "w-2 h-2 rounded-full",
                            status?.tracks?.imessage?.status === "running"
                              ? "bg-terminal"
                              : status?.tracks?.imessage?.status === "error"
                                ? "bg-red-400"
                                : "bg-gray-500",
                          )}
                        />
                      </div>
                      <button
                        onClick={async () => {
                          const v = !config.imessage_enabled;
                          try {
                            await updateHeartbeatConfig({ imessage_enabled: v });
                            setConfig((prev) => ({ ...prev, imessage_enabled: v }));
                          } catch (err) {
                            console.error("Failed to toggle iMessage:", err);
                          }
                        }}
                        className={cn(
                          "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
                          config.imessage_enabled ? "bg-terminal" : "bg-gray-600",
                        )}
                      >
                        <span
                          className={cn(
                            "inline-block h-3 w-3 transform rounded-full bg-white transition-transform",
                            config.imessage_enabled ? "translate-x-5" : "translate-x-1",
                          )}
                        />
                      </button>
                    </div>
                    {config.imessage_enabled && (
                      <div>
                        <span className="text-xs text-text-muted block mb-1">Poll interval</span>
                        <div className="flex gap-1">
                          {POLL_OPTIONS.map((opt) => (
                            <button
                              key={opt.value}
                              onClick={async () => {
                                try {
                                  await updateHeartbeatConfig({ imessage_poll_seconds: opt.value });
                                  setConfig((prev) => ({ ...prev, imessage_poll_seconds: opt.value }));
                                } catch (err) {
                                  console.error("Failed to update iMessage poll:", err);
                                }
                              }}
                              className={cn(
                                "px-2 py-1 rounded text-xs font-mono border transition-colors",
                                config.imessage_poll_seconds === opt.value
                                  ? "bg-terminal/20 text-terminal border-terminal/30"
                                  : "bg-primary-bg text-text-muted border-input-border hover:border-text-muted",
                              )}
                            >
                              {opt.label}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Calendar Track */}
                  <div className="p-3 bg-surface rounded-lg border border-input-border space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Calendar className="w-3.5 h-3.5 text-text-muted" />
                        <span className="text-sm text-text-primary">Calendar</span>
                        <span
                          className={cn(
                            "w-2 h-2 rounded-full",
                            status?.tracks?.calendar?.status === "running"
                              ? "bg-terminal"
                              : status?.tracks?.calendar?.status === "error"
                                ? "bg-red-400"
                                : "bg-gray-500",
                          )}
                        />
                      </div>
                      <button
                        onClick={async () => {
                          const v = !config.calendar_enabled;
                          try {
                            await updateHeartbeatConfig({ calendar_enabled: v });
                            setConfig((prev) => ({ ...prev, calendar_enabled: v }));
                          } catch (err) {
                            console.error("Failed to toggle calendar:", err);
                          }
                        }}
                        className={cn(
                          "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
                          config.calendar_enabled ? "bg-terminal" : "bg-gray-600",
                        )}
                      >
                        <span
                          className={cn(
                            "inline-block h-3 w-3 transform rounded-full bg-white transition-transform",
                            config.calendar_enabled ? "translate-x-5" : "translate-x-1",
                          )}
                        />
                      </button>
                    </div>
                    {config.calendar_enabled && (
                      <div className="space-y-2">
                        <div>
                          <span className="text-xs text-text-muted block mb-1">Poll interval</span>
                          <div className="flex gap-1">
                            {POLL_OPTIONS.map((opt) => (
                              <button
                                key={opt.value}
                                onClick={async () => {
                                  try {
                                    await updateHeartbeatConfig({ calendar_poll_seconds: opt.value });
                                    setConfig((prev) => ({ ...prev, calendar_poll_seconds: opt.value }));
                                  } catch (err) {
                                    console.error("Failed to update calendar poll:", err);
                                  }
                                }}
                                className={cn(
                                  "px-2 py-1 rounded text-xs font-mono border transition-colors",
                                  config.calendar_poll_seconds === opt.value
                                    ? "bg-terminal/20 text-terminal border-terminal/30"
                                    : "bg-primary-bg text-text-muted border-input-border hover:border-text-muted",
                                )}
                              >
                                {opt.label}
                              </button>
                            ))}
                          </div>
                        </div>
                        <div>
                          <span className="text-xs text-text-muted block mb-1">Lookahead window</span>
                          <div className="flex gap-1">
                            {LOOKAHEAD_OPTIONS.map((opt) => (
                              <button
                                key={opt.value}
                                onClick={async () => {
                                  try {
                                    await updateHeartbeatConfig({ calendar_lookahead_minutes: opt.value });
                                    setConfig((prev) => ({ ...prev, calendar_lookahead_minutes: opt.value }));
                                  } catch (err) {
                                    console.error("Failed to update lookahead:", err);
                                  }
                                }}
                                className={cn(
                                  "px-2 py-1 rounded text-xs font-mono border transition-colors",
                                  config.calendar_lookahead_minutes === opt.value
                                    ? "bg-terminal/20 text-terminal border-terminal/30"
                                    : "bg-primary-bg text-text-muted border-input-border hover:border-text-muted",
                                )}
                              >
                                {opt.label}
                              </button>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Email Track */}
                  <div className="p-3 bg-surface rounded-lg border border-input-border space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Mail className="w-3.5 h-3.5 text-text-muted" />
                        <span className="text-sm text-text-primary">Email</span>
                        <span
                          className={cn(
                            "w-2 h-2 rounded-full",
                            status?.tracks?.email?.status === "running"
                              ? "bg-terminal"
                              : status?.tracks?.email?.status === "error"
                                ? "bg-red-400"
                                : "bg-gray-500",
                          )}
                        />
                      </div>
                      <button
                        onClick={async () => {
                          const v = !config.email_enabled;
                          try {
                            await updateHeartbeatConfig({ email_enabled: v });
                            setConfig((prev) => ({ ...prev, email_enabled: v }));
                          } catch (err) {
                            console.error("Failed to toggle email:", err);
                          }
                        }}
                        className={cn(
                          "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
                          config.email_enabled ? "bg-terminal" : "bg-gray-600",
                        )}
                      >
                        <span
                          className={cn(
                            "inline-block h-3 w-3 transform rounded-full bg-white transition-transform",
                            config.email_enabled ? "translate-x-5" : "translate-x-1",
                          )}
                        />
                      </button>
                    </div>
                    {config.email_enabled && (
                      <div>
                        <span className="text-xs text-text-muted block mb-1">Poll interval</span>
                        <div className="flex gap-1">
                          {POLL_OPTIONS.map((opt) => (
                            <button
                              key={opt.value}
                              onClick={async () => {
                                try {
                                  await updateHeartbeatConfig({ email_poll_seconds: opt.value });
                                  setConfig((prev) => ({ ...prev, email_poll_seconds: opt.value }));
                                } catch (err) {
                                  console.error("Failed to update email poll:", err);
                                }
                              }}
                              className={cn(
                                "px-2 py-1 rounded text-xs font-mono border transition-colors",
                                config.email_poll_seconds === opt.value
                                  ? "bg-terminal/20 text-terminal border-terminal/30"
                                  : "bg-primary-bg text-text-muted border-input-border hover:border-text-muted",
                              )}
                            >
                              {opt.label}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                {/* Blocked @Edward Contacts */}
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <Shield className="w-3.5 h-3.5 text-text-muted" />
                    <span className="text-sm text-text-primary">
                      Blocked @Edward contacts
                    </span>
                  </div>

                  {config.allowed_senders.length === 0 && !showAddSender ? (
                    <p className="text-xs text-text-muted mb-2">
                      No contacts blocked — anyone can trigger Edward via @mention
                    </p>
                  ) : (
                    <div className="flex flex-wrap gap-1.5 mb-2">
                      {config.allowed_senders.map((sender) => (
                        <div
                          key={sender.identifier}
                          className="flex items-center gap-1.5 px-2 py-1 rounded bg-surface border border-input-border text-xs"
                        >
                          <span className="text-text-primary">{sender.label}</span>
                          <span className="text-text-muted font-mono text-[10px]">
                            {sender.identifier !== sender.label ? sender.identifier : ""}
                          </span>
                          <button
                            onClick={() => handleRemoveSender(sender.identifier)}
                            className="text-text-muted hover:text-red-400 transition-colors ml-0.5"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  {showAddSender ? (
                    <div className="space-y-2 p-2 bg-surface rounded border border-input-border">
                      {recentSenders.length > 0 && (
                        <div>
                          <span className="text-xs text-text-muted block mb-1">
                            Recent senders
                          </span>
                          <div className="flex flex-wrap gap-1">
                            {recentSenders.map((sender) => (
                              <button
                                key={sender.identifier}
                                onClick={() => handleAddSender(sender)}
                                className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-primary-bg border border-input-border text-text-muted hover:text-text-primary hover:border-text-muted transition-colors"
                              >
                                <Plus className="w-2.5 h-2.5" />
                                <span>{sender.label}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                      <div>
                        <span className="text-xs text-text-muted block mb-1">
                          Or add manually
                        </span>
                        <div className="flex gap-1.5 items-center">
                          <input
                            type="text"
                            value={manualIdentifier}
                            onChange={(e) => setManualIdentifier(e.target.value)}
                            placeholder="Phone or email"
                            className="flex-1 px-2 py-1 rounded text-xs bg-primary-bg border border-input-border text-text-primary placeholder-text-muted font-mono focus:outline-none focus:border-terminal"
                          />
                          <input
                            type="text"
                            value={manualLabel}
                            onChange={(e) => setManualLabel(e.target.value)}
                            placeholder="Name"
                            className="w-24 px-2 py-1 rounded text-xs bg-primary-bg border border-input-border text-text-primary placeholder-text-muted focus:outline-none focus:border-terminal"
                          />
                          <button
                            onClick={() => {
                              if (manualIdentifier.trim()) {
                                handleAddSender({
                                  identifier: manualIdentifier.trim(),
                                  label: manualLabel.trim() || manualIdentifier.trim(),
                                });
                              }
                            }}
                            disabled={!manualIdentifier.trim()}
                            className="px-2 py-1 rounded text-xs bg-terminal/20 text-terminal border border-terminal/30 hover:bg-terminal/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            Add
                          </button>
                          <button
                            onClick={() => {
                              setShowAddSender(false);
                              setManualIdentifier("");
                              setManualLabel("");
                            }}
                            className="px-2 py-1 rounded text-xs text-text-muted hover:text-text-primary transition-colors"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <button
                      onClick={handleOpenAddSender}
                      className="flex items-center gap-1 px-2 py-1 rounded text-xs text-text-muted hover:text-text-primary border border-input-border hover:border-text-muted transition-colors"
                    >
                      <UserPlus className="w-3 h-3" />
                      <span>Block a contact</span>
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
