"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import {
  ChevronDown,
  ChevronRight,
  Brain,
  Search,
  Trash2,
  RefreshCw,
  AlertCircle,
  ChevronLeft,
  ChevronRightIcon,
  X,
  Infinity,
  Clock,
  TrendingUp,
  Eye,
  Lightbulb,
  Crown,
} from "lucide-react";
import {
  searchMemories,
  deleteMemory,
  MemoryItem,
  MemoryStats,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const MEMORY_TYPES = ["all", "fact", "preference", "context", "instruction"] as const;
type MemoryType = (typeof MEMORY_TYPES)[number];

const TEMPORAL_NATURES = ["all", "timeless", "temporary", "evolving"] as const;
type TemporalNature = (typeof TEMPORAL_NATURES)[number];

const TIERS = ["all", "observation", "belief", "knowledge"] as const;
type Tier = (typeof TIERS)[number];

type SortMode = "recent" | "reinforced";

const TYPE_COLORS: Record<string, string> = {
  fact: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  preference: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  context: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  instruction: "bg-green-500/20 text-green-400 border-green-500/30",
};

const TEMPORAL_COLORS: Record<string, string> = {
  timeless: "bg-slate-500/20 text-slate-400 border-slate-500/30",
  temporary: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  evolving: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
};

const TIER_COLORS: Record<string, string> = {
  observation: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  belief: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  knowledge: "bg-amber-500/20 text-amber-400 border-amber-500/30",
};

const TIER_STAT_COLORS: Record<string, { bg: string; text: string; border: string; ring: string }> = {
  knowledge: { bg: "bg-amber-500/10", text: "text-amber-400", border: "border-amber-500/30", ring: "ring-amber-500/40" },
  belief: { bg: "bg-blue-500/10", text: "text-blue-400", border: "border-blue-500/30", ring: "ring-blue-500/40" },
  observation: { bg: "bg-gray-500/10", text: "text-gray-400", border: "border-gray-500/30", ring: "ring-gray-500/40" },
};

const TIER_ICONS: Record<string, typeof Eye> = {
  observation: Eye,
  belief: Lightbulb,
  knowledge: Crown,
};

const TEMPORAL_ICONS: Record<string, typeof Infinity> = {
  timeless: Infinity,
  temporary: Clock,
  evolving: TrendingUp,
};

function relativeTime(dateStr: string | null): string {
  if (!dateStr) return "N/A";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "N/A";
  const now = new Date();
  let diffMs = now.getTime() - date.getTime();
  if (diffMs < 0) diffMs = 0; // Clamp future timestamps (clock skew)
  const days = Math.floor(diffMs / 86400000);

  if (days < 1) {
    const hours = Math.floor(diffMs / 3600000);
    if (hours < 1) return "just now";
    return `${hours}h ago`;
  }
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.floor(days / 7)}w ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

interface MemoryBrowserProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function MemoryBrowser({ isExpanded: initialExpanded = false, hideHeader = false }: MemoryBrowserProps) {
  const [isExpanded, setIsExpanded] = useState(initialExpanded);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedType, setSelectedType] = useState<MemoryType>("all");
  const [selectedTemporal, setSelectedTemporal] = useState<TemporalNature>("all");
  const [selectedTier, setSelectedTier] = useState<Tier>("all");
  const [sortMode, setSortMode] = useState<SortMode>("recent");
  const [page, setPage] = useState(0);
  const pageSize = 10;

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<MemoryItem | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const loadMemories = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await searchMemories(
        searchQuery || undefined,
        selectedType === "all" ? undefined : selectedType,
        undefined,
        pageSize,
        page * pageSize,
        selectedTemporal === "all" ? undefined : selectedTemporal,
        selectedTier === "all" ? undefined : selectedTier
      );
      setMemories(result.memories);
      setStats(result.stats);
      setTotal(result.pagination.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load memories");
    } finally {
      setIsLoading(false);
    }
  }, [searchQuery, selectedType, selectedTemporal, selectedTier, page]);

  useEffect(() => {
    if (isExpanded) {
      loadMemories();
    }
  }, [isExpanded, loadMemories]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(0);
    loadMemories();
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;

    setIsDeleting(true);
    try {
      await deleteMemory(deleteTarget.id);
      setDeleteTarget(null);
      loadMemories();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete memory");
    } finally {
      setIsDeleting(false);
    }
  };

  // Client-side sort of the current page
  const sortedMemories = useMemo(() => {
    if (sortMode === "recent") return memories;
    return [...memories].sort((a, b) => {
      const diff = b.reinforcement_count - a.reinforcement_count;
      if (diff !== 0) return diff;
      // Tie-break by created_at desc
      const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
      const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
      return bTime - aTime;
    });
  }, [memories, sortMode]);

  const totalPages = Math.ceil(total / pageSize);

  const tierOrder = ["knowledge", "belief", "observation"] as const;

  return (
    <div className={cn("border border-input-border rounded-lg overflow-hidden", !hideHeader && "mt-8")}>
      {!hideHeader && (
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full px-4 py-3 flex items-center justify-between bg-surface hover:bg-surface/80 transition-colors"
        >
          <div className="flex items-center gap-2 text-text-primary">
            <Brain className="w-4 h-4 text-terminal" />
            <span className="font-medium text-sm">Memory Browser</span>
            {stats && (
              <span className="text-xs text-text-muted">({stats.total} memories)</span>
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
          {/* Tier stats bar */}
          {stats?.by_tier && (
            <div className="flex gap-2">
              {tierOrder.map((tier) => {
                const count = stats.by_tier[tier] || 0;
                const pct = stats.total > 0 ? ((count / stats.total) * 100).toFixed(0) : "0";
                const colors = TIER_STAT_COLORS[tier];
                const TierIcon = TIER_ICONS[tier];
                const isActive = selectedTier === tier;
                return (
                  <button
                    key={tier}
                    onClick={() => {
                      setSelectedTier(selectedTier === tier ? "all" : tier);
                      setPage(0);
                    }}
                    className={cn(
                      "flex-1 px-3 py-2 rounded-lg border transition-all text-left",
                      colors.bg, colors.border,
                      isActive && `ring-1 ${colors.ring}`,
                      "hover:opacity-80"
                    )}
                  >
                    <div className={cn("flex items-center gap-1.5 text-xs font-medium", colors.text)}>
                      <TierIcon className="w-3.5 h-3.5" />
                      <span className="capitalize">{tier}</span>
                    </div>
                    <div className="flex items-baseline gap-1 mt-0.5">
                      <span className={cn("text-lg font-semibold", colors.text)}>{count}</span>
                      <span className="text-xs text-text-muted">{pct}%</span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}

          {/* Search and filters */}
          <div className="flex flex-col sm:flex-row gap-3">
            <form onSubmit={handleSearch} className="flex-1 flex gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search memories..."
                  className="w-full pl-9 pr-4 py-2 rounded-lg border border-input-border bg-input-bg text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-terminal"
                />
              </div>
              <button
                type="submit"
                disabled={isLoading}
                className="px-4 py-2 rounded-lg bg-terminal text-white text-sm hover:opacity-80 disabled:opacity-50 transition-opacity"
              >
                Search
              </button>
            </form>

            <div className="flex gap-2 items-center flex-wrap">
              <select
                value={selectedType}
                onChange={(e) => {
                  setSelectedType(e.target.value as MemoryType);
                  setPage(0);
                }}
                className="px-3 py-2 rounded-lg border border-input-border bg-input-bg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-terminal"
              >
                {MEMORY_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type === "all" ? "All Types" : type.charAt(0).toUpperCase() + type.slice(1)}
                  </option>
                ))}
              </select>

              <select
                value={selectedTemporal}
                onChange={(e) => {
                  setSelectedTemporal(e.target.value as TemporalNature);
                  setPage(0);
                }}
                className="px-3 py-2 rounded-lg border border-input-border bg-input-bg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-terminal"
              >
                {TEMPORAL_NATURES.map((nature) => (
                  <option key={nature} value={nature}>
                    {nature === "all" ? "All Natures" : nature.charAt(0).toUpperCase() + nature.slice(1)}
                  </option>
                ))}
              </select>

              <select
                value={selectedTier}
                onChange={(e) => {
                  setSelectedTier(e.target.value as Tier);
                  setPage(0);
                }}
                className="px-3 py-2 rounded-lg border border-input-border bg-input-bg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-terminal"
              >
                {TIERS.map((tier) => (
                  <option key={tier} value={tier}>
                    {tier === "all" ? "All Tiers" : tier.charAt(0).toUpperCase() + tier.slice(1)}
                  </option>
                ))}
              </select>

              {/* Sort toggle */}
              <div className="flex rounded-lg border border-input-border overflow-hidden">
                <button
                  onClick={() => setSortMode("recent")}
                  className={cn(
                    "px-2.5 py-1.5 text-xs font-medium transition-colors",
                    sortMode === "recent"
                      ? "bg-terminal/20 text-terminal"
                      : "bg-input-bg text-text-muted hover:text-text-primary"
                  )}
                >
                  Recent
                </button>
                <button
                  onClick={() => setSortMode("reinforced")}
                  className={cn(
                    "px-2.5 py-1.5 text-xs font-medium transition-colors border-l border-input-border",
                    sortMode === "reinforced"
                      ? "bg-terminal/20 text-terminal"
                      : "bg-input-bg text-text-muted hover:text-text-primary"
                  )}
                >
                  Reinforced
                </button>
              </div>

              <button
                onClick={loadMemories}
                disabled={isLoading}
                className="p-2 rounded-lg border border-input-border bg-input-bg text-text-muted hover:text-text-primary transition-colors disabled:opacity-50"
                title="Refresh"
              >
                <RefreshCw className={cn("w-4 h-4", isLoading && "animate-spin")} />
              </button>
            </div>
          </div>

          {error && (
            <div className="flex items-center gap-2 text-red-500 text-sm p-3 bg-red-500/10 rounded-lg">
              <AlertCircle className="w-4 h-4" />
              {error}
            </div>
          )}

          {/* Memory list */}
          <div className="space-y-2">
            {isLoading && memories.length === 0 ? (
              <div className="text-center py-8 text-text-muted">Loading memories...</div>
            ) : memories.length === 0 ? (
              <div className="text-center py-8 text-text-muted">
                {searchQuery || selectedType !== "all" || selectedTemporal !== "all" || selectedTier !== "all"
                  ? "No memories match your search"
                  : "No memories stored yet"}
              </div>
            ) : (
              sortedMemories.map((memory) => {
                const TemporalIcon = TEMPORAL_ICONS[memory.temporal_nature] || Infinity;
                const TierIcon = TIER_ICONS[memory.tier] || Eye;
                return (
                  <div
                    key={memory.id}
                    className="p-4 bg-surface rounded-lg border border-input-border hover:border-terminal/30 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-2 flex-wrap">
                          <span
                            className={cn(
                              "px-2 py-0.5 rounded text-xs font-mono border",
                              TYPE_COLORS[memory.memory_type] || "bg-gray-500/20 text-gray-400 border-gray-500/30"
                            )}
                          >
                            {memory.memory_type}
                          </span>
                          <span
                            className={cn(
                              "px-2 py-0.5 rounded text-xs font-mono border inline-flex items-center gap-1",
                              TEMPORAL_COLORS[memory.temporal_nature] || TEMPORAL_COLORS.timeless
                            )}
                          >
                            <TemporalIcon className="w-3 h-3" />
                            {memory.temporal_nature}
                          </span>
                          <span
                            className={cn(
                              "rounded text-xs font-mono border inline-flex items-center gap-1",
                              TIER_COLORS[memory.tier] || TIER_COLORS.observation,
                              memory.tier === "knowledge" ? "px-2.5 py-1" : "px-2 py-0.5"
                            )}
                          >
                            <TierIcon className={cn(memory.tier === "knowledge" ? "w-3.5 h-3.5" : "w-3 h-3")} />
                            {memory.tier}
                            {memory.reinforcement_count > 0 && (
                              <span className="opacity-70">x{memory.reinforcement_count}</span>
                            )}
                          </span>
                          <span className="text-xs text-text-muted">
                            Importance: {(memory.importance * 100).toFixed(0)}%
                          </span>
                          {memory.score != null && memory.score > 0 && (
                            <span className="text-xs text-terminal">
                              Match: {(memory.score * 100).toFixed(0)}%
                            </span>
                          )}
                        </div>
                        <p className="text-sm text-text-primary break-words">{memory.content}</p>
                        <div className="mt-2 flex flex-wrap gap-3 text-xs text-text-muted">
                          <span>Learned {relativeTime(memory.created_at)}</span>
                          {memory.updated_at && memory.updated_at !== memory.created_at && (
                            <span>Updated {relativeTime(memory.updated_at)}</span>
                          )}
                          {memory.last_accessed && memory.last_accessed !== memory.created_at && (
                            <span>Last accessed {relativeTime(memory.last_accessed)}</span>
                          )}
                          <span>Accessed {memory.access_count}x</span>
                        </div>
                      </div>
                      <button
                        onClick={() => setDeleteTarget(memory)}
                        className="p-2 text-text-muted hover:text-red-500 transition-colors rounded-lg hover:bg-red-500/10"
                        title="Delete memory"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between pt-2">
              <span className="text-xs text-text-muted">
                Showing {page * pageSize + 1}-{Math.min((page + 1) * pageSize, total)} of {total}
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="p-2 rounded-lg border border-input-border bg-input-bg text-text-muted hover:text-text-primary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <span className="text-sm text-text-muted">
                  {page + 1} / {totalPages}
                </span>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                  className="p-2 rounded-lg border border-input-border bg-input-bg text-text-muted hover:text-text-primary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <ChevronRightIcon className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Delete confirmation modal */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-primary-bg border border-input-border rounded-lg p-6 max-w-md mx-4 shadow-xl">
            <div className="flex items-start justify-between mb-4">
              <h3 className="text-lg font-medium text-text-primary">Delete Memory</h3>
              <button
                onClick={() => setDeleteTarget(null)}
                className="p-1 text-text-muted hover:text-text-primary transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <p className="text-sm text-text-muted mb-4">
              Are you sure you want to delete this memory? This action cannot be undone.
            </p>
            <div className="p-3 bg-surface rounded-lg mb-4">
              <p className="text-sm text-text-primary">{deleteTarget.content}</p>
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
