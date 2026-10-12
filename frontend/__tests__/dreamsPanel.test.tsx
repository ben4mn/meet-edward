// Settings → Memory → Dreaming (2026-09-26, Jarvis polish).
//
// - Contradiction notes on Today link to /settings/dreaming?flag=<id>: the
//   panel opens that flag as "Which is right?" with both memories and
//   Keep this / Keep that / Both are fine (the existing resolve calls).
// - &ask=rewrite is a proposed rewrite: no keep-one choice.
// - ?tab=flags opens the review list. Tokens only (no zinc/gray/indigo).

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { mockFetch, restoreFetch, type Call } from "./panelTestKit.ts";
import { DreamsPanel } from "../components/settings/DreamsPanel.tsx";

const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();

const STATUS = {
  running: true, enabled: true, interval_seconds: 3600, lookback_hours: 6, last_run: iso(38 * 60_000),
  next_run: new Date(Date.now() + 22 * 60_000).toISOString(), cycle_count: 912, total_connections: 3814,
  total_flags: 211, max_cluster_size: 12, max_pairs_per_cluster: 6, max_llm_calls_per_cycle: 30,
};

function flags() {
  return [
    { id: "fl-1", memory_id: "m-02", flag_type: "contradiction", description: "Two different Carprofen strengths for Biscuit.",
      related_memory_id: "m-02b", resolved: false, resolved_at: null, created_at: iso(5 * 3600_000),
      memory_content: "Biscuit takes Carprofen 100mg twice a day.", related_memory_content: "Biscuit takes Carprofen 300mg in the evening." },
    { id: "fl-3", memory_id: "m-14", flag_type: "stale", description: "Thermostat advice is seasonal.", related_memory_id: null,
      resolved: false, resolved_at: null, created_at: iso(2 * 86400_000) },
  ];
}

let calls: Call[] = [];

beforeEach(() => {
  calls = mockFetch((url, method) => {
    if (url.includes("/api/consolidation/status")) return STATUS;
    if (url.includes("/api/consolidation/flags/") && url.endsWith("/resolve")) return { ...flags()[0], resolved: true };
    if (url.includes("/api/consolidation/flags/") && method === "PATCH") return { ...flags()[0], resolved: true };
    if (url.includes("/api/consolidation/flags")) {
      // The first page asks for open flags only; a deep link past it looks
      // through every flag, settled ones included.
      if (url.includes("resolved=false")) return flags();
      return [...flags(), { id: "fl-99", memory_id: "m-30", flag_type: "contradiction", description: "Old one.", related_memory_id: "m-31",
        resolved: true, resolved_at: iso(86400_000), created_at: iso(3 * 86400_000) }];
    }
    if (url.match(/\/api\/memories\/m-0[23]$/)) {
      const id = url.slice(-4);
      return { id, content: id === "m-02" ? "Biscuit takes Carprofen 100mg twice a day, with food." : "Biscuit's med protocol is on the fridge: 06:00 and 18:00 sharp, never skipped." };
    }
    if (url.includes("/api/consolidation/connections")) return [
      { id: "cn-1", memory_id_a: "m-02", memory_id_b: "m-03", connection_type: "elaborates", strength: 0.92, created_at: iso(3 * 3600_000) },
    ];
    if (url.includes("/api/notes")) return { notes: [
      { id: "n-flag-1", title: "Which is right?", body: "Is Biscuit's Carprofen 100mg twice a day, or 300mg in the evening?", href: "/settings/dreaming?flag=fl-1" },
    ] };
    return undefined;
  });
});

afterEach(() => {
  cleanup();
  restoreFetch();
});

describe("DreamsPanel", () => {
  it("opens ?flag= as 'Which is right?' with both memories and the question he asked", async () => {
    render(<DreamsPanel initialFlag="fl-1" />);
    const dialog = await screen.findByRole("dialog", { name: "Which is right?" });
    await within(dialog).findByText("Is Biscuit's Carprofen 100mg twice a day, or 300mg in the evening?");
    assert.ok(within(dialog).getByText("Biscuit takes Carprofen 100mg twice a day."));
    assert.ok(within(dialog).getByText("Biscuit takes Carprofen 300mg in the evening."));
    assert.ok(within(dialog).getByRole("button", { name: "Keep this" }));
    assert.ok(within(dialog).getByRole("button", { name: "Keep that" }));
    assert.ok(within(dialog).getByRole("button", { name: "Both are fine" }));
  });

  it("keeps the side Ellis picks with the resolve call", async () => {
    render(<DreamsPanel initialFlag="fl-1" />);
    const dialog = await screen.findByRole("dialog", { name: "Which is right?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep that" }));
    await waitFor(() => {
      const c = calls.find((x) => x.url.endsWith("/api/consolidation/flags/fl-1/resolve"));
      assert.ok(c, "resolve was called");
      assert.equal(c!.method, "POST");
      assert.deepEqual(c!.body, { keep: "b" });
    });
    await waitFor(() => assert.equal(screen.queryByRole("dialog", { name: "Which is right?" }) === null, true));
  });

  it("'Both are fine' settles the flag without retiring either", async () => {
    render(<DreamsPanel initialFlag="fl-1" />);
    const dialog = await screen.findByRole("dialog", { name: "Which is right?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Both are fine" }));
    await waitFor(() => {
      const c = calls.find((x) => x.url.endsWith("/api/consolidation/flags/fl-1") && x.method === "PATCH");
      assert.ok(c);
      assert.deepEqual(c!.body, { resolved: true });
    });
    assert.equal(calls.some((x) => x.url.endsWith("/resolve")), false);
  });

  it("offers no keep-one choice for a proposed rewrite", async () => {
    render(<DreamsPanel initialFlag="fl-1" initialAsk="rewrite" />);
    const dialog = await screen.findByRole("dialog", { name: "Reword this?" });
    assert.equal(within(dialog).queryByRole("button", { name: "Keep this" }) === null, true);
    assert.ok(within(dialog).getByRole("link", { name: /Tell Edward/ }));
    assert.ok(within(dialog).getByRole("button", { name: "Leave it as is" }));
  });

  it("says a settled flag is settled", async () => {
    render(<DreamsPanel initialFlag="fl-99" />);
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(/already settled — nothing left to choose/i);
  });

  it("opens the review list from ?tab=flags, in plain words", async () => {
    render(<DreamsPanel initialTab="review" />);
    await screen.findByText("Which is right?");
    assert.ok(screen.getByText("Might be out of date"));
    const tab = screen.getByRole("radio", { name: /To review/ });
    assert.equal(tab.getAttribute("aria-checked"), "true");
  });

  it("switches dreaming with a real switch and uses tokens only", async () => {
    const { container } = render(<DreamsPanel />);
    const sw = await screen.findByRole("switch", { name: "Dreaming" });
    assert.equal(sw.getAttribute("aria-checked"), "true");
    assert.doesNotMatch(container.innerHTML, /\b(zinc|gray|slate|indigo)-\d/);
  });

  it("says the next dream is due, not '10m ago', when it is running late", async () => {
    restoreFetch();
    mockFetch((url) => {
      if (url.includes("/api/consolidation/status")) return { ...STATUS, next_run: iso(10 * 60_000) };
      if (url.includes("/api/consolidation")) return [];
      return undefined;
    });
    render(<DreamsPanel />);
    const line = await screen.findByText(/Dreamed 38m ago/);
    assert.match(line.textContent ?? "", /next any minute/);
    assert.doesNotMatch(line.textContent ?? "", /next \d+m ago/);
  });

  it("shows what each connection links, in words, each side opening its memory", async () => {
    render(<DreamsPanel />);
    fireEvent.click(await screen.findByRole("radio", { name: /Connections/ }));
    const a = await screen.findByRole("link", { name: /Biscuit takes Carprofen 100mg twice a day/ });
    assert.equal(a.getAttribute("href"), "/settings/memories?id=m-02");
    const b = await screen.findByRole("link", { name: /Biscuit's med protocol is on the fridge/ });
    assert.equal(b.getAttribute("href"), "/settings/memories?id=m-03");
    assert.ok(screen.getByText(/adds detail to/));
    assert.equal(screen.queryByRole("link", { name: /^(First|Second)( memory)?$/ }), null);
  });

  it("doesn't call a flag settled when the flags could not be read", async () => {
    restoreFetch();
    mockFetch((url) => {
      if (url.includes("/api/consolidation/status")) return STATUS;
      if (url.includes("/api/consolidation/flags")) return new Response("{}", { status: 500 });
      if (url.includes("/api/consolidation/connections")) return [];
      return undefined;
    });
    render(<DreamsPanel initialFlag="fl-1" />);
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(/can't load that question right now/i);
    assert.ok(within(dialog).getByRole("button", { name: "Try again" }));
    assert.equal(within(dialog).queryByText(/already settled/i), null);
  });

  it("finds an open flag past the first page before calling it settled", async () => {
    restoreFetch();
    mockFetch((url) => {
      if (url.includes("/api/consolidation/status")) return STATUS;
      if (url.includes("/api/consolidation/flags")) {
        if (url.includes("resolved=false")) return flags();
        return [...flags(), { id: "fl-77", memory_id: "m-40", flag_type: "contradiction", description: "Two birthdays for Alex.",
          related_memory_id: "m-41", resolved: false, resolved_at: null, created_at: iso(40 * 86400_000),
          memory_content: "Alex's birthday is March 3.", related_memory_content: "Alex's birthday is March 30." }];
      }
      if (url.includes("/api/consolidation/connections")) return [];
      return undefined;
    });
    render(<DreamsPanel initialFlag="fl-77" />);
    const dialog = await screen.findByRole("dialog", { name: "Which is right?" });
    await within(dialog).findByText("Alex's birthday is March 30.");
  });

  it("reads a deep-linked flag directly (GET /flags/{id}) instead of searching every flag", async () => {
    restoreFetch();
    const seen: string[] = [];
    mockFetch((url) => {
      seen.push(url);
      if (url.includes("/api/consolidation/status")) return STATUS;
      if (url.includes("/api/consolidation/flags/fl-500")) {
        return { id: "fl-500", memory_id: "m-50", flag_type: "contradiction", description: "Two vets.", related_memory_id: "m-51",
          resolved: false, resolved_at: null, created_at: iso(90 * 86400_000),
          memory_content: "Biscuit's vet is Dr. Park.", related_memory_content: "Biscuit's vet is Dr. Patel." };
      }
      if (url.includes("/api/consolidation/flags")) return url.includes("resolved=false") ? flags() : [];
      if (url.includes("/api/consolidation/connections")) return [];
      return undefined;
    });
    render(<DreamsPanel initialFlag="fl-500" />);
    const dialog = await screen.findByRole("dialog", { name: "Which is right?" });
    await within(dialog).findByText("Biscuit's vet is Dr. Patel.");
    assert.equal(seen.some((u) => u.includes("limit=200")), false, "no 200-flag search");
  });

  it("a flag the server does not have (404) is 'Can't find it', not 'settled'", async () => {
    restoreFetch();
    mockFetch((url) => {
      if (url.includes("/api/consolidation/status")) return STATUS;
      if (url.includes("/api/consolidation/flags/fl-gone")) return new Response(JSON.stringify({ detail: "Flag not found" }), { status: 404 });
      if (url.includes("/api/consolidation/flags")) return url.includes("resolved=false") ? flags() : [];
      if (url.includes("/api/consolidation/connections")) return [];
      return undefined;
    });
    render(<DreamsPanel initialFlag="fl-gone" />);
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(/Can't find it/);
    assert.equal(within(dialog).queryByText(/already settled/i), null);
  });
});
