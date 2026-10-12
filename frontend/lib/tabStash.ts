// What only memory holds, kept across a page load in this tab (pre-deploy
// review, 2026-09-27).
//
// Attachments kept for a thread (lib/draftFiles.ts) and queued messages
// (the ChatProvider's queues) are Files and live objects, so they could not
// go into sessionStorage the way typed drafts do, and a page load dropped
// them. AppBuildReload held a deploy's reload while there were any, but
// Next 14.1 answers any navigation that fetches the server after a deploy
// with a full page load of its own (a build-id mismatch), so the hold only
// kept the old bundle running a little longer before the loss.
//
// So they are mirrored here: IndexedDB (a File survives a structured
// clone), keyed by this tab's id (sessionStorage, which a page load keeps
// and another tab does not share), each entry stamped and forgotten after a
// day, and cleared by a sign-out. Best effort: without IndexedDB (a test,
// a browser that refuses site data) every call is a quiet no-op and the
// holds in AppBuildReload are what is left.

import { SIGNED_OUT_EVENT } from "./authSession";

export type StashKind = "files" | "queues";

/** A day: long enough for a deploy and an evening, short enough not to linger. */
export const STASH_MAX_AGE_MS = 24 * 60 * 60_000;
export const TAB_ID_KEY = "edward:tab:v1";

const DB_NAME = "edward-tab-stash";
const STORE = "stash";

export interface StashBackend {
  get(key: string): Promise<unknown>;
  put(key: string, value: unknown): Promise<void>;
  delete(key: string): Promise<void>;
  keys(): Promise<string[]>;
}

interface Stamped {
  at: number;
  value: unknown;
}

function isStamped(v: unknown): v is Stamped {
  return !!v && typeof v === "object" && typeof (v as Stamped).at === "number" && "value" in (v as object);
}

/** An in-memory backend (tests; anywhere IndexedDB is missing on purpose). */
export function memoryStashBackend(): StashBackend & { data: Map<string, unknown> } {
  const data = new Map<string, unknown>();
  return {
    data,
    get: async (k) => data.get(k),
    put: async (k, v) => void data.set(k, v),
    delete: async (k) => void data.delete(k),
    keys: async () => Array.from(data.keys()),
  };
}

function indexedDbBackend(): StashBackend | null {
  let factory: IDBFactory | undefined;
  try {
    factory = typeof indexedDB !== "undefined" ? indexedDB : undefined;
  } catch {
    return null;
  }
  if (!factory) return null;
  const idb = factory;
  let opening: Promise<IDBDatabase> | null = null;
  const open = () =>
    (opening ??= new Promise<IDBDatabase>((resolve, reject) => {
      const req = idb.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
      req.onblocked = () => reject(new Error("tab stash blocked"));
    }));
  // Transactions on one store run in the order they are created, and the
  // `then`s below are created in call order: a save is never overtaken.
  const run = <T,>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>) =>
    open().then(
      (db) =>
        new Promise<T>((resolve, reject) => {
          const tx = db.transaction(STORE, mode);
          const req = fn(tx.objectStore(STORE));
          tx.oncomplete = () => resolve(req.result);
          tx.onerror = () => reject(tx.error);
          tx.onabort = () => reject(tx.error);
        }),
    );
  return {
    get: (k) => run("readonly", (s) => s.get(k)),
    put: (k, v) => run("readwrite", (s) => s.put(v, k)).then(() => undefined),
    delete: (k) => run("readwrite", (s) => s.delete(k)).then(() => undefined),
    keys: () => run("readonly", (s) => s.getAllKeys()).then((ks) => ks.map(String)),
  };
}

let chosen: StashBackend | null | undefined;

/** `undefined` goes back to the default (IndexedDB when the browser has it). */
export function setStashBackend(b: StashBackend | null | undefined): void {
  chosen = b;
}

function backend(): StashBackend | null {
  if (chosen === undefined) chosen = typeof window === "undefined" ? null : indexedDbBackend();
  return chosen;
}

let fallbackTabId: string | null = null;

function newId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `t-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }
}

/** This tab's id: kept in sessionStorage, so a page load keeps it and another tab has its own. */
export function tabId(): string {
  try {
    const store = window.sessionStorage;
    const have = store.getItem(TAB_ID_KEY);
    if (have) return have;
    const id = newId();
    store.setItem(TAB_ID_KEY, id);
    return id;
  } catch {
    return (fallbackTabId ??= newId());
  }
}

const keyFor = (kind: StashKind) => `${tabId()}:${kind}`;

/** Keep `value` for this tab until the next load (or a day). Never throws. */
export async function saveStash(kind: StashKind, value: unknown, now: number = Date.now()): Promise<void> {
  const b = backend();
  if (!b) return;
  try {
    const entry: Stamped = { at: now, value };
    await b.put(keyFor(kind), entry);
  } catch {
    /* best effort: the reload holds still stand */
  }
}

/** What this tab kept, or null; anything older than a day, any tab's, is dropped on the way. Never throws. */
export async function loadStash<T = unknown>(kind: StashKind, now: number = Date.now()): Promise<T | null> {
  const b = backend();
  if (!b) return null;
  try {
    const mine = keyFor(kind);
    let found: T | null = null;
    for (const k of await b.keys()) {
      const entry = await b.get(k);
      if (!isStamped(entry) || now - entry.at > STASH_MAX_AGE_MS) {
        await b.delete(k);
        continue;
      }
      if (k === mine) found = entry.value as T;
    }
    return found;
  } catch {
    return null;
  }
}

/** Forget what this tab kept (sign-out; tests). Never throws. */
export async function clearStash(): Promise<void> {
  const b = backend();
  if (!b) return;
  try {
    await Promise.all((["files", "queues"] as StashKind[]).map((k) => b.delete(keyFor(k))));
  } catch {
    /* nothing to do */
  }
}

if (typeof window !== "undefined") {
  // Sign-out clears what the tab holds of the owner's (lib/authSession.ts).
  window.addEventListener(SIGNED_OUT_EVENT, () => void clearStash());
}
