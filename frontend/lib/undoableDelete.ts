// Delete with Undo, the one way the settings panels do it (review
// 2026-09-26). Pinned in __tests__/undoableDelete.test.ts.
//
// - The toast lasts exactly the undo window. The default undo toast (7 s)
//   outlived pendingDeletes' 6 s window, so a tap on Undo at 6.5 s closed the
//   toast as if it had worked while the DELETE had already gone out.
// - An Undo that comes too late says so, and `onUndone` (restore a count)
//   runs only when something was really undone.
// - `onCommit` runs when the delete commits, just before the API call: the
//   panel drops the row from its own list there. The store stops hiding the
//   row at that moment, and a row that was only ever hidden came back into
//   the list until the next reload. `onError` runs when the call fails —
//   reload, so the row is back.

import type { ReactNode } from "react";
import { UNDO_WINDOW_MS, pendingDeletes as appStore, type PendingDeletes } from "./pendingDeletes";

export interface UndoToastOptions {
  title: ReactNode;
  body?: ReactNode;
  tone?: "ok" | "bad" | "neutral" | "edward";
  undo?: () => void;
  duration?: number;
}

export interface UndoableDelete {
  /** pendingDeletes key; the panels hide rows whose key is pending. */
  key: string;
  /** The API call. */
  commit: () => Promise<unknown>;
  toast: (options: UndoToastOptions) => unknown;
  title: ReactNode;
  body?: ReactNode;
  onCommit?: () => void;
  onError?: (error: unknown) => void;
  onUndone?: () => void;
  /** What a late Undo says. */
  tooLate?: string;
  store?: PendingDeletes;
  windowMs?: number;
}

export const TOO_LATE = "Too late — it's already deleted.";

export function scheduleUndoableDelete(d: UndoableDelete): void {
  const store = d.store ?? appStore;
  store.schedule(
    d.key,
    () => {
      d.onCommit?.();
      return d.commit();
    },
    { onError: (_id, error) => d.onError?.(error) },
  );
  d.toast({
    title: d.title,
    body: d.body,
    duration: d.windowMs ?? UNDO_WINDOW_MS,
    undo: () => {
      if (store.undo(d.key)) d.onUndone?.();
      else d.toast({ title: d.tooLate ?? TOO_LATE, tone: "bad" });
    },
  });
}
