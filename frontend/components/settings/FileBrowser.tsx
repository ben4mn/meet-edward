"use client";

// Settings → Memory → Files (2026-09-26, Jarvis polish).
//
// What Edward stored: uploads, what he made in his sandbox, memos. A kind
// filter (images / documents / audio), whole-row taps, image thumbnails.
// A file opens in a sheet with a preview where the phone can show one, a
// download and the conversation it came from; ?id= opens one directly.
// Deleting is undoable. Words live in lib/storageView.ts.

import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Download, File as FileIcon, FileAudio, FileText, Image as ImageIcon, Table2 } from "lucide-react";
import { deleteFile, listFiles, type StoredFileItem } from "@/lib/api";
import { getStoredFile } from "@/lib/memoryHouseApi";
import {
  fileKind,
  fileKindLabel,
  fileMatches,
  fileMeta,
  formatFileSize,
  madeBy,
  type FileFilter,
} from "@/lib/storageView";
import { tagList } from "@/lib/documentView";
import { clearUrlParams, readParam } from "@/lib/deepLink";
import { pendingDeletes } from "@/lib/pendingDeletes";
import { scheduleUndoableDelete } from "@/lib/undoableDelete";
import { formatDateTime } from "@/lib/time";
import { useUserTimezone } from "@/lib/useTimezone";
import {
  Button,
  Card,
  EmptyState,
  Notice,
  Pill,
  Segment,
  Sheet,
  Skeleton,
  buttonClasses,
  cn,
  useToast,
} from "@/components/ui";
import { DetailList, type DetailRow } from "./panelBits";

const PAGE_SIZE = 50;
const deleteKey = (id: string) => `file:${id}`;

export interface FileBrowserProps {
  /** Tests; the page reads ?id=. */
  initialId?: string | null;
  isExpanded?: boolean;
  hideHeader?: boolean;
}

export function FileBrowser(props: FileBrowserProps) {
  return (
    <Suspense fallback={<Skeleton lines={4} />}>
      <FilesBody initialId={props.initialId} />
    </Suspense>
  );
}

function useHiddenIds(): ReadonlySet<string> {
  const [snap, setSnap] = useState(pendingDeletes.pending());
  useEffect(() => pendingDeletes.subscribe(() => setSnap(pendingDeletes.pending())), []);
  return snap;
}

function FilesBody({ initialId }: { initialId?: string | null }) {
  const params = useSearchParams();
  const deepId = initialId ?? readParam(params, "id");
  const tz = useUserTimezone();
  const { toast } = useToast();
  const hidden = useHiddenIds();
  const [filter, setFilter] = useState<FileFilter>("all");
  const [files, setFiles] = useState<StoredFileItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [failed, setFailed] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      const res = await listFiles(undefined, undefined, PAGE_SIZE, 0);
      setFiles(res.files);
      setTotal(res.pagination.total);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const more = async () => {
    if (!files) return;
    setLoadingMore(true);
    try {
      const res = await listFiles(undefined, undefined, PAGE_SIZE, files.length);
      setFiles((prev) => {
        const seen = new Set((prev ?? []).map((f) => f.id));
        return [...(prev ?? []), ...res.files.filter((f) => !seen.has(f.id))];
      });
      setTotal(res.pagination.total);
    } catch {
      toast({ title: "I couldn't load more just now.", tone: "bad" });
    } finally {
      setLoadingMore(false);
    }
  };

  const openedDeep = useRef<string | null>(null);
  useEffect(() => {
    if (deepId && openedDeep.current !== deepId) {
      openedDeep.current = deepId;
      setOpenId(deepId);
    }
  }, [deepId]);

  const close = () => {
    setOpenId(null);
    clearUrlParams(["id"]);
  };

  const remove = (f: StoredFileItem) => {
    close();
    scheduleUndoableDelete({
      key: deleteKey(f.id),
      commit: () => deleteFile(f.id),
      onCommit: () => {
        setFiles((fs) => (fs ? fs.filter((x) => x.id !== f.id) : fs));
        setTotal((t) => Math.max(0, t - 1));
      },
      onError: () => {
        toast({ title: "I couldn't delete that file. It's back.", tone: "bad" });
        void load();
      },
      toast,
      title: "File deleted",
      body: f.filename,
      tooLate: "Too late — that file is already deleted.",
    });
  };

  const visible = useMemo(
    () => (files ?? []).filter((f) => !hidden.has(deleteKey(f.id)) && fileMatches(f, filter)),
    [files, hidden, filter],
  );

  return (
    <div className="space-y-4">
      <Segment<FileFilter>
        label="Kind"
        value={filter}
        onChange={setFilter}
        options={[
          { value: "all", label: "All" },
          { value: "images", label: "Images" },
          { value: "docs", label: "Docs" },
          { value: "audio", label: "Audio" },
        ]}
      />

      {failed ? (
        <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={load}>Try again</Button>}>
          I can&apos;t open my files right now.
        </Notice>
      ) : null}

      {files === null && !failed ? (
        <Card className="p-4">
          <Skeleton lines={4} />
        </Card>
      ) : files && visible.length === 0 ? (
        <EmptyState title={filter === "all" ? "No files yet." : "None of that kind."}>
          {filter === "all"
            ? "Things you send me and things I make — a budget, a chart — are kept here."
            : "Try another kind."}
        </EmptyState>
      ) : files ? (
        <Card className="px-3 py-0">
          {visible.map((f) => (
            <FileRow key={f.id} file={f} tz={tz} onOpen={() => setOpenId(f.id)} />
          ))}
        </Card>
      ) : null}

      {files && files.length < total ? (
        <Button variant="secondary" className="w-full" loading={loadingMore} onClick={more}>
          Show more
        </Button>
      ) : null}

      <FileSheet
        id={openId}
        fromList={(files ?? []).find((f) => f.id === openId) ?? null}
        tz={tz}
        onClose={close}
        onDelete={remove}
      />
    </div>
  );
}

function KindIcon({ mime, filename }: { mime: string; filename: string }) {
  const k = fileKind(mime, filename);
  const Icon = k === "image" ? ImageIcon : k === "audio" || k === "video" ? FileAudio : k === "data" ? Table2 : k === "other" ? FileIcon : FileText;
  return <Icon className="h-4 w-4" aria-hidden />;
}

function FileRow({ file: f, tz, onOpen }: { file: StoredFileItem; tz: string; onOpen: () => void }) {
  const isImage = fileKind(f.mime_type, f.filename) === "image";
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex min-h-[56px] w-full items-center gap-3 border-b border-border py-2.5 text-left transition-colors duration-fast last:border-b-0 hover:bg-surface-3/50 active:bg-surface-3/80"
    >
      <span className="flex h-10 w-10 flex-none items-center justify-center overflow-hidden rounded-sm bg-surface-3 text-text-secondary">
        {isImage ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={f.download_url} alt="" loading="lazy" className="h-full w-full object-cover" />
        ) : (
          <KindIcon mime={f.mime_type} filename={f.filename} />
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-callout text-text-primary">{f.filename}</span>
        {f.description ? <span className="block truncate text-footnote text-text-secondary">{f.description}</span> : null}
        <span className="block truncate text-caption text-text-muted">{fileMeta(f, { tz })}</span>
      </span>
    </button>
  );
}

function FileSheet({
  id,
  fromList,
  tz,
  onClose,
  onDelete,
}: {
  id: string | null;
  fromList: StoredFileItem | null;
  tz: string;
  onClose: () => void;
  onDelete: (f: StoredFileItem) => void;
}) {
  const [fetched, setFetched] = useState<StoredFileItem | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    setMissing(false);
    if (!id || fromList) {
      setFetched(null);
      return;
    }
    let alive = true;
    getStoredFile(id)
      .then((f) => alive && setFetched(f))
      .catch(() => alive && setMissing(true));
    return () => {
      alive = false;
    };
  }, [id, fromList]);

  const f = fromList ?? fetched;
  const kind = f ? fileKind(f.mime_type, f.filename) : null;
  const rows: DetailRow[] = [];
  if (f) {
    rows.push({ label: "Kind", value: fileKindLabel(f.mime_type, f.filename) });
    rows.push({ label: "Size", value: formatFileSize(f.size_bytes) });
    rows.push({ label: "From", value: madeBy(f.source) === "you" ? "You" : "Edward" });
    if (f.created_at) rows.push({ label: "Added", value: formatDateTime(f.created_at, { tz }) });
    if (f.access_count) rows.push({ label: "Opened", value: `${f.access_count} ${f.access_count === 1 ? "time" : "times"}` });
  }
  const tags = f ? tagList(f.tags) : [];

  return (
    <Sheet
      open={Boolean(id)}
      onClose={onClose}
      title={f?.filename ?? (missing ? "File not found" : "One moment")}
      description={f?.description ?? undefined}
      footer={
        f ? (
          <div className="flex gap-2">
            <Button variant="ghost" className="text-bad hover:text-bad" onClick={() => onDelete(f)}>
              Delete
            </Button>
            <div className="flex-1" />
            <Button variant="secondary" onClick={onClose}>
              Done
            </Button>
          </div>
        ) : null
      }
    >
      {missing ? (
        <p className="pb-4 text-body text-text-secondary">I can&apos;t find that file. It may have been deleted.</p>
      ) : !f ? (
        <Skeleton lines={3} className="pb-4" />
      ) : (
        <div className="space-y-4 pb-2">
          {kind === "image" ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={f.download_url} alt={f.description ?? f.filename} className="max-h-[50dvh] w-full rounded-md border border-border object-contain" />
          ) : kind === "audio" ? (
            <audio controls src={f.download_url} className="w-full" preload="metadata" />
          ) : null}
          <DetailList rows={rows} />
          {tags.length ? (
            <div className="flex flex-wrap gap-1.5">
              {tags.map((t) => (
                <Pill key={t}>#{t}</Pill>
              ))}
            </div>
          ) : null}
          <div className={cn("grid grid-cols-1 gap-2", f.source_conversation_id && "sm:grid-cols-2")}>
            <a href={f.download_url} download={f.filename} className={buttonClasses({ variant: "secondary" })}>
              <Download aria-hidden />
              Download
            </a>
            {f.source_conversation_id ? (
              <Button variant="secondary" href={`/chat?c=${encodeURIComponent(f.source_conversation_id)}`}>
                Open the conversation
              </Button>
            ) : null}
          </div>
        </div>
      )}
    </Sheet>
  );
}
