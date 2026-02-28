"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ChevronDown,
  ChevronRight,
  FolderOpen,
  Trash2,
  RefreshCw,
  AlertCircle,
  X,
  ArrowLeft,
  Download,
  ChevronLeft,
  ChevronRightIcon,
} from "lucide-react";
import {
  listFiles,
  deleteFile,
  StoredFileItem,
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

const CATEGORY_COLORS: Record<string, string> = {
  upload: "bg-blue-500/20 text-blue-400",
  generated: "bg-purple-500/20 text-purple-400",
  artifact: "bg-orange-500/20 text-orange-400",
  processed: "bg-cyan-500/20 text-cyan-400",
};

const SOURCE_COLORS: Record<string, string> = {
  user: "bg-green-500/20 text-green-400",
  edward: "bg-purple-500/20 text-purple-400",
  sandbox: "bg-yellow-500/20 text-yellow-400",
};

function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

interface FileBrowserProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function FileBrowser({ isExpanded: initialExpanded = false, hideHeader = false }: FileBrowserProps) {
  const [isExpanded, setIsExpanded] = useState(initialExpanded);
  const [files, setFiles] = useState<StoredFileItem[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [page, setPage] = useState(0);
  const pageSize = 10;

  // Detail view
  const [viewingFile, setViewingFile] = useState<StoredFileItem | null>(null);

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<StoredFileItem | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const loadFiles = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await listFiles(
        categoryFilter === "all" ? undefined : categoryFilter,
        sourceFilter === "all" ? undefined : sourceFilter,
        pageSize,
        page * pageSize
      );
      setFiles(result.files);
      setTotal(result.pagination.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load files");
    } finally {
      setIsLoading(false);
    }
  }, [categoryFilter, sourceFilter, page]);

  useEffect(() => {
    if (isExpanded) {
      loadFiles();
    }
  }, [isExpanded, loadFiles]);

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await deleteFile(deleteTarget.id);
      setDeleteTarget(null);
      if (viewingFile?.id === deleteTarget.id) setViewingFile(null);
      loadFiles();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete file");
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
                "px-2 py-0.5 rounded text-xs font-mono border",
                getTagColor(trimmed)
              )}
            >
              {trimmed}
            </span>
          );
        })}
      </div>
    );
  };

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className={cn("border border-input-border rounded-lg overflow-hidden", !hideHeader && "mt-8")}>
      {!hideHeader && (
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full px-4 py-3 flex items-center justify-between bg-surface hover:bg-surface/80 transition-colors"
        >
          <div className="flex items-center gap-2 text-text-primary">
            <FolderOpen className="w-4 h-4 text-terminal" />
            <span className="font-medium text-sm">File Storage</span>
            {total > 0 && (
              <span className="text-xs text-text-muted">({total} files)</span>
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
          {viewingFile ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setViewingFile(null)}
                  className="p-1 text-text-muted hover:text-text-primary transition-colors"
                >
                  <ArrowLeft className="w-4 h-4" />
                </button>
                <h3 className="text-sm font-mono font-medium text-text-primary flex-1 truncate">
                  {viewingFile.filename}
                </h3>
                <a
                  href={viewingFile.download_url}
                  className="p-2 text-text-muted hover:text-terminal transition-colors rounded-lg hover:bg-terminal/10"
                  title="Download file"
                >
                  <Download className="w-4 h-4" />
                </a>
                <button
                  onClick={() => setDeleteTarget(viewingFile)}
                  className="p-2 text-text-muted hover:text-red-500 transition-colors rounded-lg hover:bg-red-500/10"
                  title="Delete file"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
              <div className="p-4 bg-surface rounded-lg border border-input-border space-y-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={cn("px-2 py-0.5 rounded text-xs font-mono", CATEGORY_COLORS[viewingFile.category] || "bg-gray-500/20 text-gray-400")}>
                    {viewingFile.category}
                  </span>
                  <span className={cn("px-2 py-0.5 rounded text-xs font-mono", SOURCE_COLORS[viewingFile.source] || "bg-gray-500/20 text-gray-400")}>
                    {viewingFile.source}
                  </span>
                </div>
                {viewingFile.description && (
                  <p className="text-sm text-text-primary">{viewingFile.description}</p>
                )}
                {viewingFile.tags && (
                  <div>{renderTags(viewingFile.tags)}</div>
                )}
                <div className="grid grid-cols-2 gap-3 text-xs text-text-muted pt-2 border-t border-input-border">
                  <div><span className="text-text-muted/60">MIME:</span> {viewingFile.mime_type}</div>
                  <div><span className="text-text-muted/60">Size:</span> {formatFileSize(viewingFile.size_bytes)}</div>
                  <div><span className="text-text-muted/60">Created:</span> {formatDate(viewingFile.created_at)}</div>
                  <div><span className="text-text-muted/60">Updated:</span> {formatDate(viewingFile.updated_at)}</div>
                  <div><span className="text-text-muted/60">Last accessed:</span> {formatDate(viewingFile.last_accessed)}</div>
                  <div><span className="text-text-muted/60">Access count:</span> {viewingFile.access_count}</div>
                  {viewingFile.source_conversation_id && (
                    <div className="col-span-2">
                      <span className="text-text-muted/60">Conversation:</span>{" "}
                      <span className="font-mono text-xs">{viewingFile.source_conversation_id}</span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <>
              {/* Filters */}
              <div className="flex flex-col sm:flex-row gap-3">
                <div className="flex gap-2 flex-1">
                  <select
                    value={categoryFilter}
                    onChange={(e) => {
                      setCategoryFilter(e.target.value);
                      setPage(0);
                    }}
                    className="px-3 py-2 rounded-lg border border-input-border bg-input-bg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-terminal"
                  >
                    <option value="all">All Categories</option>
                    <option value="upload">Upload</option>
                    <option value="generated">Generated</option>
                    <option value="artifact">Artifact</option>
                    <option value="processed">Processed</option>
                  </select>
                  <select
                    value={sourceFilter}
                    onChange={(e) => {
                      setSourceFilter(e.target.value);
                      setPage(0);
                    }}
                    className="px-3 py-2 rounded-lg border border-input-border bg-input-bg text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-terminal"
                  >
                    <option value="all">All Sources</option>
                    <option value="user">User</option>
                    <option value="edward">Edward</option>
                    <option value="sandbox">Sandbox</option>
                  </select>
                </div>
                <button
                  onClick={loadFiles}
                  disabled={isLoading}
                  className="p-2 rounded-lg border border-input-border bg-input-bg text-text-muted hover:text-text-primary transition-colors disabled:opacity-50"
                  title="Refresh"
                >
                  <RefreshCw className={cn("w-4 h-4", isLoading && "animate-spin")} />
                </button>
              </div>

              {error && (
                <div className="flex items-center gap-2 text-red-500 text-sm p-3 bg-red-500/10 rounded-lg">
                  <AlertCircle className="w-4 h-4" />
                  {error}
                </div>
              )}

              {/* File list */}
              <div className="space-y-2">
                {isLoading && files.length === 0 ? (
                  <div className="text-center py-8 text-text-muted">Loading files...</div>
                ) : files.length === 0 ? (
                  <div className="text-center py-8 text-text-muted">
                    {categoryFilter !== "all" || sourceFilter !== "all"
                      ? "No files match your filters"
                      : "No files stored yet"}
                  </div>
                ) : (
                  files.map((file) => (
                    <div
                      key={file.id}
                      onClick={() => setViewingFile(file)}
                      className="p-4 bg-surface rounded-lg border border-input-border hover:border-terminal/30 transition-colors cursor-pointer"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <FolderOpen className="w-4 h-4 text-terminal flex-shrink-0" />
                            <span className="text-sm font-mono font-medium text-text-primary truncate">
                              {file.filename}
                            </span>
                            <span className="text-xs text-text-muted whitespace-nowrap">
                              {formatFileSize(file.size_bytes)}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 ml-6 mb-1 flex-wrap">
                            <span className="text-xs text-text-muted">{file.mime_type}</span>
                            <span className={cn("px-2 py-0.5 rounded text-xs font-mono", CATEGORY_COLORS[file.category] || "bg-gray-500/20 text-gray-400")}>
                              {file.category}
                            </span>
                            <span className={cn("px-2 py-0.5 rounded text-xs font-mono", SOURCE_COLORS[file.source] || "bg-gray-500/20 text-gray-400")}>
                              {file.source}
                            </span>
                          </div>
                          {file.description && (
                            <p className="text-xs text-text-muted ml-6 line-clamp-1">{file.description}</p>
                          )}
                          {file.tags && <div className="mt-1 ml-6">{renderTags(file.tags)}</div>}
                          <div className="mt-2 ml-6 text-xs text-text-muted">
                            Created: {formatDate(file.created_at)}
                          </div>
                        </div>
                        <div className="flex gap-1 shrink-0">
                          <a
                            href={file.download_url}
                            onClick={(e) => e.stopPropagation()}
                            className="p-2 text-text-muted hover:text-terminal transition-colors rounded-lg hover:bg-terminal/10"
                            title="Download"
                          >
                            <Download className="w-4 h-4" />
                          </a>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setDeleteTarget(file);
                            }}
                            className="p-2 text-text-muted hover:text-red-500 transition-colors rounded-lg hover:bg-red-500/10"
                            title="Delete file"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
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
              <h3 className="text-lg font-medium text-text-primary">Delete File</h3>
              <button
                onClick={() => setDeleteTarget(null)}
                className="p-1 text-text-muted hover:text-text-primary transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <p className="text-sm text-text-muted mb-4">
              Are you sure you want to delete this file? This action cannot be undone.
            </p>
            <div className="p-3 bg-surface rounded-lg mb-4">
              <p className="text-sm font-mono font-medium text-text-primary">{deleteTarget.filename}</p>
              <p className="text-xs text-text-muted mt-1">
                {formatFileSize(deleteTarget.size_bytes)} &middot; {deleteTarget.mime_type}
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
