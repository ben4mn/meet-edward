"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ChevronDown,
  ChevronRight,
  Dna,
  RefreshCw,
  Settings2,
  Play,
  RotateCcw,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  FileCode2,
  AlertTriangle,
} from "lucide-react";
import {
  getEvolutionStatus,
  getEvolutionHistory,
  updateEvolutionConfig,
  triggerEvolution,
  rollbackEvolution,
  EvolutionStatus,
  EvolutionCycle,
  EvolutionConfig,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  branching: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  coding: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  validating: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  testing: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  reviewing: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  deploying: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  completed: "bg-green-500/20 text-green-400 border-green-500/30",
  failed: "bg-red-500/20 text-red-400 border-red-500/30",
  rolled_back: "bg-orange-500/20 text-orange-400 border-orange-500/30",
};

const DOT_COLORS: Record<string, string> = {
  pending: "border-yellow-400 bg-yellow-400/30",
  branching: "border-blue-400 bg-blue-400/30",
  coding: "border-blue-400 bg-blue-400/30",
  validating: "border-blue-400 bg-blue-400/30",
  testing: "border-blue-400 bg-blue-400/30",
  reviewing: "border-blue-400 bg-blue-400/30",
  deploying: "border-purple-400 bg-purple-400/30",
  completed: "border-green-400 bg-green-400/30",
  failed: "border-red-400 bg-red-400/30",
  rolled_back: "border-orange-400 bg-orange-400/30",
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

interface EvolutionPanelProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function EvolutionPanel({ isExpanded: initialExpanded = false, hideHeader = false }: EvolutionPanelProps) {
  const [isExpanded, setIsExpanded] = useState(initialExpanded || hideHeader);
  const [status, setStatus] = useState<EvolutionStatus | null>(null);
  const [history, setHistory] = useState<EvolutionCycle[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  const [showTrigger, setShowTrigger] = useState(false);
  const [triggerDesc, setTriggerDesc] = useState("");
  const [isTriggering, setIsTriggering] = useState(false);
  const [expandedCycle, setExpandedCycle] = useState<string | null>(null);
  const [config, setConfig] = useState<EvolutionConfig>({
    enabled: false,
    min_interval_seconds: 3600,
    auto_trigger: false,
    require_tests: true,
    max_files_per_cycle: 20,
  });

  const loadData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [statusData, historyData] = await Promise.all([
        getEvolutionStatus(),
        getEvolutionHistory(20),
      ]);
      setStatus(statusData);
      setHistory(historyData);
      setConfig(statusData.config);
    } catch (err) {
      console.error("Failed to load evolution data:", err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isExpanded) loadData();
  }, [isExpanded, loadData]);

  // Auto-refresh when a cycle is active
  useEffect(() => {
    if (!isExpanded || !status?.current_cycle) return;
    const interval = setInterval(loadData, 3000);
    return () => clearInterval(interval);
  }, [isExpanded, status?.current_cycle, loadData]);

  const handleConfigChange = async (key: keyof EvolutionConfig, value: boolean | number) => {
    try {
      const updated = await updateEvolutionConfig({ [key]: value });
      setConfig(updated);
    } catch (err) {
      console.error("Failed to update config:", err);
    }
  };

  const handleTrigger = async () => {
    if (!triggerDesc.trim()) return;
    setIsTriggering(true);
    try {
      await triggerEvolution(triggerDesc.trim());
      setTriggerDesc("");
      setShowTrigger(false);
      // Refresh to see the new cycle
      setTimeout(loadData, 1000);
    } catch (err) {
      console.error("Failed to trigger evolution:", err);
      alert(err instanceof Error ? err.message : "Failed to trigger evolution");
    } finally {
      setIsTriggering(false);
    }
  };

  const handleRollback = async (cycleId: string) => {
    if (!confirm("Are you sure you want to rollback this evolution cycle? The server will restart.")) return;
    try {
      await rollbackEvolution(cycleId);
      loadData();
    } catch (err) {
      console.error("Failed to rollback:", err);
      alert(err instanceof Error ? err.message : "Rollback failed");
    }
  };

  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03]">
      {/* Header */}
      {!hideHeader && (
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full flex items-center gap-3 px-4 py-3 hover:bg-white/5 transition-colors"
        >
          {isExpanded ? (
            <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
          ) : (
            <ChevronRight className="w-4 h-4 text-gray-400 flex-shrink-0" />
          )}
          <Dna className="w-5 h-5 text-purple-400 flex-shrink-0" />
          <div className="flex-1 text-left">
            <span className="text-sm font-medium text-gray-200">Self-Evolution</span>
            <span className="ml-2 text-xs text-gray-500">
              {config.enabled ? "Enabled" : "Disabled"}
            </span>
          </div>
          {status?.current_cycle && (
            <span className="px-2 py-0.5 rounded text-xs bg-blue-500/20 text-blue-400 border border-blue-500/30">
              Active
            </span>
          )}
        </button>
      )}

      {/* Content */}
      {isExpanded && (
        <div className={cn("space-y-4", hideHeader ? "p-0" : "px-4 pb-4")}>
          {/* Status bar */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div
                className={cn(
                  "w-2 h-2 rounded-full",
                  config.enabled ? "bg-green-400 animate-pulse" : "bg-gray-500"
                )}
              />
              <span className="text-xs text-gray-400">
                {config.enabled ? "Evolution enabled" : "Evolution disabled"}
              </span>
              {status?.last_cycle_at && (
                <span className="text-xs text-gray-500">
                  Last cycle: {timeAgo(status.last_cycle_at)}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowConfig(!showConfig)}
                className="p-1 rounded hover:bg-white/10 text-gray-400 hover:text-gray-200 transition-colors"
                title="Configuration"
              >
                <Settings2 className="w-4 h-4" />
              </button>
              <button
                onClick={loadData}
                disabled={isLoading}
                className="p-1 rounded hover:bg-white/10 text-gray-400 hover:text-gray-200 transition-colors"
                title="Refresh"
              >
                <RefreshCw className={cn("w-4 h-4", isLoading && "animate-spin")} />
              </button>
            </div>
          </div>

          {/* Active Cycle */}
          {status?.current_cycle && (
            <div className="rounded-lg border border-blue-500/20 bg-blue-500/5 p-3 space-y-2">
              <div className="flex items-center gap-2">
                <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />
                <span className="text-sm font-medium text-blue-300">Active Cycle</span>
                <span className={cn(
                  "px-2 py-0.5 rounded text-xs border",
                  STATUS_COLORS[status.current_cycle.status] || STATUS_COLORS.pending,
                )}>
                  {status.current_cycle.status}
                </span>
              </div>
              <p className="text-xs text-gray-400">{status.current_cycle.description}</p>
              {status.current_cycle.step && (
                <p className="text-xs text-gray-500">Step: {status.current_cycle.step}</p>
              )}
            </div>
          )}

          {/* Config Section */}
          {showConfig && (
            <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3 space-y-3">
              <h4 className="text-xs font-medium text-gray-300 uppercase tracking-wider">Configuration</h4>

              <label className="flex items-center justify-between">
                <span className="text-sm text-gray-300">Enabled</span>
                <button
                  onClick={() => handleConfigChange("enabled", !config.enabled)}
                  className={cn(
                    "relative w-10 h-5 rounded-full transition-colors",
                    config.enabled ? "bg-purple-500" : "bg-gray-600"
                  )}
                >
                  <div className={cn(
                    "absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform",
                    config.enabled ? "translate-x-5" : "translate-x-0.5"
                  )} />
                </button>
              </label>

              <label className="flex items-center justify-between">
                <span className="text-sm text-gray-300">Auto-trigger</span>
                <button
                  onClick={() => handleConfigChange("auto_trigger", !config.auto_trigger)}
                  className={cn(
                    "relative w-10 h-5 rounded-full transition-colors",
                    config.auto_trigger ? "bg-purple-500" : "bg-gray-600"
                  )}
                >
                  <div className={cn(
                    "absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform",
                    config.auto_trigger ? "translate-x-5" : "translate-x-0.5"
                  )} />
                </button>
              </label>

              <label className="flex items-center justify-between">
                <span className="text-sm text-gray-300">Require tests</span>
                <button
                  onClick={() => handleConfigChange("require_tests", !config.require_tests)}
                  className={cn(
                    "relative w-10 h-5 rounded-full transition-colors",
                    config.require_tests ? "bg-purple-500" : "bg-gray-600"
                  )}
                >
                  <div className={cn(
                    "absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform",
                    config.require_tests ? "translate-x-5" : "translate-x-0.5"
                  )} />
                </button>
              </label>

              <label className="flex items-center justify-between">
                <span className="text-sm text-gray-300">Min interval</span>
                <select
                  value={config.min_interval_seconds}
                  onChange={(e) => handleConfigChange("min_interval_seconds", Number(e.target.value))}
                  className="bg-white/5 border border-white/10 rounded px-2 py-1 text-sm text-gray-300"
                >
                  <option value={300}>5 min</option>
                  <option value={900}>15 min</option>
                  <option value={1800}>30 min</option>
                  <option value={3600}>1 hour</option>
                  <option value={7200}>2 hours</option>
                </select>
              </label>

              <label className="flex items-center justify-between">
                <span className="text-sm text-gray-300">Max files/cycle</span>
                <input
                  type="number"
                  value={config.max_files_per_cycle}
                  onChange={(e) => handleConfigChange("max_files_per_cycle", Number(e.target.value))}
                  min={1}
                  max={50}
                  className="w-16 bg-white/5 border border-white/10 rounded px-2 py-1 text-sm text-gray-300 text-right"
                />
              </label>
            </div>
          )}

          {/* Trigger Button */}
          {config.enabled && !status?.current_cycle && (
            <div className="space-y-2">
              {showTrigger ? (
                <div className="space-y-2">
                  <textarea
                    value={triggerDesc}
                    onChange={(e) => setTriggerDesc(e.target.value)}
                    placeholder="Describe the change to make..."
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-500 resize-none"
                    rows={3}
                    autoFocus
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={handleTrigger}
                      disabled={!triggerDesc.trim() || isTriggering}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-purple-500/20 text-purple-300 hover:bg-purple-500/30 border border-purple-500/30 text-sm disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      {isTriggering ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Play className="w-3.5 h-3.5" />
                      )}
                      {isTriggering ? "Starting..." : "Evolve"}
                    </button>
                    <button
                      onClick={() => { setShowTrigger(false); setTriggerDesc(""); }}
                      className="px-3 py-1.5 rounded-lg text-gray-400 hover:text-gray-200 text-sm transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setShowTrigger(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-purple-500/10 text-purple-300 hover:bg-purple-500/20 border border-purple-500/20 text-sm transition-colors"
                >
                  <Play className="w-3.5 h-3.5" />
                  Trigger Evolution
                </button>
              )}
            </div>
          )}

          {/* History Timeline */}
          {history.length > 0 && (
            <div className="space-y-1">
              <h4 className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-2">
                History
              </h4>
              {history.map((cycle) => (
                <div key={cycle.id} className="group">
                  <button
                    onClick={() => setExpandedCycle(expandedCycle === cycle.id ? null : cycle.id)}
                    className="w-full flex items-start gap-2 px-2 py-1.5 rounded hover:bg-white/5 transition-colors text-left"
                  >
                    {/* Timeline dot */}
                    <div className={cn(
                      "w-2.5 h-2.5 rounded-full border mt-1 flex-shrink-0",
                      DOT_COLORS[cycle.status] || DOT_COLORS.pending,
                    )} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={cn(
                          "px-1.5 py-0.5 rounded text-[10px] border",
                          STATUS_COLORS[cycle.status] || STATUS_COLORS.pending,
                        )}>
                          {cycle.status}
                        </span>
                        <span className="text-xs text-gray-500">
                          {cycle.trigger}
                        </span>
                        {cycle.duration_ms && (
                          <span className="text-xs text-gray-600">
                            {(cycle.duration_ms / 1000).toFixed(1)}s
                          </span>
                        )}
                        <span className="text-xs text-gray-600 ml-auto">
                          {cycle.created_at ? timeAgo(cycle.created_at) : ""}
                        </span>
                      </div>
                      <p className="text-xs text-gray-400 truncate mt-0.5">
                        {cycle.description}
                      </p>
                    </div>
                  </button>

                  {/* Expanded details */}
                  {expandedCycle === cycle.id && (
                    <div className="ml-5 pl-3 border-l border-white/10 space-y-2 pb-2">
                      {cycle.files_changed.length > 0 && (
                        <div>
                          <span className="text-[10px] text-gray-500 uppercase">Files Changed</span>
                          <div className="flex flex-wrap gap-1 mt-0.5">
                            {cycle.files_changed.map((f) => (
                              <span key={f} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-white/5 text-[10px] text-gray-400">
                                <FileCode2 className="w-3 h-3" />
                                {f}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {cycle.error && (
                        <div>
                          <span className="text-[10px] text-gray-500 uppercase">Error</span>
                          <p className="text-xs text-red-400 mt-0.5">{cycle.error}</p>
                        </div>
                      )}

                      {cycle.test_output && (
                        <div>
                          <span className="text-[10px] text-gray-500 uppercase">Test Output</span>
                          <pre className="text-[10px] text-gray-400 mt-0.5 bg-black/20 rounded p-1.5 overflow-x-auto max-h-24 overflow-y-auto">
                            {cycle.test_output}
                          </pre>
                        </div>
                      )}

                      {cycle.review_summary && (
                        <div>
                          <span className="text-[10px] text-gray-500 uppercase">Review</span>
                          <p className="text-xs text-gray-400 mt-0.5">{cycle.review_summary.slice(0, 300)}</p>
                        </div>
                      )}

                      {/* Rollback button for completed cycles */}
                      {cycle.status === "completed" && cycle.rollback_tag && (
                        <button
                          onClick={() => handleRollback(cycle.id)}
                          className="flex items-center gap-1.5 px-2 py-1 rounded bg-orange-500/10 text-orange-300 hover:bg-orange-500/20 border border-orange-500/20 text-xs transition-colors mt-1"
                        >
                          <RotateCcw className="w-3 h-3" />
                          Rollback
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Empty state */}
          {history.length === 0 && !isLoading && (
            <div className="text-center py-6 text-gray-500 text-sm">
              No evolution cycles yet.
              {config.enabled
                ? " Use the trigger button above or ask Edward to evolve."
                : " Enable evolution in the config to get started."}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
