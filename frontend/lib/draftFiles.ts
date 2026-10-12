// Per-conversation composer attachments (L2, 2026-09-27 follow-ups).
//
// Typed drafts stay with their thread (lib/drafts.ts, sessionStorage), but
// the composer used to drop its attachments whenever the thread changed —
// a photo the owner attached vanished the moment they glanced at another thread.
// A File cannot go into sessionStorage, so attachments are kept here, in
// memory, keyed like drafts and the composer queue (a brand-new chat is
// "__new__"): they survive switching threads and leaving /chat and coming
// back, and are gone with the tab, a sign-out or the thread's delete.
//
// And a page load (pre-deploy review, 2026-09-27): after a deploy Next
// answers any navigation that fetches the server with a full page load,
// which dropped them whatever AppBuildReload held. Every change is mirrored
// into this tab's stash (lib/tabStash.ts) and the next load puts them back
// (restoreDraftFiles, at startup); the composer on screen picks its own up
// (subscribeDraftFiles).

import { queueKey } from "./composerQueue";
import { SIGNED_OUT_EVENT } from "./authSession";
import { loadStash, saveStash } from "./tabStash";

const held = new Map<string, readonly File[]>();
/** Deleted threads: nothing is kept for them again (ids are never reused). */
const forgotten = new Set<string>();

function key(conversationId: string | null | undefined): string {
  return queueKey(conversationId || undefined);
}

// ---- the stash --------------------------------------------------------------

/** The stash is written only once this load's restore has read it back:
 *  an early write (the composer mounting empty) would overwrite it. */
let restored = false;
let pendingWrite = false;
const listeners = new Set<() => void>();

function persist(): void {
  if (!restored) {
    pendingWrite = true;
    return;
  }
  void saveStash("files", Array.from(held.entries()).map(([k, files]) => [k, Array.from(files)]));
}

function fileKey(f: File): string {
  return `${f.name}|${f.size}|${f.lastModified}`;
}

function mergeFiles(first: readonly File[], then: readonly File[]): File[] {
  const seen = new Set<string>();
  const out: File[] = [];
  for (const f of [...first, ...then]) {
    const k = fileKey(f);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(f);
  }
  return out;
}

/** Put back what this tab kept before the page loaded, beside anything
 *  attached since; then let the composer know. */
export async function restoreDraftFiles(): Promise<void> {
  const saved = await loadStash<unknown>("files");
  let changed = false;
  if (Array.isArray(saved)) {
    for (const entry of saved) {
      if (!Array.isArray(entry) || typeof entry[0] !== "string" || !Array.isArray(entry[1])) continue;
      const [k, files] = entry as [string, unknown[]];
      const valid = files.filter((f): f is File => typeof File !== "undefined" && f instanceof File);
      if (!valid.length || forgotten.has(k)) continue;
      held.set(k, mergeFiles(valid, held.get(k) ?? []));
      changed = true;
    }
  }
  restored = true;
  if (changed || pendingWrite) persist();
  pendingWrite = false;
  if (changed) for (const fn of Array.from(listeners)) fn();
}

/** Called when kept attachments come back after a page load. */
export function subscribeDraftFiles(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** What a page load does to this module (tests): memory is gone, the stash stays. */
export function dropDraftFilesInMemory(): void {
  held.clear();
  forgotten.clear();
  restored = false;
  pendingWrite = false;
}

export function readDraftFiles(conversationId: string | null | undefined): File[] {
  return [...(held.get(key(conversationId)) ?? [])];
}

export function writeDraftFiles(conversationId: string | null | undefined, files: readonly File[]): void {
  const k = key(conversationId);
  const before = held.get(k);
  if (files.length && !forgotten.has(k)) held.set(k, [...files]);
  else held.delete(k);
  if (before !== undefined || held.has(k)) persist();
}

export function clearDraftFiles(conversationId: string | null | undefined): void {
  if (held.delete(key(conversationId))) persist();
}

/** A new chat learned its id (drafts.draftTransition "rekey"): its files follow. */
export function rekeyDraftFiles(from: string | null | undefined, to: string | null | undefined): void {
  const files = held.get(key(from));
  held.delete(key(from));
  if (files?.length) writeDraftFiles(to, files);
  else if (files) persist();
}

/** The thread was deleted (its delete committed): drop what it kept, and
 *  never keep anything for it again. Only a send used to clear a thread's
 *  files, so a photo left in a thread the owner then deleted stayed here with no
 *  way to reach it — and, since a reload would lose it, that tab never
 *  picked up another build (2026-09-27 review). When the deleted thread is
 *  on screen the composer's move to a new chat writes its files back under
 *  the old id, after this runs; the tombstone refuses that write. */
export function forgetDraftFiles(conversationId: string): void {
  if (!conversationId) return;
  const k = key(conversationId);
  forgotten.add(k);
  if (held.delete(k)) persist();
}

/** Some thread keeps attachments. They live only here, so a reload onto a
 *  new build would drop them (components/layout/AppBuildReload.tsx waits). */
export function hasDraftFiles(): boolean {
  return held.size > 0;
}

/** Forget every thread's attachments (sign-out; tests). The stash is
 *  cleared by the sign-out itself (lib/tabStash.ts). */
export function resetDraftFiles(): void {
  held.clear();
  forgotten.clear();
}

if (typeof window !== "undefined") {
  // This load's restore: what the page held before it loaded.
  void restoreDraftFiles();
  // Sign-out clears what the tab holds of the owner's (lib/authSession.ts
  // clearSessionContent: the typed drafts). Signing out is a client
  // navigation, so this map outlived it and the next sign-in put the files
  // back in the composer (2026-09-27 review). AuthContext.logout dispatches
  // the event synchronously, before the page leaves.
  window.addEventListener(SIGNED_OUT_EVENT, resetDraftFiles);
}
