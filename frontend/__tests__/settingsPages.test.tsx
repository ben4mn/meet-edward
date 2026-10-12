// R/G red for the Edward tab's navigation (2026-09-26).
//
//  - A panel page is framed by PanelShell: one breadcrumb back to its room
//    ("‹ Memory"), its title and one line — no "Edward menu" bar, no mono
//    header, no second back link to /settings.
//  - A room page has one breadcrumb back to Edward, an intro line, its live
//    view, then its panels as rows (Mind leads with Thoughts).
//  - The A–Z page names the room first on each row and never shows the
//    pre-redesign "was …" label (it stays searchable).

import { describe, it, afterEach, beforeEach } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import SettingsPanelPage from "../app/(app)/settings/[slug]/page.tsx";
import RoomPage from "../app/(app)/settings/room/[room]/page.tsx";
import AllSettingsPage from "../app/(app)/settings/all/page.tsx";
import { edwardTabStore } from "../components/settings/useEdwardTabStatus.ts";

const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };
const originalFetch = globalThis.fetch;

beforeEach(() => {
  edwardTabStore.reset();
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({}), { status: 404, headers: { "content-type": "application/json" } })) as typeof fetch;
});
afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

function at(pathname: string, ui: React.ReactNode) {
  return (
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value={pathname}>{ui}</PathnameContext.Provider>
    </AppRouterContext.Provider>
  );
}

function atQuery(pathname: string, query: string, ui: React.ReactNode) {
  return (
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value={pathname}>
        <SearchParamsContext.Provider value={new URLSearchParams(query)}>{ui}</SearchParamsContext.Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>
  );
}

function linksTo(href: string): HTMLElement[] {
  return screen.queryAllByRole("link").filter((a) => a.getAttribute("href") === href);
}

describe("a panel page", () => {
  it("breadcrumbs to its room, with one title and no Edward-menu bar", () => {
    render(at("/settings/locale", <SettingsPanelPage params={{ slug: "locale" }} />));
    const crumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    const back = within(crumb).getByRole("link");
    assert.equal(back.getAttribute("href"), "/settings/room/house");
    assert.match(back.textContent ?? "", /House/);
    const h1 = screen.getByRole("heading", { level: 1 });
    assert.equal(h1.textContent, "Locale");
    assert.doesNotMatch(h1.className, /font-mono/);
    assert.equal(screen.queryByRole("button", { name: /Edward menu/ }), null);
    assert.equal(linksTo("/settings").length, 0, "no second back link to the landing");
  });
});

describe("a room page", () => {
  it("has one way back to Edward, an intro, and its panels as rows (Mind leads with Thoughts)", () => {
    render(at("/settings/room/mind", <RoomPage params={{ room: "mind" }} searchParams={{}} />));
    assert.equal(linksTo("/settings").length, 1);
    assert.equal(screen.getByRole("heading", { level: 1 }).textContent, "Mind");
    const panels = screen.getByRole("list", { name: "Settings in Mind" });
    const hrefs = within(panels).getAllByRole("link").map((a) => a.getAttribute("href"));
    assert.deepEqual(hrefs.slice(0, 2), ["/settings/thoughts", "/settings/journal"]);
    assert.equal(screen.queryByRole("button", { name: /Edward menu/ }), null);
  });

  it("opens the Memory room's search on the query it was sent", () => {
    render(at("/settings/room/memory", <RoomPage params={{ room: "memory" }} searchParams={{ q: "biscuit" }} />));
    const box = screen.getByRole("searchbox", { name: "Search everything Edward knows" }) as HTMLInputElement;
    assert.equal(box.value, "biscuit");
  });
});

describe("the A–Z page", () => {
  it("names the room first on each row and never shows 'was …'", () => {
    render(at("/settings/all", <AllSettingsPage />));
    const list = screen.getByRole("list", { name: "All settings" });
    const rows = within(list).getAllByRole("link");
    assert.equal(rows.length, 21);
    const memories = rows.find((a) => a.getAttribute("href") === "/settings/memories")!;
    assert.match(memories.textContent ?? "", /Memory · What he remembers/);
    assert.doesNotMatch(document.body.textContent ?? "", /\bwas (Core|Integrations|Automation|System)\b/);
  });

  it("still finds a panel by its old category", () => {
    render(at("/settings/all", <AllSettingsPage />));
    fireEvent.change(screen.getByRole("searchbox", { name: "Find a setting" }), { target: { value: "integrations" } });
    const hrefs = within(screen.getByRole("list", { name: "All settings" }))
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    assert.ok(hrefs.includes("/settings/gmail"));
    assert.ok(!hrefs.includes("/settings/memories"));
  });
});

// Review 2026-09-26 (nested-subview-back): a sub-view has one way back — to
// its parent view, not the room — and one title.
import { panelFrame } from "../components/settings/panelFrame.ts";
import { readFileSync } from "node:fs";

describe("a panel's sub-view", () => {
  it("panelFrame: the parent view is the breadcrumb, the sub-view the title", () => {
    const q = (s: string) => new URLSearchParams(s);
    assert.deepEqual(panelFrame("heartbeat", q("view=log")), { title: "Full log", back: { href: "/settings/heartbeat", label: "Listening" } });
    assert.deepEqual(panelFrame("heartbeat", q("view=config")), { title: "Configuration", back: { href: "/settings/heartbeat", label: "Listening" } });
    assert.deepEqual(panelFrame("documents", q("id=d-cabin")), { title: null, back: { href: "/settings/documents", label: "Documents" } });
    assert.deepEqual(panelFrame("heartbeat", q("")), {});
    assert.deepEqual(panelFrame("documents", null), {});
    assert.deepEqual(panelFrame("locale", q("view=log")), {});
  });

  it("the heartbeat log breadcrumbs to Listening with 'Full log' as its one title", () => {
    render(atQuery("/settings/heartbeat", "view=log", <SettingsPanelPage params={{ slug: "heartbeat" }} />));
    const crumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    const back = within(crumb).getByRole("link");
    assert.equal(back.getAttribute("href"), "/settings/heartbeat");
    assert.match(back.textContent ?? "", /Listening/);
    assert.equal(screen.getByRole("heading", { level: 1 }).textContent, "Full log");
  });

  it("the panels no longer draw a second back row of their own", () => {
    const hb = readFileSync(new URL("../components/settings/HeartbeatPanel.tsx", import.meta.url), "utf8");
    const docs = readFileSync(new URL("../components/settings/DocumentBrowser.tsx", import.meta.url), "utf8");
    assert.equal(/What I decided today/.test(hb), false);
    assert.equal(/All documents/.test(docs), false);
  });
});
