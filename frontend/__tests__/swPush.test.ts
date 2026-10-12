// The push service worker, run for real in a sandbox (2026-09-24).
//
// public/sw-push.js is a classic script imported by the generated worker,
// so it cannot import lib/notificationNav.ts; it carries a plain-JS copy of
// the approval decisions. These tests load the file into a fake worker
// global, fire push and notificationclick events at it, and check what it
// fetched and opened — and that its copy agrees with the TypeScript one.

import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

import { approvalDecisionRequest, notificationDestination } from "../lib/notificationNav.ts";

const SOURCE = readFileSync(new URL("../public/sw-push.js", import.meta.url), "utf8");
const ORIGIN = "https://edward.example.com";

type Listener = (event: any) => void;

function loadWorker(opts: { fetchOk?: boolean; windows?: any[] } = {}) {
  const listeners: Record<string, Listener> = {};
  const fetched: { path: string; init: any }[] = [];
  const opened: string[] = [];
  const shown: { title: string; options: any }[] = [];
  const sandbox: any = {
    self: {
      addEventListener: (type: string, fn: Listener) => {
        listeners[type] = fn;
      },
      registration: {
        showNotification: async (title: string, options: any) => {
          shown.push({ title, options });
        },
        pushManager: { subscribe: async () => ({}) },
      },
      location: { origin: ORIGIN },
    },
    clients: {
      matchAll: async () => opts.windows ?? [],
      openWindow: async (url: string) => {
        opened.push(url);
      },
    },
    fetch: async (path: string, init: any) => {
      fetched.push({ path, init });
      return { ok: opts.fetchOk ?? true, status: opts.fetchOk === false ? 409 : 200 };
    },
    console: { log() {}, error() {} },
    encodeURIComponent,
    JSON,
    Number,
    Promise,
    Array,
  };
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);
  return { listeners, fetched, opened, shown, sandbox };
}

async function click(w: ReturnType<typeof loadWorker>, data: any, action = "") {
  const waits: Promise<unknown>[] = [];
  w.listeners.notificationclick({
    action,
    notification: { data, close() {} },
    waitUntil: (p: Promise<unknown>) => waits.push(p),
  });
  await Promise.all(waits);
  // Let chained .then()s settle.
  await new Promise((r) => setTimeout(r, 0));
}

const APPROVAL = {
  url: "/chat?c=edward-mind",
  kind: "approval",
  approval_id: 7,
  conversation_id: "edward-mind",
  ledger_id: "l1",
  delivery_id: null,
};

describe("sw-push.js approvals", () => {
  let w: ReturnType<typeof loadWorker>;
  beforeEach(() => {
    w = loadWorker();
  });

  it("keeps the approval id and thread on the notification", async () => {
    const waits: Promise<unknown>[] = [];
    w.listeners.push({
      data: {
        json: () => ({
          title: "Approve Edward's send?",
          body: 'send_imessage to Parker: "hi"',
          ...APPROVAL,
          tag: "approval-7",
          actions: [
            { action: "approve", title: "Approve" },
            { action: "deny", title: "Deny" },
            { action: "open", title: "Open" },
          ],
        }),
      },
      waitUntil: (p: Promise<unknown>) => waits.push(p),
    });
    await Promise.all(waits);
    const { options } = w.shown[0];
    assert.equal(options.data.approval_id, 7);
    assert.equal(options.data.conversation_id, "edward-mind");
    assert.equal(options.data.kind, "approval");
    assert.deepEqual(
      options.actions.map((a: any) => a.action),
      ["approve", "deny", "open"],
    );
  });

  it("Approve posts the decision and opens nothing", async () => {
    await click(w, APPROVAL, "approve");
    const decision = w.fetched.find((f) => f.path.startsWith("/api/approvals/"));
    assert.ok(decision, JSON.stringify(w.fetched));
    assert.equal(decision.path, "/api/approvals/7");
    assert.equal(decision.init.method, "POST");
    assert.equal(decision.init.credentials, "include");
    assert.deepEqual(JSON.parse(decision.init.body), { decision: "approve" });
    assert.deepEqual(w.opened, []);
  });

  it("Deny posts deny", async () => {
    await click(w, APPROVAL, "deny");
    const decision = w.fetched.find((f) => f.path.startsWith("/api/approvals/"));
    assert.deepEqual(JSON.parse(decision!.init.body), { decision: "deny" });
  });

  it("Open, or a plain tap, goes to the thread and decides nothing", async () => {
    await click(w, APPROVAL, "open");
    await click(w, APPROVAL, "");
    assert.deepEqual(w.opened, ["/chat?c=edward-mind", "/chat?c=edward-mind"]);
    assert.equal(w.fetched.filter((f) => f.path.startsWith("/api/approvals/")).length, 0);
  });

  it("a refused decision opens the thread so Ellis sees why", async () => {
    const refused = loadWorker({ fetchOk: false });
    await click(refused, APPROVAL, "approve");
    assert.deepEqual(refused.opened, ["/chat?c=edward-mind"]);
  });

  it("other pushes keep Open / Done / Snooze", async () => {
    await click(w, { url: "/today", kind: "mind", ledger_id: "l2", delivery_id: "d2" }, "done");
    assert.ok(w.fetched.every((f) => !f.path.startsWith("/api/approvals/")));
    assert.ok(w.fetched.some((f) => f.path === "/api/push/ack"));
    assert.deepEqual(w.opened, []);
    await click(w, { url: "/settings/room/mind", kind: "mind" }, "");
    assert.deepEqual(w.opened, ["/settings/room/mind"]);
  });

  it("its copy of the decisions agrees with lib/notificationNav.ts", () => {
    const cases: [any, string][] = [
      [APPROVAL, "approve"],
      [APPROVAL, "deny"],
      [APPROVAL, "open"],
      [{ ...APPROVAL, approval_id: "12" }, "approve"],
      [{ ...APPROVAL, approval_id: "7/../x" }, "approve"],
      [{ ...APPROVAL, kind: "mind" }, "approve"],
      [null, "approve"],
    ];
    for (const [data, action] of cases) {
      assert.deepEqual(
        JSON.parse(JSON.stringify(w.sandbox.approvalDecisionRequest(data, action))),
        approvalDecisionRequest(data, action),
        `${JSON.stringify(data)} ${action}`,
      );
    }
    for (const data of [APPROVAL, { ...APPROVAL, url: "/today" }, { kind: "mind", url: "/today" }]) {
      assert.equal(w.sandbox.notificationDestination(data), notificationDestination(data, ORIGIN));
    }
  });
});
