// R/G red for the Work room's and the knowledge search's deep links
// (review 2026-09-26, work-deeplinks-ignored / dead-deep-links): the rows
// link to /settings/orchestrator?id=…, /settings/wishlist?id=… and
// /settings/databases?name=…, and the panels ignored the param — Ellis landed
// at the top with nothing open and had to find the row himself.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";

import { json, mockFetch, restoreFetch } from "./panelTestKit.ts";
import { OrchestratorPanel } from "../components/settings/OrchestratorPanel.tsx";
import { WishlistPanel } from "../components/settings/WishlistPanel.tsx";
import { DatabaseBrowser } from "../components/settings/DatabaseBrowser.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";

afterEach(() => {
  cleanup();
  restoreFetch();
});

const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();

function task(id: string, description: string, status: string) {
  return {
    id, parent_conversation_id: "c1", worker_conversation_id: `w-${id}`, task_description: description, task_type: "worker",
    model: null, cc_session_id: null, status, context_mode: "scoped", result_summary: status === "failed" ? null : "Done.",
    error: status === "failed" ? "It timed out." : null, timeout_seconds: 600, started_at: iso(3600_000),
    completed_at: iso(3000_000), created_at: iso(3600_000), updated_at: iso(3000_000),
  };
}

function orchestratorFetch() {
  return mockFetch((url) => {
    if (url.includes("/api/orchestrator/status")) {
      return {
        config: { enabled: true, max_concurrent_workers: 4, max_concurrent_cc_sessions: 1, default_worker_model: "gpt-5.6-terra", default_worker_timeout: 600 },
        active_count: 0,
        recent_tasks: [task("t-8", "Tidy the pet board notes", "completed"), task("t-7", "Price out the cabin weekend", "failed")],
      };
    }
    if (url.endsWith("/api/orchestrator/tasks/t-99")) return task("t-99", "Compare three dog walkers", "completed");
    if (url.includes("/api/settings/models")) return { providers: { openai: { available: true, models: [] } }, catalog: {} };
    return undefined;
  });
}

describe("OrchestratorPanel ?id=", () => {
  it("opens that helper's card", async () => {
    orchestratorFetch();
    render(<OrchestratorPanel initialId="t-7" />);
    const card = (await screen.findByText("Price out the cabin weekend")).closest("button") as HTMLElement;
    await waitFor(() => assert.equal(card.getAttribute("aria-expanded"), "true"));
    const other = screen.getByText("Tidy the pet board notes").closest("button") as HTMLElement;
    assert.equal(other.getAttribute("aria-expanded"), "false");
  });

  it("fetches one that is no longer in the recent list", async () => {
    const calls = orchestratorFetch();
    render(<OrchestratorPanel initialId="t-99" />);
    const card = (await screen.findByText("Compare three dog walkers")).closest("button") as HTMLElement;
    assert.equal(card.getAttribute("aria-expanded"), "true");
    assert.ok(calls.some((c) => c.url.endsWith("/api/orchestrator/tasks/t-99")));
  });
});

function wish(id: string, title: string, status: string) {
  return { id, title, why: "Because.", detail: null, status, priority: 2, source: "edward", conversation_id: null, times_wanted: 1, created_at: iso(86400_000), updated_at: iso(86400_000) };
}

describe("WishlistPanel ?id=", () => {
  it("unfolds the wish's section and marks the wish", async () => {
    mockFetch((url) => (url.includes("/api/wishlist") ? { items: [wish("w1", "Watch the garage camera", "open"), wish("w9", "Read my own logs", "done")] } : undefined));
    render(
      <ToastProvider>
        <WishlistPanel initialId="w9" />
      </ToastProvider>,
    );
    const title = await screen.findByText("Read my own logs");
    const card = title.closest("[data-wish-id]") as HTMLElement;
    assert.equal(card.getAttribute("data-highlight"), "true");
    const plain = screen.getByText("Watch the garage camera").closest("[data-wish-id]") as HTMLElement;
    assert.equal(plain.hasAttribute("data-highlight"), false);
  });
});

describe("DatabaseBrowser ?name=", () => {
  it("opens that database's tables", async () => {
    const calls = mockFetch((url) => {
      if (url.endsWith("/api/databases")) {
        return [
          { id: "db1", name: "runs", schema_name: "edward_db_runs", description: "Training log", created_at: iso(86400_000), updated_at: null, last_accessed: null },
          { id: "db2", name: "weights", schema_name: "edward_db_weights", description: "Monthly weights", created_at: iso(86400_000), updated_at: null, last_accessed: null },
        ];
      }
      if (url.includes("/api/databases/weights/tables")) return [{ name: "entries", column_count: 3 }];
      return json({}, 404);
    });
    render(<DatabaseBrowser initialName="weights" />);
    const entries = await screen.findByText("entries");
    assert.ok(entries);
    const row = screen.getByText("weights").closest("button") as HTMLElement;
    assert.equal(row.getAttribute("aria-expanded"), "true");
    assert.ok(calls.some((c) => c.url.includes("/api/databases/weights/tables")));
    assert.equal(within(screen.getByText("runs").closest("button") as HTMLElement).queryByText("entries"), null);
  });
});
