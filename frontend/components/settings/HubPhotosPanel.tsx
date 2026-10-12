"use client";

// Settings → House → Hub photos (2026-09-26, UX polish).
//
// The screensaver album for the hub at the pet station. The album is a
// directory on the host Mac (EDWARD_PHOTOS_DIR); this panel lets anyone
// fill it from a phone.
// Two things it stays honest about: HEIC can't be drawn by the hub's
// browser (so it is converted on the way in, and what is already in the
// folder that can't be shown is counted), and a photo that fails to upload
// says which one and why. Uploads are sequential on purpose (twenty
// parallel POSTs from across the house is how photos go missing).
// Deleting is undoable: the photo leaves the grid at once and the file
// goes when the Undo window closes.

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Plus, RefreshCw, Trash2 } from "lucide-react";
import {
  deleteHubPhoto,
  hubPhotoUrl,
  listHubPhotos,
  uploadHubPhoto,
  type HubPhotosResponse,
  type HubUploadResult,
} from "@/lib/api";
import { pendingDeletes } from "@/lib/pendingDeletes";
import { useBuildReloadHold } from "@/lib/buildReload";
import { scheduleUndoableDelete } from "@/lib/undoableDelete";
import { Button, Card, EmptyState, Notice, SectionLabel, Skeleton, useToast } from "@/components/ui";

export interface HubPhotosPanelProps {
  isExpanded?: boolean;
  hideHeader?: boolean;
}

// `image/*` alone hides HEIC on some Android pickers; iOS needs the
// explicit types to offer originals.
const ACCEPT = "image/*,.heic,.HEIC,.heif,.HEIF";
const deleteKey = (name: string) => `hub-photo:${name}`;

function useHiddenIds(): ReadonlySet<string> {
  const [snap, setSnap] = useState(pendingDeletes.pending());
  useEffect(() => pendingDeletes.subscribe(() => setSnap(pendingDeletes.pending())), []);
  return snap;
}

export function HubPhotosPanel(_props: HubPhotosPanelProps = {}) {
  const { toast } = useToast();
  const hidden = useHiddenIds();
  const [data, setData] = useState<HubPhotosResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [rejected, setRejected] = useState<HubUploadResult[]>([]);
  const [added, setAdded] = useState<{ count: number; converted: number } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // One file per request over the tunnel: twenty photos take minutes with
  // no tap, and a new deploy's reload cut the batch off and dropped the
  // rest of the picked files (2026-09-27 review; lib/buildReload.ts).
  useBuildReloadHold(progress !== null);

  const load = useCallback(async () => {
    setFailed(false);
    try {
      setData(await listHubPhotos());
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const upload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const list = Array.from(files);
    setRejected([]);
    setAdded(null);
    setProgress({ done: 0, total: list.length });
    let count = 0;
    let converted = 0;
    const failures: HubUploadResult[] = [];
    for (let i = 0; i < list.length; i++) {
      try {
        const res = await uploadHubPhoto(list[i]);
        count += res.saved_count;
        converted += res.converted_count;
        failures.push(...res.rejected);
      } catch (e) {
        failures.push({ ok: false, name: list[i].name, reason: e instanceof Error ? e.message : "upload failed" });
      }
      setProgress({ done: i + 1, total: list.length });
    }
    setAdded({ count, converted });
    setRejected(failures);
    setProgress(null);
    if (inputRef.current) inputRef.current.value = "";
    await load();
  };

  const remove = (name: string) => {
    scheduleUndoableDelete({
      key: deleteKey(name),
      commit: () => deleteHubPhoto(name).then(load),
      onCommit: () => setData((d) => (d ? { ...d, photos: d.photos.filter((p) => p !== name) } : d)),
      onError: () => {
        toast({ title: `I couldn't delete ${name}. It's back.`, tone: "bad" });
        void load();
      },
      toast,
      title: "Photo removed",
      body: name,
      tooLate: "Too late — that photo is already removed.",
    });
  };

  if (failed && !data) {
    return (
      <Notice tone="bad" action={<Button size="sm" variant="secondary" onClick={load}>Try again</Button>}>
        I can&apos;t open the album right now.
      </Notice>
    );
  }
  if (!data) {
    return (
      <div className="space-y-4" aria-hidden>
        <Skeleton lines={2} />
        <div className="grid grid-cols-3 gap-2">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="aspect-square rounded-md bg-surface-3 motion-safe:animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  const photos = data.photos.filter((p) => !hidden.has(deleteKey(p)));
  const uploading = progress !== null;

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          className="hidden"
          onChange={(e) => upload(e.target.files)}
        />
        <div className="flex gap-2">
          <Button
            className="flex-1"
            icon={<Plus aria-hidden />}
            loading={uploading}
            onClick={() => inputRef.current?.click()}
          >
            {uploading ? `Uploading ${progress.done} of ${progress.total}…` : "Add photos"}
          </Button>
          <Button variant="secondary" size="icon" aria-label="Refresh the album" onClick={load} disabled={uploading}>
            <RefreshCw aria-hidden />
          </Button>
        </div>
        <p className="text-footnote text-text-muted">
          The hub shows these when it goes idle. iPhone photos (HEIC) are turned into JPEG on the way in — the hub
          can&apos;t draw HEIC.
        </p>
      </div>

      {added && added.count > 0 ? (
        <Notice tone="ok">
          Added {added.count} photo{added.count === 1 ? "" : "s"}
          {added.converted > 0 ? ` (${added.converted} converted from HEIC)` : ""}.
        </Notice>
      ) : null}

      {rejected.length > 0 ? (
        <Notice tone="warn" title={`${rejected.length} not added`}>
          <ul className="mt-1 space-y-0.5">
            {rejected.map((r, i) => (
              <li key={`${r.name}-${i}`} className="[overflow-wrap:anywhere]">
                <span className="font-mono">{r.name}</span> — {r.reason}
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}

      <section aria-label="In the album" className="space-y-2">
        <SectionLabel action={String(photos.length)}>In the album</SectionLabel>
        {data.skipped_unsupported > 0 ? (
          <p className="px-0.5 text-footnote text-text-muted">
            {data.skipped_unsupported} file{data.skipped_unsupported === 1 ? "" : "s"} in the folder can&apos;t be shown
            (HEIC or another format the hub can&apos;t draw).
          </p>
        ) : null}
        {photos.length === 0 ? (
          <Card>
            <EmptyState title="Nothing here yet.">The hub shows the clock on black until you add some.</EmptyState>
          </Card>
        ) : (
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6">
            {photos.map((name) => (
              <div key={name} className="group relative aspect-square overflow-hidden rounded-md border border-border bg-surface-3">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={hubPhotoUrl(name)} alt="" loading="lazy" className="h-full w-full object-cover" />
                <button
                  type="button"
                  onClick={() => remove(name)}
                  aria-label={`Delete ${name}`}
                  className="absolute right-0 top-0 flex h-11 w-11 items-center justify-center opacity-0 transition-opacity duration-fast focus-visible:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
                >
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-black/60 text-white shadow-e1 backdrop-blur-sm">
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                  </span>
                </button>
              </div>
            ))}
          </div>
        )}
        <p className="break-all px-0.5 font-mono text-caption text-text-muted">
          {data.dir}
          {!data.dir_present ? " — the folder is missing; the first upload creates it" : ""}
        </p>
      </section>
    </div>
  );
}
