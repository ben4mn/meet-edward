// The Memory room's "What does he have on…" (review 2026-09-26,
// memory-room-all-10): with no query each kind is a preview of the newest
// few, so "All 10" read as "he has 10 memories". A preview says "See all"
// and opens the kind's own panel; "All N" is kept for a real search.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { mockFetch, restoreFetch } from "./panelTestKit.ts";
import { KnowledgeSearch } from "../components/settings/KnowledgeSearch.tsx";

const mems = Array.from({ length: 10 }, (_, i) => ({
  id: `m-${i}`, content: `Biscuit memory ${i}`, memory_type: "fact", tier: "knowledge", updated_at: null, created_at: new Date().toISOString(),
}));

beforeEach(() => {
  mockFetch((url) => {
    if (url.includes("/api/memories")) return { memories: mems, stats: { total: 4843 }, pagination: { limit: 10, offset: 0, total: 4843 } };
    if (url.includes("/api/documents")) return { documents: [], stats: { total: 0 }, pagination: { limit: 10, offset: 0, total: 0 } };
    if (url.includes("/api/files")) return { files: [], pagination: { total: 0 } };
    if (url.includes("/api/databases")) return [];
    return undefined;
  });
});

afterEach(() => {
  cleanup();
  restoreFetch();
});

describe("KnowledgeSearch", () => {
  it("offers See all (the panel), not a preview's length, when nothing is searched", async () => {
    render(<KnowledgeSearch />);
    const group = await screen.findByRole("region", { name: "Memories" });
    // (booleans, never an element: the runner would inspect a jsdom node for minutes)
    assert.equal(within(group).queryByText(/^All \d+$/) !== null, false, "no 'All 10' on a preview");
    const seeAll = within(group).queryByRole("link", { name: /See all/ });
    assert.ok(seeAll !== null, "a See all link");
    assert.equal(seeAll!.getAttribute("href"), "/settings/memories");
  });

  it("keeps All N for a real search", async () => {
    render(<KnowledgeSearch />);
    await screen.findByRole("region", { name: "Memories" });
    fireEvent.change(screen.getByRole("searchbox", { name: /Search everything/ }), { target: { value: "biscuit" } });
    await waitFor(() => assert.ok(within(screen.getByRole("region", { name: "Memories" })).queryByRole("button", { name: "All 10" }) !== null));
  });
});

// Review 2026-09-26 (failures-rendered-as-empty): a source that failed is
// said, in Edward's voice — never shown as "nothing on it".
import { json } from "./panelTestKit.ts";

describe("KnowledgeSearch when a source fails", () => {
  it("says which part it couldn't search, and still shows the rest", async () => {
    mockFetch((url) => {
      if (url.includes("/api/memories")) return json({ detail: "busy" }, 503);
      if (url.includes("/api/documents")) return { documents: [{ id: "d1", title: "Biscuit's meds", content: "x", tags: "", updated_at: null, created_at: new Date().toISOString() }], stats: { total: 1 }, pagination: { limit: 10, offset: 0, total: 1 } };
      if (url.includes("/api/files")) return { files: [], pagination: { total: 0 } };
      if (url.includes("/api/databases")) return [];
      return undefined;
    });
    render(<KnowledgeSearch />);
    await screen.findByRole("region", { name: "Documents" });
    assert.equal(screen.queryByText(/I can't search memories right now/) !== null, true);
  });

  it("with everything failing, never says there is nothing", async () => {
    mockFetch(() => json({ detail: "down" }, 503));
    render(<KnowledgeSearch />);
    await waitFor(() => assert.equal(screen.queryByText(/I can't search what I know right now/) !== null, true));
    assert.equal(screen.queryByText(/Nothing here yet|Nothing on/) !== null, false);
  });
});
