// The antenna tip is his trust light (design system §4): amber while his
// hands in Ellis's Chrome are down, whatever he is doing — never a calm
// emerald while the browser link is dead (review 2026-09-26:
// hands-down-invisible, screen-hands-down-trust-light). /screen marks it
// through its own anchor; every other Edward reads the presence provider.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { readFileSync } from "node:fs";
import { render } from "@testing-library/react";

import { EdwardAvatar } from "../components/EdwardAvatar.tsx";
import { PaneSvg } from "../components/pane/PaneSvg.tsx";
import { PresenceContext } from "../lib/presenceContext.ts";
import type { EdwardPresence } from "../lib/presence.tsx";

const globalsCss = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");

function presenceWith(handsDown: boolean): EdwardPresence {
  return {
    signals: {
      online: true,
      approvalsWaiting: 0,
      unreadThoughts: 0,
      quotaExhausted: false,
      handsDown,
      hour: 12,
    },
    mood: "work",
    status: "Working",
    label: "Edward · Working",
    beat: null,
    mindNow: null,
    online: true,
    available: true,
    refresh: () => {},
  };
}

const pane = (c: HTMLElement) => c.querySelector(".pane-avatar") as HTMLElement;

describe("the trust light when his hands are down", () => {
  it("PaneSvg marks data-hands='down' only when told", () => {
    assert.equal(pane(render(<PaneSvg size={40} handsDown />).container).getAttribute("data-hands"), "down");
    assert.equal(pane(render(<PaneSvg size={40} />).container).getAttribute("data-hands"), null);
  });

  it("an Edward under the presence provider follows its hands signal", () => {
    const down = render(
      <PresenceContext.Provider value={presenceWith(true)}>
        <EdwardAvatar size={40} mood="work" />
      </PresenceContext.Provider>,
    );
    assert.equal(pane(down.container).getAttribute("data-hands"), "down");
    const up = render(
      <PresenceContext.Provider value={presenceWith(false)}>
        <EdwardAvatar size={40} mood="work" />
      </PresenceContext.Provider>,
    );
    assert.equal(pane(up.container).getAttribute("data-hands"), null);
  });

  it("an explicit prop wins, and outside the provider he makes no claim", () => {
    assert.equal(pane(render(<EdwardAvatar size={40} handsDown />).container).getAttribute("data-hands"), "down");
    assert.equal(pane(render(<EdwardAvatar size={40} />).container).getAttribute("data-hands"), null);
  });

  it("globals.css turns the tip amber for it, and leaves offline grey", () => {
    assert.match(
      globalsCss,
      /\.pane-avatar\[data-hands="down"\]:not\(\[data-mood="offline"\]\)\s*\{\s*--pane-tip:\s*var\(--pane-amber\);/,
    );
  });
});
