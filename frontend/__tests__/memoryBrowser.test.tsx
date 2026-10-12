// Settings → Memory → Memories (2026-09-26, Jarvis polish).
//
// - Search first; one "Filter" chip opens a sheet (type incl. intention,
//   tier, nature, order); the tier bar is one segment.
// - A card leads with how he came by it ("he heard") and whom it is about;
//   importance and access counts are not on the card.
// - The whole card opens "Where this came from"; ?id= opens it directly.
// - Forget… previews, then asks in a ConfirmSheet; errors are a Notice.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { json, mockFetch, restoreFetch, type Call } from "./panelTestKit.ts";
import { MemoryBrowser } from "../components/settings/MemoryBrowser.tsx";

const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();

const MEMORIES = [
  {
    id: "m-01", content: "Ellis's partner is Rowan.", memory_type: "fact", importance: 0.95, temporal_nature: "timeless",
    tier: "knowledge", reinforcement_count: 14, source_conversation_id: null, created_at: iso(8 * 86400_000),
    updated_at: null, last_accessed: null, access_count: 42, user_id: null, origin: "heard", about: "owner",
  },
  {
    id: "m-12", content: "Watch Biscuit's PM dose adherence this week.", memory_type: "intention", importance: 0.7,
    temporal_nature: "evolving", tier: "observation", reinforcement_count: 0, source_conversation_id: null,
    created_at: iso(2 * 86400_000), updated_at: null, last_accessed: null, access_count: 0, user_id: null,
    origin: "thought", about: "self",
  },
];

const STATS = { total: 4400, live: 4410, created_last_24h: 37, unused_live: 1902, by_type: {}, by_tier: { knowledge: 1200, belief: 1000, observation: 2200 }, average_importance: 0.6 };

function provenance(id: string) {
  const m = MEMORIES.find((x) => x.id === id) ?? MEMORIES[0];
  return {
    memory: { id: m.id, content: m.content, origin: m.origin, about: m.about, source_type: "conversation", created_at: m.created_at, source_conversation_id: "c-1", tier: m.tier, reinforcement_count: m.reinforcement_count },
    conversation: { id: "c-1", title: "Weekend plans", source: "user", channel: null },
    chat: null, contact: null, fold: null, source_ref: { kind: "extraction", turn_source: "chat" },
    summary: "Stored from “Weekend plans”.",
  };
}

let calls: Call[] = [];
let forgetFails = false;

beforeEach(() => {
  forgetFails = false;
  calls = mockFetch((url, method, body) => {
    if (url.includes("/provenance")) return provenance(url.split("/api/memories/")[1].split("/")[0]);
    if (url.includes("/api/memories/forget")) {
      if (forgetFails) return json({ detail: "The memory store is busy." }, 503);
      const b = body as { confirm?: boolean };
      if (!b.confirm) {
        return { plan: { id: "p1", subject: "Rowan", terms: ["Rowan"], memories: [{ id: "m-01", content: MEMORIES[0].content, why: "mentions Rowan" }], documents: [], protected_documents: [], related: { enrichments: 0, connections: 0, flags: 0, entity_mentions: 0, entities: 0, graph_queue: 0 }, conversations: { count: 0, titles: [] } } };
      }
      return { result: { subject: "Rowan", plan_id: "p1", deleted: { memories: 1, documents: 0, enrichments: 0, connections: 0, flags: 0, entity_mentions: 0, entities: 0, graph_queue: 0 }, still_matching: { memories: 0, documents: 0 }, protected_documents: [], conversations_mentioning: 0 } };
    }
    if (/\/api\/memories\/m-\d+$/.test(url)) return MEMORIES.find((m) => url.endsWith(m.id));
    if (url.includes("/api/memories")) {
      const u = new URL(url, "http://x");
      const type = u.searchParams.get("memory_type");
      const rows = type ? MEMORIES.filter((m) => m.memory_type === type) : MEMORIES;
      return { memories: rows, stats: STATS, pagination: { limit: 20, offset: 0, total: rows.length } };
    }
    return undefined;
  });
});

afterEach(() => {
  cleanup();
  restoreFetch();
});

describe("MemoryBrowser", () => {
  it("leads each card with how he came by it and whom it is about, without importance or access", async () => {
    render(<MemoryBrowser />);
    const text = await screen.findByText("Ellis's partner is Rowan.");
    const card = text.closest("button") as HTMLElement;
    assert.ok(card, "the whole card is a button");
    assert.ok(within(card).getByText("he heard"));
    assert.ok(within(card).getByText("about you"));
    assert.match(card.textContent ?? "", /Known · reinforced 14×/);
    assert.doesNotMatch(card.textContent ?? "", /Importance|95%|Accessed|42x/i);
    const mine = (await screen.findByText("Watch Biscuit's PM dose adherence this week.")).closest("button") as HTMLElement;
    assert.ok(within(mine).getByText("he thought"));
    assert.ok(within(mine).getByText("about himself"));
  });

  it("puts the tiers in one segment with counts", async () => {
    render(<MemoryBrowser />);
    const group = await screen.findByRole("radiogroup", { name: "How sure he is" });
    const radios = within(group).getAllByRole("radio");
    assert.deepEqual(radios.map((r) => r.textContent?.replace(/\s+/g, " ").trim()), ["All 4.4k", "Known 1.2k", "Believed 1k", "Noticed 2.2k"]);
  });

  it("opens where a memory came from when the card is tapped, with the details tucked inside", async () => {
    render(<MemoryBrowser />);
    fireEvent.click((await screen.findByText("Ellis's partner is Rowan.")).closest("button") as HTMLElement);
    const dialog = await screen.findByRole("dialog", { name: "Where this came from" });
    await within(dialog).findByText("Stored from “Weekend plans”.");
    assert.ok(within(dialog).getByText(/Importance/));
    assert.ok(within(dialog).getByText("95%"));
  });

  it("opens ?id= directly", async () => {
    render(<MemoryBrowser initialId="m-12" />);
    const dialog = await screen.findByRole("dialog", { name: "Where this came from" });
    await within(dialog).findByText("Watch Biscuit's PM dose adherence this week.");
    assert.ok(calls.some((c) => c.url.includes("/api/memories/m-12/provenance")));
  });

  it("filters by type from one Filter sheet, intention included", async () => {
    render(<MemoryBrowser />);
    await screen.findByText("Ellis's partner is Rowan.");
    fireEvent.click(screen.getByRole("button", { name: /^Filter/ }));
    const dialog = await screen.findByRole("dialog", { name: "Filter memories" });
    fireEvent.click(within(dialog).getByRole("radio", { name: "Intention" }));
    await waitFor(() => assert.ok(calls.some((c) => c.url.includes("memory_type=intention"))));
    assert.ok(screen.getByRole("button", { name: /^Filter · 1/ }));
  });

  it("searches as Ellis types (debounced), with a 16px input", async () => {
    render(<MemoryBrowser />);
    const input = await screen.findByRole("searchbox", { name: "Search memories" });
    assert.match(input.className, /text-\[16px\]/);
    fireEvent.change(input, { target: { value: "Biscuit" } });
    await waitFor(() => assert.ok(calls.some((c) => c.url.includes("query=Biscuit"))), { timeout: 2000 });
  });

  it("forgets through a preview and a confirm sheet", async () => {
    render(<MemoryBrowser />);
    await screen.findByText("Ellis's partner is Rowan.");
    fireEvent.click(screen.getByRole("button", { name: /Forget/ }));
    const sheet = await screen.findByRole("dialog", { name: "Forget someone or something" });
    fireEvent.change(within(sheet).getByLabelText(/Who or what/), { target: { value: "Rowan" } });
    fireEvent.click(within(sheet).getByRole("button", { name: "Preview" }));
    await within(sheet).findByText(/1 memory would be deleted/);
    fireEvent.click(within(sheet).getByRole("button", { name: "Forget all of it…" }));
    const confirm = await screen.findByRole("dialog", { name: "Forget “Rowan”?" });
    fireEvent.click(within(confirm).getByRole("button", { name: "Forget" }));
    await screen.findByText(/Deleted 1 memory\. Checked again: nothing still matches\./);
    assert.ok(calls.some((c) => c.method === "POST" && (c.body as { confirm?: boolean }).confirm === true));
  });

  it("says a failed preview in a Notice, not red text", async () => {
    forgetFails = true;
    render(<MemoryBrowser />);
    await screen.findByText("Ellis's partner is Rowan.");
    fireEvent.click(screen.getByRole("button", { name: /Forget/ }));
    const sheet = await screen.findByRole("dialog", { name: "Forget someone or something" });
    fireEvent.change(within(sheet).getByLabelText(/Who or what/), { target: { value: "Rowan" } });
    fireEvent.click(within(sheet).getByRole("button", { name: "Preview" }));
    const alert = await within(sheet).findByRole("alert");
    assert.match(alert.textContent ?? "", /The memory store is busy\./);
  });
});
