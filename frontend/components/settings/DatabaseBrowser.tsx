"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ChevronDown,
  ChevronRight,
  Database,
  Trash2,
  RefreshCw,
  AlertCircle,
  X,
  Table2,
} from "lucide-react";
import {
  listDatabases,
  getDatabaseTables,
  getDatabaseColumns,
  deleteDatabase,
  PersistentDatabase,
  DatabaseTable,
  DatabaseColumn,
} from "@/lib/api";
import { cn } from "@/lib/utils";

interface DatabaseBrowserProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function DatabaseBrowser({ isExpanded: initialExpanded = false, hideHeader = false }: DatabaseBrowserProps) {
  const [isExpanded, setIsExpanded] = useState(initialExpanded);
  const [databases, setDatabases] = useState<PersistentDatabase[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Inline expansion for tables
  const [expandedDb, setExpandedDb] = useState<string | null>(null);
  const [tables, setTables] = useState<DatabaseTable[]>([]);
  const [loadingTables, setLoadingTables] = useState(false);

  // Inline expansion for columns
  const [expandedTable, setExpandedTable] = useState<string | null>(null);
  const [columns, setColumns] = useState<DatabaseColumn[]>([]);
  const [loadingColumns, setLoadingColumns] = useState(false);

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<PersistentDatabase | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const loadDatabases = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await listDatabases();
      setDatabases(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load databases");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isExpanded) {
      loadDatabases();
    }
  }, [isExpanded, loadDatabases]);

  const handleExpandDb = async (dbName: string) => {
    if (expandedDb === dbName) {
      setExpandedDb(null);
      setExpandedTable(null);
      return;
    }
    setExpandedDb(dbName);
    setExpandedTable(null);
    setLoadingTables(true);
    try {
      const result = await getDatabaseTables(dbName);
      setTables(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load tables");
    } finally {
      setLoadingTables(false);
    }
  };

  const handleExpandTable = async (tableName: string) => {
    if (!expandedDb) return;
    if (expandedTable === tableName) {
      setExpandedTable(null);
      return;
    }
    setExpandedTable(tableName);
    setLoadingColumns(true);
    try {
      const result = await getDatabaseColumns(expandedDb, tableName);
      setColumns(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load columns");
    } finally {
      setLoadingColumns(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await deleteDatabase(deleteTarget.name);
      setDeleteTarget(null);
      if (expandedDb === deleteTarget.name) setExpandedDb(null);
      loadDatabases();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete database");
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

  return (
    <div className={cn("border border-input-border rounded-lg overflow-hidden", !hideHeader && "mt-8")}>
      {!hideHeader && (
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full px-4 py-3 flex items-center justify-between bg-surface hover:bg-surface/80 transition-colors"
        >
          <div className="flex items-center gap-2 text-text-primary">
            <Database className="w-4 h-4 text-terminal" />
            <span className="font-medium text-sm">Persistent Databases</span>
            {databases.length > 0 && (
              <span className="text-xs text-text-muted">({databases.length})</span>
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
          {/* Header */}
          <div className="flex justify-end">
            <button
              onClick={loadDatabases}
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

          {/* Database list */}
          <div className="space-y-2">
            {isLoading && databases.length === 0 ? (
              <div className="text-center py-8 text-text-muted">Loading databases...</div>
            ) : databases.length === 0 ? (
              <div className="text-center py-8 text-text-muted">
                No persistent databases created yet
              </div>
            ) : (
              databases.map((db) => (
                <div key={db.id}>
                  <div
                    onClick={() => handleExpandDb(db.name)}
                    className="p-4 bg-surface rounded-lg border border-input-border hover:border-terminal/30 transition-colors cursor-pointer"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          {expandedDb === db.name ? (
                            <ChevronDown className="w-4 h-4 text-terminal flex-shrink-0" />
                          ) : (
                            <ChevronRight className="w-4 h-4 text-terminal flex-shrink-0" />
                          )}
                          <span className="text-sm font-mono font-medium text-text-primary">
                            {db.name}
                          </span>
                        </div>
                        {db.description && (
                          <p className="text-xs text-text-muted ml-6 mb-1">{db.description}</p>
                        )}
                        <div className="mt-2 ml-6 flex gap-3 text-xs text-text-muted">
                          <span>Created: {formatDate(db.created_at)}</span>
                          {db.last_accessed && (
                            <span>Last accessed: {formatDate(db.last_accessed)}</span>
                          )}
                        </div>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setDeleteTarget(db);
                        }}
                        className="p-2 text-text-muted hover:text-red-500 transition-colors rounded-lg hover:bg-red-500/10"
                        title="Delete database"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>

                  {/* Inline table list */}
                  {expandedDb === db.name && (
                    <div className="ml-6 mt-1 mb-2 p-3 bg-surface/50 rounded-lg border border-input-border/50">
                      {loadingTables ? (
                        <p className="text-xs text-text-muted">Loading tables...</p>
                      ) : tables.length === 0 ? (
                        <p className="text-xs text-text-muted">No tables in this database</p>
                      ) : (
                        <div className="space-y-1">
                          <p className="text-xs text-text-muted mb-2">
                            {tables.length} table{tables.length !== 1 ? "s" : ""}
                          </p>
                          {tables.map((table) => (
                            <div key={table.name}>
                              <div
                                onClick={() => handleExpandTable(table.name)}
                                className="flex items-center gap-2 px-2 py-1.5 rounded bg-primary-bg hover:bg-primary-bg/80 cursor-pointer transition-colors"
                              >
                                {expandedTable === table.name ? (
                                  <ChevronDown className="w-3 h-3 text-terminal flex-shrink-0" />
                                ) : (
                                  <ChevronRight className="w-3 h-3 text-terminal flex-shrink-0" />
                                )}
                                <Table2 className="w-3 h-3 text-terminal flex-shrink-0" />
                                <span className="text-xs font-mono text-text-primary flex-1">
                                  {table.name}
                                </span>
                                <span className="text-xs text-text-muted">
                                  {table.column_count} col{table.column_count !== 1 ? "s" : ""}
                                </span>
                              </div>
                              {expandedTable === table.name && (
                                <div className="ml-8 mt-1 mb-2 space-y-0.5">
                                  {loadingColumns ? (
                                    <p className="text-xs text-text-muted py-1">Loading columns...</p>
                                  ) : columns.length === 0 ? (
                                    <p className="text-xs text-text-muted py-1">No columns</p>
                                  ) : (
                                    columns.map((col) => (
                                      <div
                                        key={col.name}
                                        className="flex items-center gap-2 px-2 py-1 rounded bg-surface/50 text-xs"
                                      >
                                        <span className="font-mono text-text-primary">{col.name}</span>
                                        <span className="px-1.5 py-0.5 rounded bg-terminal/10 text-terminal font-mono text-[10px]">
                                          {col.data_type}
                                        </span>
                                        {col.is_nullable === "YES" && (
                                          <span className="text-text-muted/60 text-[10px]">nullable</span>
                                        )}
                                        {col.column_default && (
                                          <span className="text-text-muted/60 text-[10px] truncate max-w-[120px]" title={col.column_default}>
                                            = {col.column_default}
                                          </span>
                                        )}
                                      </div>
                                    ))
                                  )}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Delete confirmation modal */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-primary-bg border border-input-border rounded-lg p-6 max-w-md mx-4 shadow-xl">
            <div className="flex items-start justify-between mb-4">
              <h3 className="text-lg font-medium text-text-primary">Delete Database</h3>
              <button
                onClick={() => setDeleteTarget(null)}
                className="p-1 text-text-muted hover:text-text-primary transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <p className="text-sm text-text-muted mb-2">
              Are you sure you want to delete this database? This will permanently drop the
              PostgreSQL schema and <strong className="text-red-400">all tables and data within it</strong>.
              This action cannot be undone.
            </p>
            <div className="p-3 bg-surface rounded-lg mb-4">
              <p className="text-sm font-mono font-medium text-text-primary">{deleteTarget.name}</p>
              {deleteTarget.description && (
                <p className="text-xs text-text-muted mt-1">{deleteTarget.description}</p>
              )}
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
                {isDeleting ? "Deleting..." : "Delete Database"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
