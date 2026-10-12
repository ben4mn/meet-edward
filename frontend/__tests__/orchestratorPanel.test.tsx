// Settings → Work → Orchestrator (2026-09-26). The default helper model
// was a hard-coded Claude list (Haiku / Sonnet / Opus) over a stored
// gpt-5.6-terra, so the select showed "Haiku 4.5" — a lie. It now offers
// the live Codex catalog and shows what is stored, even when the catalog
// does not list it.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { OrchestratorPanel } from "../components/settings/OrchestratorPanel.tsx";

const originalFetch = globalThis.fetch;
let storedModel = "gpt-5.6-terra";

beforeEach(() => {
  storedModel = "gpt-5.6-terra";
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    if (url.includes("/api/orchestrator/status")) {
      return json({
        config: { enabled: true, max_concurrent_workers: 4, max_concurrent_cc_sessions: 1, default_worker_model: storedModel, default_worker_timeout: 600 },
        active_count: 0,
        recent_tasks: [],
      });
    }
    if (url.includes("/api/settings/models")) {
      return json({
        providers: {
          openai: { available: true, models: [
            { id: "gpt-6-astra", name: "GPT-6-Astra (Recommended)", default_effort: "medium" },
            { id: "gpt-5.6-terra", name: "GPT-5.6-Terra", default_effort: "medium" },
          ] },
          anthropic: { available: false, models: [] },
        },
        catalog: { recommended: "gpt-6-astra" },
      });
    }
    return new Response("{}", { status: 404 });
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("OrchestratorPanel", () => {
  it("offers the live catalog for the helper model, the stored one selected", async () => {
    render(<OrchestratorPanel />);
    const select = (await screen.findByLabelText(/helper model/i)) as HTMLSelectElement;
    await waitFor(() => assert.equal(select.options.length, 2));
    assert.equal(select.value, "gpt-5.6-terra");
    const labels = Array.from(select.options).map((o) => o.textContent);
    assert.deepEqual(labels, ["GPT-6-Astra (recommended)", "GPT-5.6-Terra"]);
    assert.equal(screen.queryByText(/haiku|sonnet|opus|claude/i), null);
  });

  it("shows a stored model the catalog does not list, as current", async () => {
    storedModel = "claude-haiku-4-5-20251001";
    render(<OrchestratorPanel />);
    const select = (await screen.findByLabelText(/helper model/i)) as HTMLSelectElement;
    await waitFor(() => assert.equal(select.options.length, 3));
    assert.equal(select.value, "claude-haiku-4-5-20251001");
    assert.equal(select.options[select.selectedIndex].textContent, "claude-haiku-4-5-20251001 (current)");
  });

  it("says so in his words when there is nothing to show", async () => {
    render(<OrchestratorPanel />);
    await screen.findByText(/haven't needed any helpers/i);
  });
});
