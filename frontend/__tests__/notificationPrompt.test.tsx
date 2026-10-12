// R/G red (2026-09-26 review): the "Logged · Undo" toast landed on top of
// the notification prompt — both sit just above the dock — and hid its
// "Allow notifications / Not now" row. While the prompt shows above the
// dock it tells the toasts its height (--prompt-h), and they stack above it.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { NotificationPrompt } from "../components/NotificationPrompt.tsx";

const ROOT = path.resolve(import.meta.dirname, "..");

// Node's own global localStorage is inert without --localstorage-file; give
// the page a real in-memory one (as theme.test.tsx does).
class MemoryStorage {
  private m = new Map<string, string>();
  get length() {
    return this.m.size;
  }
  key(i: number) {
    return Array.from(this.m.keys())[i] ?? null;
  }
  getItem(k: string) {
    return this.m.has(k) ? this.m.get(k)! : null;
  }
  setItem(k: string, v: string) {
    this.m.set(k, String(v));
  }
  removeItem(k: string) {
    this.m.delete(k);
  }
  clear() {
    this.m.clear();
  }
}
const storage = new MemoryStorage();
Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true, writable: true });
Object.defineProperty(window, "localStorage", { value: storage, configurable: true, writable: true });
const originalFetch = globalThis.fetch;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function flush(ms = 40) {
  await act(async () => {
    await new Promise((r) => setTimeout(r, ms));
  });
}

const promptH = () => document.documentElement.style.getPropertyValue("--prompt-h");

beforeEach(() => {
  // Node has its own global navigator beside jsdom's: give both a service worker.
  Object.defineProperty(window.navigator, "serviceWorker", { configurable: true, value: {} });
  Object.defineProperty(globalThis.navigator, "serviceWorker", { configurable: true, value: {} });
  for (const g of [window, globalThis] as unknown as Record<string, unknown>[]) {
    g.PushManager = function PushManager() {};
    g.Notification = { permission: "default" };
  }
  try {
    window.localStorage.removeItem("edward-notification-dismissed");
  } catch {
    /* none */
  }
  document.documentElement.style.removeProperty("--prompt-h");
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const p = String(input);
    if (p.includes("/api/push/status")) return json({ configured: true, subscription_count: 0, vapid_public_key: "k" });
    return json({});
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("NotificationPrompt and the toasts", () => {
  it("above the dock it lifts the toasts by its height; dismissed, it lets them back down", async () => {
    render(
      <PathnameContext.Provider value="/today">
        <NotificationPrompt />
      </PathnameContext.Provider>,
    );
    await flush();
    assert.ok(screen.getByText("Let me reach you"));
    assert.ok(promptH() && promptH() !== "0px", `--prompt-h is set (${promptH() || "unset"})`);
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await flush();
    assert.equal(promptH(), "0px");
  });

  it("at the top (chat) it leaves the toasts where they are", async () => {
    render(
      <PathnameContext.Provider value="/chat">
        <NotificationPrompt />
      </PathnameContext.Provider>,
    );
    await flush();
    assert.ok(screen.getByText("Let me reach you"));
    assert.equal(promptH(), "0px");
  });

  it("the toast viewport adds --prompt-h to its bottom offset", () => {
    const css = fs.readFileSync(path.join(ROOT, "app/globals.css"), "utf8");
    const rule = css.match(/\.toast-viewport\s*\{[^}]*\}/)?.[0] ?? "";
    assert.match(rule, /var\(--prompt-h,\s*0px\)/);
  });
});
