// "Screen off" on the hub survives a deploy (2026-09-27 review of H3). It is
// page state only: the page gives up the wake lock and stretches the idle
// redirect so the panel can go dark for the night. Nobody touches a
// blacked-out screen, so the kiosk read as idle and a deploy's reload
// brought back the bright settings page, took the wake lock again and, two
// minutes later, the photo screensaver: lit all night. It holds reloads.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { createRequire } from "node:module";
import { reloadHeld } from "../lib/buildReload.ts";

// The page imports hub.css (Next bundles it); node would parse it as JS.
const req = createRequire(__filename);
(req.extensions as Record<string, (m: { exports: unknown }) => void>)[".css"] = (m) => {
  m.exports = {};
};
const HubSettingsPage = (req("../app/(display)/hub/settings/page.tsx") as { default: React.ComponentType }).default;

(globalThis as unknown as { React: typeof React }).React = React;
const originalFetch = globalThis.fetch;
const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("the hub's Screen off", () => {
  it("holds a new build's reload while the screen is off", async () => {
    globalThis.fetch = (async () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } })) as typeof fetch;
    render(
      <AppRouterContext.Provider value={router as never}>
        <PathnameContext.Provider value="/hub/settings">
          <SearchParamsContext.Provider value={new URLSearchParams() as never}>
            <HubSettingsPage />
          </SearchParamsContext.Provider>
        </PathnameContext.Provider>
      </AppRouterContext.Provider>,
    );
    assert.equal(reloadHeld(), false);
    const off = screen.getByRole("button", { name: /Screen off/ });
    fireEvent.click(off);
    if (!screen.queryByRole("button", { name: "Screen off — tap to wake" })) fireEvent.click(screen.getByRole("button", { name: /Screen off|tap again/i }));
    await act(async () => void (await new Promise((r) => setTimeout(r, 10))));
    assert.ok(screen.queryByRole("button", { name: "Screen off — tap to wake" }), "the screen is off");
    assert.equal(reloadHeld(), true, "a reload would light the panel back up for the night");
  });
});
