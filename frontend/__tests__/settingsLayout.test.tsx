// R/G red: every /settings/* page must live inside a scroll container.
//
// The app shell's <main> is `overflow-hidden` (the chat page owns its own
// scrolling). The Edward dashboard wrapped itself in `overflow-y-auto`,
// but the A–Z list, the rooms and the individual panels did not — on a
// phone the list was simply clipped at the bottom and could not scroll.
// A settings layout now provides the scroll container for all of them.
//
// 2026-09-26 (the Edward tab's frame): on md+ every settings route is a
// persistent two-pane layout — a rail of rooms and their panels on the
// left, the page on the right. The rail used to live only in the panel
// layout and sat inside the page's scroll container, so it scrolled away
// with the content. The frame is now a height-bounded flex row: the rail
// and the page each scroll on their own.

import { describe, it, afterEach, beforeEach } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render, screen, fireEvent, cleanup, act, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import SettingsLayout from "../app/(app)/settings/layout.tsx";
import { SettingsSidebar } from "../components/settings/SettingsSidebar.tsx";
import { edwardTabStore } from "../components/settings/useEdwardTabStatus.ts";

const pushed: string[] = [];
const router = {
  push: (href: string) => pushed.push(href),
  replace: () => {},
  back: () => {},
  forward: () => {},
  refresh: () => {},
  prefetch: () => {},
};

const originalFetch = globalThis.fetch;
const originalMatchMedia = window.matchMedia;
let fetches: string[] = [];

/** The rail only reads its status on md+ (on a phone it is hidden). */
function setWide(wide: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: /min-width:\s*768px/.test(query) ? wide : false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

beforeEach(() => {
  pushed.length = 0;
  fetches = [];
  edwardTabStore.reset();
  setWide(true);
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    fetches.push(path);
    if (path.startsWith("/api/debug/memories/stats")) return json({ total: 4843, created_last_24h: 37, by_type: {}, by_tier: {}, average_importance: 0.5 });
    if (path.startsWith("/api/hub/health"))
      return json({ overall: "problem", watchdog: { status: "problem", stale: false, age_minutes: 1, checked_at: null, problems: ["Postgres is down"], repairs: [] }, agents: [], components: [], host: "mini", checked_at: "2026-09-26T18:00:00Z" });
    return json({}, 404);
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
  window.matchMedia = originalMatchMedia;
});

function at(pathname: string, ui: React.ReactNode) {
  return (
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value={pathname}>{ui}</PathnameContext.Provider>
    </AppRouterContext.Provider>
  );
}

const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));

describe("settings layout", () => {
  it("wraps every settings page in a vertical scroll container", () => {
    const { getByTestId } = render(
      at(
        "/settings",
        <SettingsLayout>
          <div data-testid="page">hello</div>
        </SettingsLayout>,
      ),
    );
    const wrapper = getByTestId("page").parentElement!;
    const cls = wrapper.className.split(/\s+/);
    assert.ok(cls.includes("overflow-y-auto"), `got ${wrapper.className}`);
    assert.ok(cls.includes("min-h-0"), "must be allowed to shrink inside the flex column");
    assert.ok(cls.includes("flex-1"), "must fill the shell's remaining height");
  });

  it("bounds its height in a flex row so the rail never scrolls away with the page", () => {
    const { getByTestId } = render(
      at(
        "/settings/memories",
        <SettingsLayout>
          <div data-testid="page">hello</div>
        </SettingsLayout>,
      ),
    );
    const frame = getByTestId("page").parentElement!.parentElement!;
    const cls = frame.className.split(/\s+/);
    for (const c of ["flex", "flex-1", "min-h-0", "overflow-hidden"]) assert.ok(cls.includes(c), `frame needs ${c}: ${frame.className}`);
    const rail = screen.getByRole("complementary", { name: "Edward's rooms" });
    assert.ok(rail.parentElement === frame, "the rail is the page's sibling, outside its scroll container");
    assert.match(rail.className, /\bhidden\b/, "no rail on a phone");
    assert.match(rail.className, /\bmd:flex\b/);
    assert.match(rail.className, /\bmin-h-0\b/);
  });
});

describe("the rail", () => {
  it("lists the five rooms, each with its panels beneath", () => {
    render(at("/settings", <SettingsSidebar />));
    const rail = screen.getByRole("complementary", { name: "Edward's rooms" });
    for (const [room, label] of [["mind", "Mind"], ["memory", "Memory"], ["attention", "Attention"], ["work", "Work"], ["house", "House"]]) {
      const link = within(rail).getByRole("link", { name: new RegExp(`^${label}$`) });
      assert.equal(link.getAttribute("href"), `/settings/room/${room}`);
    }
    assert.equal(within(rail).getByRole("link", { name: "Thoughts" }).getAttribute("href"), "/settings/thoughts");
    assert.equal(within(rail).getByRole("link", { name: /All settings/ }).getAttribute("href"), "/settings/all");
  });

  it("highlights the panel on a panel page and the room on a room page", () => {
    const { unmount } = render(at("/settings/memories", <SettingsSidebar />));
    assert.equal(screen.getByRole("link", { name: "Memories" }).getAttribute("aria-current"), "page");
    assert.equal(screen.getByRole("link", { name: /^Memory$/ }).getAttribute("aria-current"), null);
    unmount();
    render(at("/settings/room/memory", <SettingsSidebar />));
    assert.equal(screen.getByRole("link", { name: /^Memory$/ }).getAttribute("aria-current"), "page");
    assert.equal(screen.getByRole("link", { name: "Memories" }).getAttribute("aria-current"), null);
  });

  it("finds a setting A–Z from the top and opens the first hit on Enter", () => {
    render(at("/settings", <SettingsSidebar />));
    const box = screen.getByRole("searchbox", { name: "Find a setting" });
    fireEvent.change(box, { target: { value: "token" } });
    const results = screen.getByRole("list", { name: "Matching settings" });
    const links = within(results).getAllByRole("link");
    assert.equal(links.length, 1);
    assert.equal(links[0].getAttribute("href"), "/settings/usage");
    assert.match(links[0].textContent ?? "", /Work/, "the room is named beside the hit");
    fireEvent.keyDown(box, { key: "Enter" });
    assert.deepEqual(pushed, ["/settings/usage"]);
  });

  it("still finds a panel by the category it had before the regroup", () => {
    render(at("/settings", <SettingsSidebar />));
    fireEvent.change(screen.getByRole("searchbox", { name: "Find a setting" }), { target: { value: "integrations" } });
    const hrefs = within(screen.getByRole("list", { name: "Matching settings" }))
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    assert.ok(hrefs.includes("/settings/gmail"), hrefs.join(","));
  });

  it("puts each room's health on its dot", async () => {
    render(at("/settings", <SettingsSidebar />));
    await act(async () => {
      await tick(30);
    });
    const house = screen.getByRole("link", { name: /^House$/ });
    const dot = house.querySelector("[data-tone]");
    assert.ok(dot, "a dot");
    assert.equal(dot!.getAttribute("data-tone"), "bad");
    assert.match(house.getAttribute("title") ?? "", /Needs you · Postgres is down/);
  });

  it("fetches nothing on a phone, where it is hidden", async () => {
    setWide(false);
    render(at("/settings/memories", <SettingsSidebar />));
    await act(async () => {
      await tick(30);
    });
    assert.deepEqual(fetches, []);
  });
});
