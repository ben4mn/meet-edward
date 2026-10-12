// Old or unknown settings URLs (review 2026-09-26, settings-404-raw):
// /settings/evolution, /settings/dreams and /settings/wakeup showed Next's
// bare "404 | This page could not be found." Retired panels now go to where
// their work lives, and anything else under /settings says so in Edward's
// voice, with the rooms and All settings one tap away.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { movedTo } from "../components/settings/settingsRedirects.ts";
import SettingsNotFound from "../app/(app)/settings/not-found.tsx";
import SettingsPanelPage from "../app/(app)/settings/[slug]/page.tsx";

afterEach(() => cleanup());

describe("movedTo", () => {
  it("sends retired panels to where their work lives now", () => {
    assert.equal(movedTo("dreams"), "/settings/dreaming");
    assert.equal(movedTo("evolution"), "/settings/wishlist");
    assert.equal(movedTo("wakeup"), "/settings/thoughts");
  });
  it("leaves live and unknown slugs alone", () => {
    assert.equal(movedTo("memories"), null);
    assert.equal(movedTo("kitchen"), null);
    assert.equal(movedTo("constructor"), null);
  });
});

describe("a retired panel URL", () => {
  it("redirects instead of rendering a 404", () => {
    let thrown: unknown = null;
    try {
      render(<SettingsPanelPage params={{ slug: "evolution" }} />);
    } catch (e) {
      thrown = e;
    }
    const digest = String((thrown as { digest?: string } | null)?.digest ?? "");
    assert.match(digest, /NEXT_REDIRECT.*\/settings\/wishlist/);
  });
});

describe("the settings not-found page", () => {
  it("says the page moved, in his voice, with the rooms and All settings", () => {
    render(
      <AppRouterContext.Provider value={{ push() {}, replace() {}, back() {}, forward() {}, refresh() {}, prefetch() {} } as never}>
        <PathnameContext.Provider value="/settings/room/kitchen">
          <SettingsNotFound />
        </PathnameContext.Provider>
      </AppRouterContext.Provider>,
    );
    assert.match(screen.getByRole("heading", { level: 1 }).textContent ?? "", /can't find that page/i);
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    for (const h of ["/settings/all", "/settings/room/mind", "/settings/room/memory", "/settings/room/attention", "/settings/room/work", "/settings/room/house"]) {
      assert.ok(hrefs.includes(h), h);
    }
    assert.equal(screen.queryByText(/404|could not be found/), null);
  });
});
