"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Brain,
  Calendar,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock,
  Loader2,
  RefreshCw,
  Sparkles,
  Sun,
  Target,
  TrendingUp,
  Users,
  CheckCircle2,
  Pause,
  X,
} from "lucide-react";
import {
  getProactiveStatus,
  getOpenLoops,
  getEntities,
  updateProactiveConfig,
  updateOpenLoop,
  ProactiveStatus,
  ProactiveConfig,
  OpenLoop,
  EntityInfo,
} from "@/lib/api";

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return "never";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "--";
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

const STATUS_COLORS: Record<string, string> = {
  open: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  resolved: "bg-green-500/20 text-green-400 border-green-500/30",
  snoozed: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  expired: "bg-gray-500/20 text-gray-400 border-gray-500/30",
};

const ENTITY_TYPE_COLORS: Record<string, string> = {
  person: "bg-purple-500/20 text-purple-400",
  project: "bg-blue-500/20 text-blue-400",
  place: "bg-green-500/20 text-green-400",
  org: "bg-orange-500/20 text-orange-400",
  topic: "bg-gray-500/20 text-gray-400",
};

const CHANNEL_OPTIONS = [
  { label: "Push", value: "push" },
  { label: "iMessage", value: "imessage" },
  { label: "Chat", value: "chat" },
];

const DEPTH_OPTIONS = [
  { label: "Light", value: "light" },
  { label: "Standard", value: "standard" },
  { label: "Deep", value: "deep" },
];

export function ProactivePanel({
  isExpanded: propExpanded,
  hideHeader,
}: {
  isExpanded?: boolean;
  hideHeader?: boolean;
}) {
  const [expanded, setExpanded] = useState(propExpanded ?? false);
  const [status, setStatus] = useState<ProactiveStatus | null>(null);
  const [openLoops, setOpenLoops] = useState<OpenLoop[]>([]);
  const [entities, setEntities] = useState<EntityInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<"config" | "loops" | "entities">("config");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, loops, ents] = await Promise.all([
        getProactiveStatus(),
        getOpenLoops("open"),
        getEntities(),
      ]);
      setStatus(s);
      setOpenLoops(loops);
      setEntities(ents);
    } catch (e) {
      console.error("Proactive status fetch failed:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (expanded || propExpanded) refresh();
  }, [expanded, propExpanded, refresh]);

  const handleConfigChange = async (updates: Partial<ProactiveConfig>) => {
    try {
      const updated = await updateProactiveConfig(updates);
      setStatus((prev) =>
        prev ? { ...prev, config: updated } : prev
      );
    } catch (e) {
      console.error("Config update failed:", e);
    }
  };

  const handleResolveLoop = async (id: string) => {
    try {
      await updateOpenLoop(id, { status: "resolved" });
      setOpenLoops((prev) => prev.filter((l) => l.id !== id));
    } catch (e) {
      console.error("Resolve failed:", e);
    }
  };

  const handleSnoozeLoop = async (id: string) => {
    const until = new Date(Date.now() + 7 * 86400000).toISOString();
    try {
      await updateOpenLoop(id, { status: "snoozed", snooze_until: until });
      setOpenLoops((prev) => prev.filter((l) => l.id !== id));
    } catch (e) {
      console.error("Snooze failed:", e);
    }
  };

  const config = status?.config;

  return (
    <div className="border border-input-border rounded-lg bg-surface overflow-hidden">
      {!hideHeader && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center justify-between p-4 hover:bg-surface-hover transition-colors"
        >
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-terminal/10 flex items-center justify-center">
              <Sparkles className="w-4 h-4 text-terminal" />
            </div>
            <div className="text-left">
              <h3 className="font-medium text-text-primary">Proactive Intelligence</h3>
              <p className="text-xs text-text-muted">
                Entities, open loops, briefings
              </p>
            </div>
          </div>
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-text-muted" />
          ) : (
            <ChevronRight className="w-4 h-4 text-text-muted" />
          )}
        </button>
      )}

      {(expanded || propExpanded) && (
        <div className={hideHeader ? "" : "border-t border-input-border"}>
          {/* Stats bar */}
          <div className="flex items-center gap-4 px-4 py-3 bg-primary-bg/50 border-b border-input-border text-xs text-text-muted">
            <div className="flex items-center gap-1.5">
              <Users className="w-3.5 h-3.5" />
              <span>{status?.entity_count ?? 0} entities</span>
            </div>
            <div className="flex items-center gap-1.5">
              <Target className="w-3.5 h-3.5" />
              <span>{status?.open_loop_count ?? 0} open loops</span>
            </div>
            <button
              onClick={refresh}
              className="ml-auto p-1 hover:text-text-primary transition-colors"
              disabled={loading}
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>

          {/* Tabs */}
          <div className="flex border-b border-input-border">
            {(["config", "loops", "entities"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`flex-1 py-2 text-xs font-medium transition-colors ${
                  activeTab === tab
                    ? "text-terminal border-b-2 border-terminal"
                    : "text-text-muted hover:text-text-primary"
                }`}
              >
                {tab === "config" ? "Settings" : tab === "loops" ? `Loops (${openLoops.length})` : `Entities (${entities.length})`}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="p-4 space-y-4">
            {activeTab === "config" && config && (
              <div className="space-y-5">
                {/* Morning Briefing */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Sun className="w-4 h-4 text-amber-400" />
                      <span className="text-sm font-medium text-text-primary">Morning Briefing</span>
                    </div>
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        checked={config.morning_briefing_enabled}
                        onChange={(e) => handleConfigChange({ morning_briefing_enabled: e.target.checked })}
                        className="sr-only peer"
                      />
                      <div className="w-9 h-5 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:bg-terminal transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
                    </label>
                  </div>
                  {config.morning_briefing_enabled && (
                    <div className="flex gap-3 ml-6">
                      <div>
                        <label className="text-xs text-text-muted">Time</label>
                        <input
                          type="time"
                          value={config.morning_briefing_time}
                          onChange={(e) => handleConfigChange({ morning_briefing_time: e.target.value })}
                          className="block mt-1 px-2 py-1 text-xs bg-primary-bg border border-input-border rounded text-text-primary"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-text-muted">Channel</label>
                        <select
                          value={config.morning_briefing_channel}
                          onChange={(e) => handleConfigChange({ morning_briefing_channel: e.target.value })}
                          className="block mt-1 px-2 py-1 text-xs bg-primary-bg border border-input-border rounded text-text-primary"
                        >
                          {CHANNEL_OPTIONS.map((o) => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                          ))}
                        </select>
                      </div>
                      {status?.morning_briefing_running && (
                        <div className="flex items-end pb-1">
                          <span className="text-xs text-green-400 flex items-center gap-1">
                            <CircleDot className="w-3 h-3" /> Running
                          </span>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Meeting Prep */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Calendar className="w-4 h-4 text-blue-400" />
                      <span className="text-sm font-medium text-text-primary">Meeting Prep</span>
                    </div>
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        checked={config.meeting_prep_enabled}
                        onChange={(e) => handleConfigChange({ meeting_prep_enabled: e.target.checked })}
                        className="sr-only peer"
                      />
                      <div className="w-9 h-5 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:bg-terminal transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
                    </label>
                  </div>
                  {config.meeting_prep_enabled && (
                    <div className="flex gap-3 ml-6 items-end">
                      <div>
                        <label className="text-xs text-text-muted">Advance (min)</label>
                        <select
                          value={config.meeting_prep_advance_minutes}
                          onChange={(e) => handleConfigChange({ meeting_prep_advance_minutes: parseInt(e.target.value) })}
                          className="block mt-1 px-2 py-1 text-xs bg-primary-bg border border-input-border rounded text-text-primary"
                        >
                          {[5, 10, 15, 30, 60].map((m) => (
                            <option key={m} value={m}>{m}m</option>
                          ))}
                        </select>
                      </div>
                      {status?.meeting_prep_running && (
                        <span className="text-xs text-green-400 flex items-center gap-1 pb-1">
                          <CircleDot className="w-3 h-3" /> Running
                        </span>
                      )}
                    </div>
                  )}
                </div>

                {/* Pattern Detection */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <TrendingUp className="w-4 h-4 text-purple-400" />
                    <span className="text-sm font-medium text-text-primary">Weekly Patterns</span>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={config.pattern_detection_enabled}
                      onChange={(e) => handleConfigChange({ pattern_detection_enabled: e.target.checked })}
                      className="sr-only peer"
                    />
                    <div className="w-9 h-5 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:bg-terminal transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
                  </label>
                </div>

                {/* Open Loop Nudges */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Target className="w-4 h-4 text-red-400" />
                      <span className="text-sm font-medium text-text-primary">Open Loop Nudges</span>
                    </div>
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        checked={config.open_loop_nudge_enabled}
                        onChange={(e) => handleConfigChange({ open_loop_nudge_enabled: e.target.checked })}
                        className="sr-only peer"
                      />
                      <div className="w-9 h-5 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:bg-terminal transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
                    </label>
                  </div>
                  {config.open_loop_nudge_enabled && (
                    <div className="ml-6">
                      <label className="text-xs text-text-muted">Nudge after (days)</label>
                      <select
                        value={config.open_loop_nudge_days}
                        onChange={(e) => handleConfigChange({ open_loop_nudge_days: parseInt(e.target.value) })}
                        className="block mt-1 px-2 py-1 text-xs bg-primary-bg border border-input-border rounded text-text-primary"
                      >
                        {[1, 3, 5, 7, 14, 30].map((d) => (
                          <option key={d} value={d}>{d}d</option>
                        ))}
                      </select>
                    </div>
                  )}
                </div>

                {/* Extraction Depth */}
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <Brain className="w-4 h-4 text-cyan-400" />
                    <span className="text-sm font-medium text-text-primary">Extraction Depth</span>
                  </div>
                  <div className="flex gap-2 ml-6">
                    {DEPTH_OPTIONS.map((opt) => (
                      <button
                        key={opt.value}
                        onClick={() => handleConfigChange({ extraction_depth: opt.value })}
                        className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                          config.extraction_depth === opt.value
                            ? "bg-terminal/20 text-terminal border-terminal/50"
                            : "bg-primary-bg text-text-muted border-input-border hover:text-text-primary"
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {activeTab === "loops" && (
              <div className="space-y-2">
                {openLoops.length === 0 ? (
                  <p className="text-xs text-text-muted text-center py-6">No open loops</p>
                ) : (
                  openLoops.map((loop) => (
                    <div
                      key={loop.id}
                      className="flex items-start gap-3 p-3 rounded-lg border border-input-border bg-primary-bg/50"
                    >
                      <CircleDot className="w-4 h-4 text-amber-400 mt-0.5 flex-shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-text-primary">{loop.description}</p>
                        <div className="flex items-center gap-2 mt-1">
                          <span className={`px-1.5 py-0.5 text-[10px] rounded border ${STATUS_COLORS[loop.status] || STATUS_COLORS.open}`}>
                            {loop.status}
                          </span>
                          <span className="text-[10px] text-text-muted">
                            {timeAgo(loop.detected_at)}
                          </span>
                          {loop.nudge_count > 0 && (
                            <span className="text-[10px] text-text-muted">
                              nudged {loop.nudge_count}x
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="flex gap-1 flex-shrink-0">
                        <button
                          onClick={() => handleResolveLoop(loop.id)}
                          title="Resolve"
                          className="p-1 text-green-400 hover:bg-green-500/20 rounded transition-colors"
                        >
                          <CheckCircle2 className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleSnoozeLoop(loop.id)}
                          title="Snooze 7d"
                          className="p-1 text-blue-400 hover:bg-blue-500/20 rounded transition-colors"
                        >
                          <Pause className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}

            {activeTab === "entities" && (
              <div className="space-y-2">
                {entities.length === 0 ? (
                  <p className="text-xs text-text-muted text-center py-6">No entities tracked yet</p>
                ) : (
                  entities.map((entity) => (
                    <div
                      key={entity.id}
                      className="flex items-center gap-3 p-3 rounded-lg border border-input-border bg-primary-bg/50"
                    >
                      <span className={`px-2 py-0.5 text-[10px] rounded-full ${ENTITY_TYPE_COLORS[entity.entity_type] || ENTITY_TYPE_COLORS.topic}`}>
                        {entity.entity_type}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-text-primary truncate">{entity.name}</p>
                        {entity.aliases.length > 0 && (
                          <p className="text-[10px] text-text-muted truncate">
                            aka: {entity.aliases.join(", ")}
                          </p>
                        )}
                      </div>
                      <div className="text-right flex-shrink-0">
                        <p className="text-xs text-text-muted">{entity.mention_count}x</p>
                        <p className="text-[10px] text-text-muted">{timeAgo(entity.last_seen)}</p>
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
