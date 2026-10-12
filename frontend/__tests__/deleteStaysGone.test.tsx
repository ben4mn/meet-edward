// A deleted row stays gone once its delete commits (review 2026-09-26,
// pending-delete-rows-reappear, HIGH): the store stops hiding the id the
// moment the commit starts, so each panel drops the row from its own list
// in the same tick (scheduleUndoableDelete's onCommit). Documents and
// reminders are pinned in their own panel tests; these pin Files and
// Memories. Assertions are booleans, never a jsdom node.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { mockFetch, restoreFetch, type Call } from "./panelTestKit.ts";
import { FileBrowser } from "../components/settings/FileBrowser.tsx";
import { MemoryBrowser } from "../components/settings/MemoryBrowser.tsx";
import { pendingDeletes } from "../lib/pendingDeletes.ts";

const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();

afterEach(() => {
  cleanup();
  restoreFetch();
  pendingDeletes.flush();
});

describe("Files", () => {
  it("a deleted file does not come back when the delete commits", async () => {
    const FILES = [
      { id: "f-csv", filename: "cabin-budget.csv", mime_type: "text/csv", size_bytes: 412, category: "document", description: null, tags: null, source: "sandbox", source_conversation_id: null, created_at: iso(3600_000), updated_at: iso(3600_000), last_accessed: null, access_count: 0, download_url: "/api/files/f-csv/download" },
      { id: "f-jpg", filename: "biscuit-labs.jpg", mime_type: "image/jpeg", size_bytes: 184220, category: "document", description: null, tags: null, source: "upload", source_conversation_id: null, created_at: iso(3600_000), updated_at: iso(3600_000), last_accessed: null, access_count: 0, download_url: "/api/files/f-jpg/download" },
    ];
    const calls: Call[] = mockFetch((url, method) => {
      if (method === "DELETE") return { ok: true };
      const fm = url.match(/\/api\/files\/([^/?]+)$/);
      if (fm) return FILES.find((f) => f.id === fm[1]);
      if (url.includes("/api/files")) return { files: FILES, pagination: { limit: 50, offset: 0, total: FILES.length } };
      return undefined;
    });
    render(<FileBrowser initialId="f-csv" />);
    const dialog = await screen.findByRole("dialog", { name: "cabin-budget.csv" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await act(async () => {
      pendingDeletes.flush();
      await new Promise((r) => setTimeout(r, 20));
    });
    await waitFor(() => assert.ok(calls.some((c) => c.method === "DELETE" && c.url.includes("/api/files/f-csv"))));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true, "the sheet has closed"));
    const back = screen.queryByText("cabin-budget.csv") !== null;
    assert.equal(back, false, "the deleted file stays gone");
    assert.equal(screen.queryByText("biscuit-labs.jpg") !== null, true);
  });
});

describe("Memories", () => {
  it("a deleted memory does not come back when the delete commits", async () => {
    const MEMORIES = [
      { id: "m-01", content: "Ellis's partner is Rowan.", memory_type: "fact", importance: 0.95, temporal_nature: "timeless", tier: "knowledge", reinforcement_count: 14, source_conversation_id: null, created_at: iso(8 * 86400_000), updated_at: null, last_accessed: null, access_count: 42, user_id: null, origin: "heard", about: "owner" },
      { id: "m-12", content: "Watch Biscuit's PM dose adherence this week.", memory_type: "intention", importance: 0.7, temporal_nature: "evolving", tier: "observation", reinforcement_count: 0, source_conversation_id: null, created_at: iso(2 * 86400_000), updated_at: null, last_accessed: null, access_count: 0, user_id: null, origin: "thought", about: "self" },
    ];
    const STATS = { total: 2, live: 2, created_last_24h: 0, unused_live: 0, by_type: {}, by_tier: {}, average_importance: 0.6 };
    const calls: Call[] = mockFetch((url, method) => {
      if (method === "DELETE") return { ok: true };
      if (url.includes("/provenance")) return { memory: { id: "m-01", content: MEMORIES[0].content, origin: "heard", about: "owner", source_type: "conversation", created_at: MEMORIES[0].created_at, source_conversation_id: null, tier: "knowledge", reinforcement_count: 14 }, conversation: null, chat: null, contact: null, fold: null, source_ref: null, summary: "Ellis told me." };
      if (/\/api\/memories\/m-\d+$/.test(url)) return MEMORIES.find((m) => url.endsWith(m.id));
      if (url.includes("/api/memories")) return { memories: MEMORIES, stats: STATS, pagination: { limit: 20, offset: 0, total: MEMORIES.length } };
      return undefined;
    });
    render(<MemoryBrowser initialId="m-01" />);
    const dialog = await screen.findByRole("dialog", { name: "Where this came from" });
    await waitFor(() => assert.equal(within(dialog).getByRole("button", { name: "Delete" }).hasAttribute("disabled"), false));
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await act(async () => {
      pendingDeletes.flush();
      await new Promise((r) => setTimeout(r, 20));
    });
    await waitFor(() => assert.ok(calls.some((c) => c.method === "DELETE" && c.url.includes("/api/memories/m-01"))));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true, "the sheet has closed"));
    const back = screen.queryByText("Ellis's partner is Rowan.") !== null;
    assert.equal(back, false, "the deleted memory stays gone");
  });
});
