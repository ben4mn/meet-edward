"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Search,
  Trash2,
  RefreshCw,
  AlertCircle,
  ChevronLeft,
  ChevronRightIcon,
  X,
  Plus,
  ArrowLeft,
  Tag,
} from "lucide-react";
import {
  searchDocuments,
  deleteDocument,
  createDocument,
  DocumentItem,
  DocumentStats,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const TAG_COLORS = [
  "bg-blue-500/20 text-blue-400 border-blue-500/30",
  "bg-purple-500/20 text-purple-400 border-purple-500/30",
  "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  "bg-green-500/20 text-green-400 border-green-500/30",
  "bg-pink-500/20 text-pink-400 border-pink-500/30",
  "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
  "bg-orange-500/20 text-orange-400 border-orange-500/30",
];

function getTagColor(tag: string): string {
  let hash = 0;
  for (let i = 0; i < tag.length; i++) {
    hash = tag.charCodeAt(i) + ((hash << 5) - hash);
  }
  return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
}

interface DocumentBrowserProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function DocumentBrowser({ isExpanded: initialExpanded = false, hideHeader = false }: DocumentBrowserProps) {
  const [isExpanded, setIsExpanded] = useState(initialExpanded);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [stats, setStats] = useState<DocumentStats | null>(null);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTag, setSelectedTag] = useState<string>("all");
  const [page, setPage] = useState(0);
  const pageSize = 10;

  // View states
  const [viewingDoc, setViewingDoc] = useState<DocumentItem | null>(null);
  const [showNewForm, setShowNewForm] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newContent, setNewContent] = useState("");
  const [newTags, setNewTags] = useState("");
  const [isCreating, setIsCreating] = useState(false);

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<DocumentItem | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const loadDocuments = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await searchDocuments(
        searchQuery || undefined,
        selectedTag === "all" ? undefined : selectedTag,
        pageSize,
        page * pageSize
      );
      setDocuments(result.documents);
      setStats(result.stats);
      setTotal(result.pagination.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load documents");
    } finally {
      setIsLoading(false);
    }
  }, [searchQuery, selectedTag, page]);

  useEffect(() => {
    if (isExpanded) {
      loadDocuments();
    }
  }, [isExpanded, loadDocuments]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(0);
    loadDocuments();
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await deleteDocument(deleteTarget.id);
      setDeleteTarget(null);
      if (viewingDoc?.id === deleteTarget.id) setViewingDoc(null);
      loadDocuments();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete document");
    } finally {
      setIsDeleting(false);
    }
  };

  const handleCreate = async () => {
    if (!newTitle.trim() || !newContent.trim()) return;
    setIsCreating(true);
    setError(null);
    try {
      await createDocument({
        title: newTitle.trim(),
        content: newContent.trim(),
        tags: newTags.trim() || undefined,
      });
      setShowNewForm(false);
      setNewTitle("");
      setNewContent("");
      setNewTags("");
      loadDocuments();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create document");
    } finally {
      setIsCreating(false);
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "N/A";
    const date = new Date(dateStr);
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  };

  const renderTags = (tags: string | null) => {
    if (!tags) return null;
    return (
      <div className="flex flex-wrap gap-1">
        {tags.split(",").map((tag) => {
          const trimmed = tag.trim();
          if (!trimmed) return null;
          return (
            <span
              key={trimmed}
              className={cn(
                "px-2 py-0.5 rounded text-xs font-mono border cursor-pointer",
                getTagColor(trimmed)
              )}
              onClick={(e) => {
                e.stopPropagation();
                setSelectedTag(trimmed);
                setPage(0);
              }}
            >
              {trimmed}
            </span>
          );
        })}
      </div>
    );
  };

  const totalPages = Math.ceil(total / pageSize);
  const allTags = stats ? Object.keys(stats.by_tag).sort() : [];

  return (
    <div className={cn("border border-input-border rounded-lg overflow-hidden", !hideHeader && "mt-8")}>
      {!hideHeader && (
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full px-4 py-3 flex items-center justify-between bg-surface hover:bg-surface/80 transition-colors"
        >
          <div className="flex items-center gap-2 text-text-primary">
            <FileText className="w-4 h-4 text-terminal" />
            <span className="font-medium text-sm">Document Store</span>
            {stats && (
              <span className="text-xs text-text-muted">({stats.total} documents)</span>
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
          {/* Viewing a single document */}
          {viewingDoc ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setViewingDoc(null)}
                  className="p-1 text-text-muted hover:text-text-primary transition-colors"
                >
                  <ArrowLeft className="w-4 h-4" />
                </button>
                <h3 className="text-sm font-medium text-text-primary flex-1 truncate">
                  {viewingDoc.title}
                </h3>
                <button
                  onClick={() => setDeleteTarget(viewingDoc)}
                  className="p-2 text-text-muted hover:text-red-500 transition-colors rounded-lg hover:bg-red-500/10"
                  title="Delete document"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
              {viewingDoc.tags && (
                <div className="flex items-center gap-2">
                  <Tag className="w-3 h-3 text-text-muted" />
                  {renderTags(viewingDoc.tags)}
                </div>
              )}
              <div className="text-xs text-text-muted flex gap-3">
                <span>Created: {formatDate(viewingDoc.created_at)}</span>
                {viewingDoc.updated_at && viewingDoc.updated_at !== viewingDoc.created_at && (
                  <span>Updated: {formatDate(viewingDoc.updated_at)}</span>
                )}
                <span>Accessed: {viewingDoc.access_count}x</span>
              </div>
              <div className="p-4 bg-surface rounded-lg border border-input-border max-h-96 overflow-y-auto">
                <pre className="text-sm text-text-primary whitespace-pre-wrap font-mono break-words">
                  {viewingDoc.content}
                </pre>
              </div>
            </div>
          ) : showNewForm ? (
            /* New document form */
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setShowNewForm(false)}
                  className="p-1 text-text-muted hover:text-text-primary transition-colors"
                >
                  <ArrowLeft className="w-4 h-4" />
                </button>
                <h3 className="text-sm font-medium text-text-primary">New Document</h3>
              </div>
              <input
                type="text"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="Document title..."
                className="w-full px-4 py-2 rounded-lg border border-input-border bg-input-bg text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-terminal"
              />
              <textarea
                value={newContent}
                onChange={(e) => setNewContent(e.target.value)}
                placeholder="Document content (markdown supported)..."
                rows={10}
                className="w-full px-4 py-2 rounded-lg border border-input-border bg-input-bg text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-terminal font-mono resize-y"
              />
              <input
                type="text"
                value={newTags}
                onChange={(e) => setNewTags(e.target.value)}
                placeholder="Tags (comma-separated, e.g. recipe,italian,dinner)..."
                className="w-full px-4 py-2 rounded-lg border border-input-border bg-input-bg text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-terminal"
              />
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setShowNewForm(false)}
                  className="px-4 py-2 rounded-lg border border-input-border text-sm text-text-primary hover:bg-surface transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreate}
                  disabled={isCreating || !newTitle.trim() || !newContent.trim()}
                  className="px-4 py-2 rounded-lg bg-terminal text-white text-sm hover:opacity-80 disabled:opacity-50 transition-opacity"
                >
                  {isCreating ? "Saving..." : "Save Document"}
                </button>
              </div>
            </div>
          ) : (
            /* Document list view */
            <>
              {/* Search and filters */}
              <div className="flex flex-col sm:flex-row gap-3">
                <form onSubmit={handleSearch} className="flex-1 flex gap-2">
                  <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
                    <input
                      type="text"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      placeholder="Search documents..."
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

                <div className="flex gap-2 items-center">
                  <select
                    value={selectedTag}
                    onChange={(e) => {
                      setSelectedTag(e.target.value);
                      setPage(0);
                    }}
                    className="px-3 py-2 rounded-lg border border-input-border bg-input-bg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-terminal"
                  >
                    <option value="all">All Tags</option>
                    {allTags.map((tag) => (
                      <option key={tag} value={tag}>
                        {tag} ({stats?.by_tag[tag] || 0})
                      </option>
                    ))}
                  </select>

                  <button
                    onClick={() => setShowNewForm(true)}
                    className="p-2 rounded-lg border border-input-border bg-input-bg text-text-muted hover:text-terminal transition-colors"
                    title="New Document"
                  >
                    <Plus className="w-4 h-4" />
                  </button>

                  <button
                    onClick={loadDocuments}
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

              {/* Document list */}
              <div className="space-y-2">
                {isLoading && documents.length === 0 ? (
                  <div className="text-center py-8 text-text-muted">Loading documents...</div>
                ) : documents.length === 0 ? (
                  <div className="text-center py-8 text-text-muted">
                    {searchQuery || selectedTag !== "all"
                      ? "No documents match your search"
                      : "No documents stored yet"}
                  </div>
                ) : (
                  documents.map((doc) => (
                    <div
                      key={doc.id}
                      onClick={() => setViewingDoc(doc)}
                      className="p-4 bg-surface rounded-lg border border-input-border hover:border-terminal/30 transition-colors cursor-pointer"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <FileText className="w-4 h-4 text-terminal flex-shrink-0" />
                            <span className="text-sm font-medium text-text-primary truncate">
                              {doc.title}
                            </span>
                            {doc.score != null && doc.score > 0 && (
                              <span className="text-xs text-terminal">
                                {(doc.score * 100).toFixed(0)}%
                              </span>
                            )}
                          </div>
                          {doc.tags && <div className="mb-2 ml-6">{renderTags(doc.tags)}</div>}
                          <p className="text-xs text-text-muted ml-6 line-clamp-2">
                            {doc.content.slice(0, 200).replace(/\n/g, " ")}
                            {doc.content.length > 200 ? "..." : ""}
                          </p>
                          <div className="mt-2 ml-6 flex flex-wrap gap-3 text-xs text-text-muted">
                            <span>Updated: {formatDate(doc.updated_at)}</span>
                            <span>Accessed: {doc.access_count}x</span>
                          </div>
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setDeleteTarget(doc);
                          }}
                          className="p-2 text-text-muted hover:text-red-500 transition-colors rounded-lg hover:bg-red-500/10"
                          title="Delete document"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))
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
            </>
          )}
        </div>
      )}

      {/* Delete confirmation modal */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-primary-bg border border-input-border rounded-lg p-6 max-w-md mx-4 shadow-xl">
            <div className="flex items-start justify-between mb-4">
              <h3 className="text-lg font-medium text-text-primary">Delete Document</h3>
              <button
                onClick={() => setDeleteTarget(null)}
                className="p-1 text-text-muted hover:text-text-primary transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <p className="text-sm text-text-muted mb-4">
              Are you sure you want to delete this document? This action cannot be undone.
            </p>
            <div className="p-3 bg-surface rounded-lg mb-4">
              <p className="text-sm font-medium text-text-primary">{deleteTarget.title}</p>
              <p className="text-xs text-text-muted mt-1">
                {deleteTarget.content.slice(0, 100)}
                {deleteTarget.content.length > 100 ? "..." : ""}
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
