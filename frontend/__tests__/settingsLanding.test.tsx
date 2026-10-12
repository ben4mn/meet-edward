// R/G red for the Edward tab's landing — "what he knows, watches and does".
//
//  - A hero: Edward (the one live one on this screen while he is in view),
//    his honest status, and the opening of his own "Edward: state" page in
//    his voice, with "Read all".
//  - Three answers that each open a room: Knows (memories, with a search
//    field that goes to the Memory room), Watches (intentions and what
//    waits on Ellis), Does (his last wake, with his thinking to read).
//  - The five rooms once — not a health strip and a rooms card — and
//    "All settings" once.

import { describe, it, afterEach, beforeEach } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render, screen, cleanup, fireEvent, act, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { SettingsDashboard } from "../components/settings/SettingsDashboard.tsx";
import { ThemeProvider } from "../lib/theme.tsx";
import { edwardTabStore } from "../components/settings/useEdwardTabStatus.ts";

const pushed: string[] = [];
const router = {
  push: (h: string) => pushed.push(h),
  replace: () => {},
  back: () => {},
  forward: () => {},
  refresh: () => {},
  prefetch: () => {},
};
const originalFetch = globalThis.fetch;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const MIND_NOW = {
  now: new Date().toISOString(),
  waking: null,
  last: { at: new Date(Date.now() - 12 * 60_000).toISOString(), reason: "mention", outcome: "acted", line: "Replied to Parker about the flight.", thought_id: "t-reply-parker" },
  recent: [{ at: new Date(Date.now() - 12 * 60_000).toISOString(), reason: "mention", outcome: "acted", line: "Replied to Parker about the flight.", thought_id: "t-reply-parker" }],
  next_wake_at: null,
  approvals_waiting: 1,
  unread_thoughts: 0,
  quota: { used_percent: 40, exhausted: false, resets_at: null },
  hands: { connected: true, down_for_s: null },
};

beforeEach(() => {
  pushed.length = 0;
  edwardTabStore.reset();
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (path.startsWith("/api/debug/memories/stats")) return json({ total: 4812, created_last_24h: 23, by_type: {}, by_tier: {}, average_importance: 0.5 });
    if (path.startsWith("/api/documents"))
      return json({ documents: [{ id: "d-state", title: "Edward: state", content: "I'm Edward. I keep Ellis's days light. I watch the pets too.\n\n## Watching\n- Biscuit", tags: "state,self", updated_at: new Date().toISOString() }] });
    if (path.startsWith("/api/mind/intentions")) return json({ intentions: [{ id: "i1", content: "a" }, { id: "i2", content: "b" }, { id: "i3", content: "c" }] });
    if (path.startsWith("/api/mind/now")) return json(MIND_NOW);
    return json({}, 404);
  }) as typeof fetch;
});
afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

function page() {
  return render(
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value="/settings">
        <ThemeProvider>
          <SettingsDashboard />
        </ThemeProvider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>,
  );
}

const settle = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 40));
  });
};

describe("the Edward tab landing", () => {
  it("opens with Edward, his status and his own words", async () => {
    page();
    await settle();
    assert.equal(screen.getByRole("heading", { level: 1 }).textContent, "Edward");
    const hero = screen.getByRole("region", { name: "Edward now" });
    assert.ok(hero.querySelector(".pane-avatar"), "Edward himself is in the hero");
    assert.ok(within(hero).getByText("I'm Edward. I keep Ellis's days light."));
    assert.ok(within(hero).getByRole("button", { name: /Read all/ }));
  });

  it("opens his whole page from Read all (the markdown renderer loads on demand)", async () => {
    page();
    await settle();
    const hero = screen.getByRole("region", { name: "Edward now" });
    fireEvent.click(within(hero).getByRole("button", { name: /Read all/ }));
    const dialog = await screen.findByRole("dialog");
    const heading = await within(dialog).findByRole("heading", { name: "Watching" }, { timeout: 3000 });
    assert.ok(heading, "the full page renders as markdown in the sheet");
  });

  it("answers what he knows, watches and does, each opening a room", async () => {
    page();
    await settle();
    const answers = screen.getByRole("region", { name: "What he knows, watches and does" });
    const knows = within(answers).getByRole("link", { name: /Knows/ });
    assert.equal(knows.getAttribute("href"), "/settings/room/memory");
    assert.match(knows.textContent ?? "", /4,812 memories · 23 today/);
    const watches = within(answers).getByRole("link", { name: /Watches/ });
    assert.equal(watches.getAttribute("href"), "/settings/room/attention");
    assert.match(watches.textContent ?? "", /3 intentions · 1 waiting on you/);
    const does = within(answers).getByRole("link", { name: /Does/ });
    assert.equal(does.getAttribute("href"), "/settings/room/work");
    assert.match(does.textContent ?? "", /Woke 12m ago · acted/);
    const thinking = within(answers).getByRole("link", { name: /Read his thinking/ });
    assert.equal(thinking.getAttribute("href"), "/chat?c=t-reply-parker");
  });

  it("sends a Knows search to the Memory room", async () => {
    page();
    await settle();
    const box = screen.getByRole("searchbox", { name: "Search what he knows" });
    fireEvent.change(box, { target: { value: "biscuit meds" } });
    fireEvent.submit(box.closest("form")!);
    assert.deepEqual(pushed, ["/settings/room/memory?q=biscuit%20meds"]);
  });

  it("lists the five rooms once, and All settings once", async () => {
    const { container } = page();
    await settle();
    assert.equal(container.querySelectorAll("[data-room-row]").length, 5);
    for (const label of ["Mind", "Memory", "Attention", "Work", "House"]) {
      assert.equal(screen.getAllByText(label, { selector: "[data-room-row] *" }).length, 1, label);
    }
    const all = screen.getAllByRole("link").filter((a) => a.getAttribute("href") === "/settings/all");
    assert.equal(all.length, 1);
    assert.doesNotMatch(container.textContent ?? "", /temp 0\.7|gpt-/);
  });

  it("has You (appearance, sign out) after the rooms", async () => {
    page();
    await settle();
    const you = screen.getByRole("region", { name: "You" });
    assert.ok(within(you).getByRole("radiogroup", { name: "Appearance" }));
    assert.ok(within(you).getByRole("button", { name: /Sign out/ }));
    const rooms = screen.getByRole("region", { name: "His rooms" });
    assert.ok(rooms.compareDocumentPosition(you) & Node.DOCUMENT_POSITION_FOLLOWING, "You comes after the rooms");
  });
});
