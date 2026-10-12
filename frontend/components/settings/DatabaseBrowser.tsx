"use client";

// Settings → Memory → Databases (2026-09-26, Jarvis polish).
//
// The named databases Edward keeps (a PostgreSQL schema each): a database
// expands to its tables, a table to its columns. Names and types are data,
// so they are set in mono. Deleting drops every table and row, so it asks
// once in a ConfirmSheet. ?name= (a hit in "What does he have on…") opens
// that database.

import React, { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Database, Table2, Trash2 } from "lucide-react";
import {
  deleteDatabase,
  getDatabaseColumns,
  getDatabaseTables,
  listDatabases,
  type DatabaseColumn,
  type DatabaseTable,
  type PersistentDatabase,
} from "@/lib/api";
import { clearUrlParams, readParam } from "@/lib/deepLink";
import { formatRelative } from "@/lib/time";
import { Button, Card, ConfirmSheet, EmptyState, Notice, Pill, Row, Skeleton, useToast } from "@/components/ui";

export interface DatabaseBrowserProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
  /** Tests: the ?name= deep link without a router. */
  initialName?: string | null;
}

type Loaded<T> = T | "loading" | "failed";

export function DatabaseBrowser(props: DatabaseBrowserProps = {}) {
  return (
    <Suspense
      fallback={
        <Card className="p-4">
          <Skeleton lines={3} />
        </Card>
      }
    >
      <DatabasesBody initialName={props.initialName} />
    </Suspense>
  );
}

function DatabasesBody({ initialName }: { initialName?: string | null }) {
  const params = useSearchParams();
  const deepName = initialName ?? readParam(params, "name");
  const { toast } = useToast();
  const [dbs, setDbs] = useState<PersistentDatabase[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [openDb, setOpenDb] = useState<string | null>(null);
  const [tables, setTables] = useState<Record<string, Loaded<DatabaseTable[]>>>({});
  const [openTable, setOpenTable] = useState<string | null>(null);
  const [columns, setColumns] = useState<Record<string, Loaded<DatabaseColumn[]>>>({});
  const [deleting, setDeleting] = useState<PersistentDatabase | null>(null);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      setDbs(await listDatabases());
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggleDb = async (name: string) => {
    if (openDb === name) {
      setOpenDb(null);
      return;
    }
    setOpenDb(name);
    setOpenTable(null);
    if (Array.isArray(tables[name])) return;
    setTables((t) => ({ ...t, [name]: "loading" }));
    try {
      const rows = await getDatabaseTables(name);
      setTables((t) => ({ ...t, [name]: rows }));
    } catch {
      setTables((t) => ({ ...t, [name]: "failed" }));
    }
  };

  // ?name= opens that database once.
  const openedDeep = useRef<string | null>(null);
  useEffect(() => {
    if (!deepName || !dbs || openedDeep.current === deepName) return;
    openedDeep.current = deepName;
    clearUrlParams(["name"]);
    if (dbs.some((d) => d.name === deepName)) void toggleDb(deepName);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepName, dbs]);

  const toggleTable = async (db: string, table: string) => {
    const key = `${db}.${table}`;
    if (openTable === key) {
      setOpenTable(null);
      return;
    }
    setOpenTable(key);
    if (Array.isArray(columns[key])) return;
    setColumns((c) => ({ ...c, [key]: "loading" }));
    try {
      const rows = await getDatabaseColumns(db, table);
      setColumns((c) => ({ ...c, [key]: rows }));
    } catch {
      setColumns((c) => ({ ...c, [key]: "failed" }));
    }
  };

  const remove = async () => {
    if (!deleting) return;
    const db = deleting;
    try {
      await deleteDatabase(db.name);
      setDbs((prev) => (prev ? prev.filter((d) => d.name !== db.name) : prev));
      if (openDb === db.name) setOpenDb(null);
      toast({ title: `Deleted ${db.name}` });
    } catch {
      toast({ title: `I couldn't delete ${db.name}.`, tone: "bad" });
    } finally {
      setDeleting(null);
    }
  };

  if (failed && !dbs) {
    return (
      <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={load}>Try again</Button>}>
        I can&apos;t read my databases right now.
      </Notice>
    );
  }
  if (!dbs) {
    return (
      <Card className="p-4">
        <Skeleton lines={3} />
      </Card>
    );
  }
  if (dbs.length === 0) {
    return (
      <EmptyState icon={<Database />} title="No databases yet.">
        When something is best kept in tables — a training log, monthly weights — I make one and it shows up here.
      </EmptyState>
    );
  }

  return (
    <div className="space-y-4">
      <Card className="px-3 py-0">
        {dbs.map((db) => {
          const open = openDb === db.name;
          const t = tables[db.name];
          return (
            <div key={db.id} className="border-b border-border last:border-b-0">
              <div className="flex items-center gap-1">
                <Row
                  className="min-w-0 flex-1 border-b-0"
                  icon={<Database />}
                  title={<span className="font-mono text-[15px]">{db.name}</span>}
                  detail={[db.description, db.created_at ? `made ${formatRelative(db.created_at)}` : null].filter(Boolean).join(" · ")}
                  expanded={open}
                  onClick={() => toggleDb(db.name)}
                />
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Delete ${db.name}`}
                  className="flex-none text-text-muted hover:text-bad"
                  onClick={() => setDeleting(db)}
                >
                  <Trash2 aria-hidden />
                </Button>
              </div>
              {open ? (
                <div className="pb-3 pl-11">
                  {t === "loading" || t === undefined ? (
                    <Skeleton lines={2} className="py-2" />
                  ) : t === "failed" ? (
                    <Notice tone="bad">I couldn&apos;t read its tables.</Notice>
                  ) : t.length === 0 ? (
                    <p className="py-2 text-footnote text-text-muted">No tables yet.</p>
                  ) : (
                    <div className="rounded-md border border-border bg-primary-bg/40 px-2">
                      {t.map((table) => {
                        const key = `${db.name}.${table.name}`;
                        const cols = columns[key];
                        return (
                          <div key={table.name} className="border-b border-border last:border-b-0">
                            <Row
                              className="border-b-0"
                              icon={<Table2 />}
                              title={<span className="font-mono text-[15px]">{table.name}</span>}
                              detail={`${table.column_count} ${table.column_count === 1 ? "column" : "columns"}`}
                              expanded={openTable === key}
                              onClick={() => toggleTable(db.name, table.name)}
                            />
                            {openTable === key ? (
                              <div className="pb-2 pl-11">
                                {cols === "loading" || cols === undefined ? (
                                  <Skeleton lines={2} className="py-1" />
                                ) : cols === "failed" ? (
                                  <p className="py-1 text-footnote text-bad">I couldn&apos;t read its columns.</p>
                                ) : (
                                  <ul className="space-y-1.5 py-1">
                                    {cols.map((c) => (
                                      <li key={c.name} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-footnote">
                                        <span className="font-mono text-text-primary">{c.name}</span>
                                        <Pill className="font-mono">{c.data_type}</Pill>
                                        {c.is_nullable === "YES" ? <span className="text-caption text-text-muted">optional</span> : null}
                                        {c.column_default ? (
                                          <span className="max-w-[12rem] truncate font-mono text-caption text-text-muted" title={c.column_default}>
                                            = {c.column_default}
                                          </span>
                                        ) : null}
                                      </li>
                                    ))}
                                  </ul>
                                )}
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              ) : null}
            </div>
          );
        })}
      </Card>

      <ConfirmSheet
        open={Boolean(deleting)}
        onClose={() => setDeleting(null)}
        onConfirm={remove}
        title={`Delete “${deleting?.name ?? ""}”?`}
        body="This drops the database and every table and row in it. It can't be undone."
        confirmLabel="Delete"
      />
    </div>
  );
}
