// Settings → Memory → Documents (2026-09-26, Jarvis polish).
//
// - "Edward: state" and "Goal: …" documents are pinned to the top.
// - Tag chips filter the list.
// - A document reads as rendered markdown at full height (no inner scroll
//   box); ?id= opens it directly.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { mockFetch, restoreFetch, type Call } from "./panelTestKit.ts";
import { DocumentBrowser } from "../components/settings/DocumentBrowser.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";
import { pendingDeletes } from "../lib/pendingDeletes.ts";

const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();
const doc = (id: string, title: string, content: string, tags: string, ago: number) => ({
  id, title, content, tags, source_conversation_id: null, created_at: iso(ago), updated_at: iso(ago),
  last_accessed: null, access_count: 3, user_id: null, score: null,
});

const DOCS = [
  doc("d-cabin", "Cabin weekend — Lake Tahoe", "# Cabin weekend\n\nFri 17th – Sun 19th. **Dogs coming.**", "trip,plans", 30 * 60_000),
  doc("d-goal", "Goal: Run the Springfield half", "## Why\nSub-2:00.", "goal,active", 2 * 86400_000),
  doc("d-biscuit", "Biscuit — med protocol", "06:00 and 18:00 sharp.", "pets,biscuit", 3 * 86400_000),
  doc("d-state", "Edward: state", "I'm Edward. I keep Ellis's days light.", "state,self", 7 * 3600_000),
];

let calls: Call[] = [];

beforeEach(() => {
  calls = mockFetch((url, method) => {
    if (method === "DELETE") return { status: "deleted" };
    const m = url.match(/\/api\/documents\/([^?]+)$/);
    if (m) return DOCS.find((d) => d.id === m[1]);
    if (url.includes("/api/documents")) {
      const u = new URL(url, "http://x");
      const tag = u.searchParams.get("tags");
      const rows = tag ? DOCS.filter((d) => d.tags.split(",").includes(tag)) : DOCS;
      return { documents: rows, stats: { total: DOCS.length, by_tag: { trip: 1, plans: 1, goal: 1, active: 1, pets: 1, biscuit: 1, state: 1, self: 1 } }, pagination: { limit: 50, offset: 0, total: rows.length } };
    }
    return undefined;
  });
});

afterEach(() => {
  cleanup();
  restoreFetch();
});

describe("DocumentBrowser", () => {
  it("pins his state page and Ellis's goals above the rest", async () => {
    render(<DocumentBrowser />);
    await screen.findByText("Cabin weekend — Lake Tahoe");
    const titles = screen.getAllByTestId("doc-title").map((el) => el.textContent);
    assert.deepEqual(titles, ["My state", "Run the Springfield half", "Cabin weekend — Lake Tahoe", "Biscuit — med protocol"]);
  });

  it("filters by a tag chip", async () => {
    render(<DocumentBrowser />);
    await screen.findByText("Cabin weekend — Lake Tahoe");
    const tags = screen.getByRole("radiogroup", { name: "Tags" });
    fireEvent.click(within(tags).getByRole("radio", { name: /pets/ }));
    await waitFor(() => assert.ok(calls.some((c) => c.url.includes("tags=pets"))));
    await waitFor(() => assert.equal(screen.queryByText("Cabin weekend — Lake Tahoe") === null, true));
  });

  it("opens ?id= as rendered markdown at full height", async () => {
    const { container } = render(<DocumentBrowser initialId="d-cabin" />);
    const heading = await screen.findByRole("heading", { name: "Cabin weekend", level: 1 });
    assert.ok(heading);
    assert.ok(screen.getByText("Dogs coming.").tagName === "STRONG");
    // The way back is the page's breadcrumb ("‹ Documents"), not a second row here.
    assert.doesNotMatch(container.innerHTML, /max-h-96|overflow-y-auto/);
  });

  it("opens a document when its card is tapped", async () => {
    render(<DocumentBrowser />);
    fireEvent.click((await screen.findByText("Biscuit — med protocol")).closest("button") as HTMLElement);
    await screen.findByText("06:00 and 18:00 sharp.");
  });

  it("gives an opened document its own history entry, so Back returns to the list", async () => {
    window.history.replaceState(null, "", "/settings/documents");
    const before = window.history.length;
    render(<DocumentBrowser />);
    fireEvent.click((await screen.findByText("Biscuit — med protocol")).closest("button") as HTMLElement);
    await screen.findByText("06:00 and 18:00 sharp.");
    assert.equal(window.location.search, "?id=d-biscuit");
    assert.equal(window.history.length, before + 1, "pushState, not replaceState");
  });

  it("keeps a deleted document gone once the delete goes out, and says when Undo is too late", async () => {
    const listed = () => screen.queryAllByTestId("doc-title").map((el) => el.textContent);
    render(
      <ToastProvider>
        <DocumentBrowser />
      </ToastProvider>,
    );
    fireEvent.click((await screen.findByText("Biscuit — med protocol")).closest("button") as HTMLElement);
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    const undo = await screen.findByRole("button", { name: /Undo/ });
    assert.ok(!listed().includes("Biscuit — med protocol"), "hidden while Undo is offered");
    await act(async () => {
      pendingDeletes.flush();
      await new Promise((r) => setTimeout(r, 20));
    });
    assert.ok(calls.some((c) => c.method === "DELETE" && c.url.includes("/api/documents/d-biscuit")));
    assert.ok(!listed().includes("Biscuit — med protocol"), "the row does not come back when the delete commits");
    fireEvent.click(undo);
    assert.ok(await screen.findByText(/Too late/));
  });
});
