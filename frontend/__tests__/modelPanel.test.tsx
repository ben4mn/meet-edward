// Settings → Mind → Model (2026-09-26): the chat model as radio cards that
// save on tap (undo in a toast), only the providers that are available, a
// read-only "who thinks what", and no second copy of the chat work
// preference (it lives in the Mind room).

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ModelPanel } from "../components/settings/ModelPanel.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";

const originalFetch = globalThis.fetch;
let posts: unknown[] = [];
let stored = "gpt-6-astra";
let routing: Record<string, unknown> = {};

beforeEach(() => {
  posts = [];
  stored = "gpt-6-astra";
  routing = {};
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    if (url.endsWith("/api/settings/models")) {
      return json({
        providers: {
          openai: {
            available: true,
            models: [
              { id: "gpt-6-astra", name: "GPT-6-Astra (Recommended)", description: "Frontier intelligence.", default_effort: "medium" },
              { id: "gpt-5.6-sol", name: "GPT-5.6-Sol", description: "Older coding model.", default_effort: "low" },
            ],
          },
          anthropic: { available: false, models: [] },
        },
        catalog: { recommended: "gpt-6-astra" },
        background_model: "gpt-5.6-terra",
        voice_model: "gpt-5.6-luna",
        ...routing,
      });
    }
    if (url.endsWith("/api/settings")) {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        posts.push(body);
        stored = body.model;
      }
      return json({ name: "Edward", personality: "", provider: "openai", model: stored, temperature: 1, system_prompt: "", timezone: "UTC" });
    }
    return json({}, 404);
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("ModelPanel", () => {
  it("offers the catalog as radio cards, the saved one checked, recommended marked", async () => {
    render(<ModelPanel />);
    const astra = await screen.findByRole("radio", { name: /GPT-6-Astra/ });
    assert.equal(astra.getAttribute("aria-checked"), "true");
    assert.equal(screen.getByRole("radio", { name: /GPT-5.6-Sol/ }).getAttribute("aria-checked"), "false");
    assert.ok(screen.getByText("Recommended"));
    assert.ok(screen.getByText("Older coding model."));
  });

  it("never shows a dead provider button", async () => {
    render(<ModelPanel />);
    await screen.findByRole("radio", { name: /GPT-6-Astra/ });
    assert.equal(screen.queryByText(/anthropic/i), null);
  });

  it("saves on tap", async () => {
    render(<ModelPanel />);
    fireEvent.click(await screen.findByRole("radio", { name: /GPT-5.6-Sol/ }));
    await waitFor(() => assert.deepEqual(posts, [{ provider: "openai", model: "gpt-5.6-sol" }]));
    assert.equal(screen.getByRole("radio", { name: /GPT-5.6-Sol/ }).getAttribute("aria-checked"), "true");
  });

  it("undoes a switch: Undo saves the previous model back", async () => {
    render(
      <ToastProvider>
        <ModelPanel />
      </ToastProvider>,
    );
    fireEvent.click(await screen.findByRole("radio", { name: /GPT-5.6-Sol/ }));
    await waitFor(() => assert.equal(posts.length, 1));
    fireEvent.click(await screen.findByRole("button", { name: /Undo/ }));
    await waitFor(() =>
      assert.deepEqual(posts, [
        { provider: "openai", model: "gpt-5.6-sol" },
        { provider: "openai", model: "gpt-6-astra" },
      ]),
    );
    await waitFor(() => assert.equal(screen.getByRole("radio", { name: /GPT-6-Astra/ }).getAttribute("aria-checked"), "true"));
  });

  it("says who thinks what, read-only", async () => {
    render(<ModelPanel />);
    await screen.findByText("Who thinks what");
    assert.ok(screen.getByText("Background"));
    assert.ok(screen.getByText("gpt-5.6-terra"));
    assert.ok(screen.getAllByText("GPT-6-Astra").length >= 1, "chat reads by name");
    assert.ok(screen.getByText("gpt-5.6-luna"), "an id the catalog does not list reads as itself");
  });

  it("shows the mind and the screens with their effort, and where a wake departs from it", async () => {
    routing = {
      mind_model: "gpt-6-astra", mind_effort: "high", mind_efforts: { morning: "high", reflect: "xhigh" },
      pulse_model: "gpt-5.6-terra", pulse_effort: "low", chat_model: "gpt-6-astra", chat_effort: "low",
    };
    render(<ModelPanel />);
    await screen.findByText("Who thinks what");
    assert.ok(screen.getByText("Mind"));
    assert.ok(screen.getByText("Surfaces"));
    assert.ok(screen.getByText("Reflecting on the week at xhigh"));
  });

  it("does not repeat the chat work preference", async () => {
    render(<ModelPanel />);
    await screen.findByRole("radio", { name: /GPT-6-Astra/ });
    assert.equal(screen.queryByText(/work in chat/i), null);
  });
});
