"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ChevronDown,
  ChevronRight,
  Calendar,
  Trash2,
  RefreshCw,
  AlertCircle,
  X,
  XCircle,
  ArrowLeft,
  ArrowUp,
  ArrowDown,
  Search,
} from "lucide-react";
import {
  listEvents,
  cancelEvent,
  deleteEvent,
  ScheduledEventItem,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-green-500/20 text-green-400 border-green-500/30",
  processing: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  completed: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  cancelled: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  failed: "bg-red-500/20 text-red-400 border-red-500/30",
};

const CHANNEL_COLORS: Record<string, string> = {
  chat: "bg-purple-500/20 text-purple-400",
  sms: "bg-cyan-500/20 text-cyan-400",
  whatsapp: "bg-green-500/20 text-green-400",
  imessage: "bg-blue-500/20 text-blue-400",
};

interface EventsBrowserProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function EventsBrowser({ isExpanded: initialExpanded = false, hideHeader = false }: EventsBrowserProps) {
  const [isExpanded, setIsExpanded] = useState(initialExpanded);
  const [events, setEvents] = useState<ScheduledEventItem[]>([]);
  const [pendingCount, setPendingCount] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [statusFilter, setStatusFilter] = useState<string>("pending");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");
  const [searchQuery, setSearchQuery] = useState("");

  // Detail view
  const [viewingEvent, setViewingEvent] = useState<ScheduledEventItem | null>(null);

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<ScheduledEventItem | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const loadEvents = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await listEvents(
        statusFilter === "all" ? undefined : statusFilter,
        sortOrder,
        searchQuery || undefined,
      );
      setEvents(result);
      // Also get pending count for header
      if (statusFilter === "pending" && !searchQuery) {
        setPendingCount(result.length);
      } else {
        const pending = await listEvents("pending");
        setPendingCount(pending.length);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load events");
    } finally {
      setIsLoading(false);
    }
  }, [statusFilter, sortOrder, searchQuery]);

  useEffect(() => {
    if (isExpanded) {
      loadEvents();
    }
  }, [isExpanded, loadEvents]);

  const handleCancel = async (event: ScheduledEventItem) => {
    try {
      await cancelEvent(event.id);
      loadEvents();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel event");
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await deleteEvent(deleteTarget.id);
      setDeleteTarget(null);
      if (viewingEvent?.id === deleteTarget.id) setViewingEvent(null);
      loadEvents();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete event");
    } finally {
      setIsDeleting(false);
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "N/A";
    const date = new Date(dateStr);
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  };

  return (
    <div className={cn("border border-input-border rounded-lg overflow-hidden", !hideHeader && "mt-8")}>
      {!hideHeader && (
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full px-4 py-3 flex items-center justify-between bg-surface hover:bg-surface/80 transition-colors"
        >
          <div className="flex items-center gap-2 text-text-primary">
            <Calendar className="w-4 h-4 text-terminal" />
            <span className="font-medium text-sm">Scheduled Events</span>
            {pendingCount !== null && (
              <span className="text-xs text-text-muted">({pendingCount} pending)</span>
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
          {/* Detail view */}
          {viewingEvent ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setViewingEvent(null)}
                  className="p-1 text-text-muted hover:text-text-primary transition-colors"
                >
                  <ArrowLeft className="w-4 h-4" />
                </button>
                <h3 className="text-sm font-medium text-text-primary flex-1 truncate">
                  Event Details
                </h3>
                <button
                  onClick={() => setDeleteTarget(viewingEvent)}
                  className="p-2 text-text-muted hover:text-red-500 transition-colors rounded-lg hover:bg-red-500/10"
                  title="Delete event"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
              <div className="p-4 bg-surface rounded-lg border border-input-border space-y-3">
                <p className="text-sm text-text-primary">{viewingEvent.description}</p>
                <div className="grid grid-cols-2 gap-3 text-xs text-text-muted">
                  <div>
                    <span className="text-text-muted/60">Status:</span>{" "}
                    <span className={cn("px-2 py-0.5 rounded text-xs font-mono border", STATUS_COLORS[viewingEvent.status] || STATUS_COLORS.pending)}>
                      {viewingEvent.status}
                    </span>
                  </div>
                  <div>
                    <span className="text-text-muted/60">Scheduled:</span> {formatDate(viewingEvent.scheduled_at)}
                  </div>
                  <div>
                    <span className="text-text-muted/60">Next fire:</span> {formatDate(viewingEvent.next_fire_at)}
                  </div>
                  <div>
                    <span className="text-text-muted/60">Created:</span> {formatDate(viewingEvent.created_at)}
                  </div>
                  {viewingEvent.recurrence_pattern && (
                    <div>
                      <span className="text-text-muted/60">Recurrence:</span>{" "}
                      <span className="font-mono">{viewingEvent.recurrence_pattern}</span>
                    </div>
                  )}
                  {viewingEvent.delivery_channel && (
                    <div>
                      <span className="text-text-muted/60">Channel:</span> {viewingEvent.delivery_channel}
                    </div>
                  )}
                  <div>
                    <span className="text-text-muted/60">Fire count:</span> {viewingEvent.fire_count}
                  </div>
                  {viewingEvent.last_fired_at && (
                    <div>
                      <span className="text-text-muted/60">Last fired:</span> {formatDate(viewingEvent.last_fired_at)}
                    </div>
                  )}
                  {viewingEvent.conversation_id && (
                    <div className="col-span-2">
                      <span className="text-text-muted/60">Conversation:</span>{" "}
                      <span className="font-mono text-xs">{viewingEvent.conversation_id}</span>
                    </div>
                  )}
                </div>
                {viewingEvent.last_result && (
                  <div className="pt-3 border-t border-input-border">
                    <p className="text-xs text-text-muted/60 mb-1">Last result:</p>
                    <pre className="text-xs text-text-muted font-mono whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                      {viewingEvent.last_result}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <>
              {/* Filters */}
              <div className="flex gap-3 items-center">
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="px-3 py-2 rounded-lg border border-input-border bg-input-bg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-terminal"
                >
                  <option value="all">All Statuses</option>
                  <option value="pending">Pending</option>
                  <option value="completed">Completed</option>
                  <option value="cancelled">Cancelled</option>
                  <option value="failed">Failed</option>
                </select>
                <div className="flex-1" />
                <button
                  onClick={() => setSortOrder(sortOrder === "asc" ? "desc" : "asc")}
                  className="p-2 rounded-lg border border-input-border bg-input-bg text-text-muted hover:text-text-primary transition-colors"
                  title={sortOrder === "asc" ? "Oldest first" : "Newest first"}
                >
                  {sortOrder === "asc" ? (
                    <ArrowUp className="w-4 h-4" />
                  ) : (
                    <ArrowDown className="w-4 h-4" />
                  )}
                </button>
                <button
                  onClick={loadEvents}
                  disabled={isLoading}
                  className="p-2 rounded-lg border border-input-border bg-input-bg text-text-muted hover:text-text-primary transition-colors disabled:opacity-50"
                  title="Refresh"
                >
                  <RefreshCw className={cn("w-4 h-4", isLoading && "animate-spin")} />
                </button>
              </div>

              {/* Search */}
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  loadEvents();
                }}
                className="flex gap-2"
              >
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted pointer-events-none" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search events..."
                    className="w-full pl-9 pr-3 py-2 rounded-lg border border-input-border bg-input-bg text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-terminal"
                  />
                </div>
                {searchQuery && (
                  <button
                    type="button"
                    onClick={() => setSearchQuery("")}
                    className="p-2 rounded-lg border border-input-border bg-input-bg text-text-muted hover:text-text-primary transition-colors"
                    title="Clear search"
                  >
                    <X className="w-4 h-4" />
                  </button>
                )}
              </form>

              {error && (
                <div className="flex items-center gap-2 text-red-500 text-sm p-3 bg-red-500/10 rounded-lg">
                  <AlertCircle className="w-4 h-4" />
                  {error}
                </div>
              )}

              {/* Event list */}
              <div className="space-y-2">
                {isLoading && events.length === 0 ? (
                  <div className="text-center py-8 text-text-muted">Loading events...</div>
                ) : events.length === 0 ? (
                  <div className="text-center py-8 text-text-muted">
                    {statusFilter !== "all"
                      ? `No ${statusFilter} events`
                      : "No scheduled events"}
                  </div>
                ) : (
                  events.map((event) => (
                    <div
                      key={event.id}
                      onClick={() => setViewingEvent(event)}
                      className="p-4 bg-surface rounded-lg border border-input-border hover:border-terminal/30 transition-colors cursor-pointer"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1 flex-wrap">
                            <span className={cn("px-2 py-0.5 rounded text-xs font-mono border", STATUS_COLORS[event.status] || STATUS_COLORS.pending)}>
                              {event.status}
                            </span>
                            {event.delivery_channel && (
                              <span className={cn("px-2 py-0.5 rounded text-xs font-mono", CHANNEL_COLORS[event.delivery_channel] || "bg-gray-500/20 text-gray-400")}>
                                {event.delivery_channel}
                              </span>
                            )}
                            {event.recurrence_pattern && (
                              <span className="px-2 py-0.5 rounded text-xs font-mono bg-orange-500/20 text-orange-400">
                                recurring ({event.fire_count}x fired)
                              </span>
                            )}
                          </div>
                          <p className="text-sm text-text-primary mt-1 line-clamp-2">
                            {event.description}
                          </p>
                          <div className="mt-2 flex gap-3 text-xs text-text-muted">
                            <span>Scheduled: {formatDate(event.scheduled_at)}</span>
                            {event.next_fire_at && event.status === "pending" && (
                              <span>Next: {formatDate(event.next_fire_at)}</span>
                            )}
                          </div>
                        </div>
                        <div className="flex gap-1 shrink-0">
                          {event.status === "pending" && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handleCancel(event);
                              }}
                              className="p-2 text-text-muted hover:text-yellow-500 transition-colors rounded-lg hover:bg-yellow-500/10"
                              title="Cancel event"
                            >
                              <XCircle className="w-4 h-4" />
                            </button>
                          )}
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setDeleteTarget(event);
                            }}
                            className="p-2 text-text-muted hover:text-red-500 transition-colors rounded-lg hover:bg-red-500/10"
                            title="Delete event"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* Delete confirmation modal */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-primary-bg border border-input-border rounded-lg p-6 max-w-md mx-4 shadow-xl">
            <div className="flex items-start justify-between mb-4">
              <h3 className="text-lg font-medium text-text-primary">Delete Event</h3>
              <button
                onClick={() => setDeleteTarget(null)}
                className="p-1 text-text-muted hover:text-text-primary transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <p className="text-sm text-text-muted mb-4">
              Are you sure you want to permanently delete this event? This action cannot be undone.
            </p>
            <div className="p-3 bg-surface rounded-lg mb-4">
              <p className="text-sm text-text-primary">{deleteTarget.description}</p>
              <p className="text-xs text-text-muted mt-1">
                Scheduled: {formatDate(deleteTarget.scheduled_at)}
              </p>
            </div>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setDeleteTarget(null)}
                className="px-4 py-2 rounded-lg border border-input-border text-sm text-text-primary hover:bg-surface transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={isDeleting}
                className="px-4 py-2 rounded-lg bg-red-500 text-white text-sm hover:bg-red-600 disabled:opacity-50 transition-colors"
              >
                {isDeleting ? "Deleting..." : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
