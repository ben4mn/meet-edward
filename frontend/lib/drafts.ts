// Per-conversation composer drafts (2026-09-26).
//
// What the owner types in one thread stays with that thread: switch away and
// back and the half-written message is still in the box. sessionStorage,
// keyed like the composer queue (lib/composerQueue.ts queueKey: a brand-new
// chat is "__new__"), so a reload keeps it and a new app session starts
// clean. Every storage call is guarded: private mode, blocked site data and
// a full quota all degrade to "no draft", never to an error.

import { queueKey } from "./composerQueue";

export const DRAFT_PREFIX = "edward:draft:";
/** A pasted novel is kept up to here; past it the tail is dropped rather than failing the write. */
export const MAX_DRAFT_CHARS = 20_000;

export interface DraftStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function draftKey(conversationId: string | null | undefined): string {
  return DRAFT_PREFIX + queueKey(conversationId || undefined);
}

function defaultStorage(): DraftStorage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

function pick(storage: DraftStorage | null | undefined): DraftStorage | null {
  return storage === undefined ? defaultStorage() : storage;
}

export function readDraft(conversationId: string | null | undefined, storage?: DraftStorage | null): string {
  const s = pick(storage);
  if (!s) return "";
  try {
    return s.getItem(draftKey(conversationId)) ?? "";
  } catch {
    return "";
  }
}

export function writeDraft(conversationId: string | null | undefined, text: string, storage?: DraftStorage | null): void {
  const s = pick(storage);
  if (!s) return;
  const key = draftKey(conversationId);
  try {
    if (!text.trim()) s.removeItem(key);
    else s.setItem(key, text.length > MAX_DRAFT_CHARS ? text.slice(0, MAX_DRAFT_CHARS) : text);
  } catch {
    /* private mode or quota: the draft simply is not kept */
  }
}

export function clearDraft(conversationId: string | null | undefined, storage?: DraftStorage | null): void {
  const s = pick(storage);
  if (!s) return;
  try {
    s.removeItem(draftKey(conversationId));
  } catch {
    /* nothing to clear */
  }
}

/**
 * The conversation on screen changed from `prev` to `next`. A brand-new
 * chat learning its id from its own first stream event keeps the box as it
 * is and files it under the new id — "rekey". That is the stream's id being
 * *assigned* in this very render: it names `next` now and named nothing the
 * last time the composer looked (`streamingBefore`). A new chat opening a
 * thread that was already streaming (the owner asked in X, tapped New chat, typed,
 * tapped X) is an ordinary "switch" — the new-chat text stays with the new
 * chat and X gets its own draft back (2026-09-26 review). Anything else is
 * a "switch" too.
 */
export function draftTransition(
  prev: string | null | undefined,
  next: string | null | undefined,
  streamingNow: string | null | undefined,
  streamingBefore: string | null | undefined,
): "none" | "rekey" | "switch" {
  if ((prev || undefined) === (next || undefined)) return "none";
  if (!prev && next && streamingNow === next && !streamingBefore) return "rekey";
  return "switch";
}
