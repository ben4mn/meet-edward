// R/G red for the settings panels' delete-with-undo (review 2026-09-26,
// undo-toast-outlives-window): the toast offered Undo for 7 s while the
// delete committed at 6 s, a late Undo closed the toast as if it had worked,
// and the deleted row came back into the list when the store stopped hiding
// it. One helper now schedules the delete, times the toast to the window,
// drops the row from the panel's own list at commit, and says when an Undo
// came too late.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { createPendingDeletes, UNDO_WINDOW_MS } from "../lib/pendingDeletes.ts";
import { scheduleUndoableDelete } from "../lib/undoableDelete.ts";

type Opts = { title: unknown; body?: unknown; tone?: string; undo?: () => void; duration?: number };

function harness() {
  const timers: Array<() => void> = [];
  const store = createPendingDeletes({
    setTimer: (fn) => {
      timers.push(fn);
      return timers.length as unknown as ReturnType<typeof setTimeout>;
    },
    clearTimer: () => {},
  });
  const toasts: Opts[] = [];
  const log: string[] = [];
  const toast = (o: Opts) => {
    toasts.push(o);
    return String(toasts.length);
  };
  const fire = () => timers.splice(0).forEach((t) => t());
  return { store, toasts, log, toast, fire };
}

describe("scheduleUndoableDelete", () => {
  it("shows Undo for exactly the undo window, not the default undo toast length", () => {
    const h = harness();
    scheduleUndoableDelete({ key: "doc:1", commit: async () => {}, toast: h.toast, title: "Document deleted", store: h.store });
    assert.equal(h.toasts.length, 1);
    assert.equal(h.toasts[0].duration, UNDO_WINDOW_MS);
    assert.equal(typeof h.toasts[0].undo, "function");
  });

  it("undoes in time: nothing is sent and the caller hears about it", async () => {
    const h = harness();
    scheduleUndoableDelete({
      key: "doc:1",
      commit: async () => void h.log.push("commit"),
      onCommit: () => h.log.push("drop"),
      onUndone: () => h.log.push("undone"),
      toast: h.toast,
      title: "Document deleted",
      store: h.store,
    });
    h.toasts[0].undo!();
    h.fire();
    await Promise.resolve();
    assert.deepEqual(h.log, ["undone"]);
    assert.equal(h.toasts.length, 1);
  });

  it("drops the row from the panel's own list when the delete commits, before the call", async () => {
    const h = harness();
    scheduleUndoableDelete({
      key: "doc:1",
      commit: async () => void h.log.push("commit"),
      onCommit: () => h.log.push("drop"),
      toast: h.toast,
      title: "Document deleted",
      store: h.store,
    });
    h.fire();
    await Promise.resolve();
    assert.deepEqual(h.log, ["drop", "commit"]);
    assert.equal(h.store.isPending("doc:1"), false);
  });

  it("says so when Undo comes after the delete went out", async () => {
    const h = harness();
    scheduleUndoableDelete({
      key: "doc:1",
      commit: async () => {},
      onUndone: () => h.log.push("undone"),
      toast: h.toast,
      title: "Document deleted",
      tooLate: "Too late — that document is already deleted.",
      store: h.store,
    });
    h.fire();
    h.toasts[0].undo!();
    assert.deepEqual(h.log, []);
    assert.equal(h.toasts.length, 2);
    assert.equal(h.toasts[1].title, "Too late — that document is already deleted.");
    assert.equal(h.toasts[1].tone, "bad");
  });

  it("reports a failed call so the panel can bring the row back", async () => {
    const h = harness();
    scheduleUndoableDelete({
      key: "doc:1",
      commit: async () => {
        throw new Error("500");
      },
      onError: () => h.log.push("error"),
      toast: h.toast,
      title: "Document deleted",
      store: h.store,
    });
    h.fire();
    await new Promise((r) => setTimeout(r, 0));
    assert.deepEqual(h.log, ["error"]);
  });
});
