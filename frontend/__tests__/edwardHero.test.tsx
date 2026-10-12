// The Edward tab's hero (components/settings/EdwardHero.tsx), integration
// pass 2026-09-26:
//  - A beat already current when the tab opens (the morning wave, a wince,
//    the "acted" sonar) is not replayed on every /settings visit; a beat
//    that arrives while he is on screen plays (beat-replay-on-mount).
//  - Before the first answer about his mind (presence.status ""), the hero
//    says no status at all — not a guessed "Resting" from its own fetch.
//  - The line is not a live region: the "next wake in Nm" countdown would
//    be re-announced every 20 s (aria-live-reannounce-greeting-and-countdown).

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React, { useState } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { EdwardHero } from "../components/settings/EdwardHero.tsx";
import { edwardTabStore } from "../components/settings/useEdwardTabStatus.ts";
import { PresenceContext } from "../lib/presenceContext.ts";
import { presenceSignals } from "../lib/presenceModel.ts";
import type { EdwardPresence, PresenceBeat } from "../lib/presence.tsx";

const originalFetch = globalThis.fetch;
const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const MIND_NOW = {
  now: new Date().toISOString(),
  waking: null,
  last: null,
  recent: [],
  next_wake_at: new Date(Date.now() + 38 * 60_000).toISOString(),
  approvals_waiting: 0,
  unread_thoughts: 0,
  quota: { used_percent: 40, exhausted: false, resets_at: null },
  hands: { connected: true, down_for_s: null },
};

function stub(beat: PresenceBeat | null, over: Partial<EdwardPresence> = {}): EdwardPresence {
  return {
    signals: presenceSignals({ chat: null, online: true, mindNow: null, hour: 14 }),
    mood: "idle",
    status: "Resting",
    label: "Edward · Resting",
    beat,
    mindNow: null,
    online: true,
    available: true,
    refresh: () => {},
    ...over,
  };
}

let setBeat: (b: PresenceBeat | null) => void = () => {};
function Stub({ initial, over, children }: { initial: PresenceBeat | null; over?: Partial<EdwardPresence>; children: React.ReactNode }) {
  const [beat, set] = useState<PresenceBeat | null>(initial);
  setBeat = set;
  return <PresenceContext.Provider value={stub(beat, over)}>{children}</PresenceContext.Provider>;
}

function mount(initial: PresenceBeat | null, over?: Partial<EdwardPresence>) {
  return render(
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value="/settings">
        <Stub initial={initial} over={over}>
          <EdwardHero />
        </Stub>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>,
  );
}

let played: string[] = [];
let observer: MutationObserver | null = null;

const settle = async (ms = 40) => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, ms));
  });
};

beforeEach(() => {
  edwardTabStore.reset();
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (path.startsWith("/api/mind/now")) return json(MIND_NOW);
    if (path.startsWith("/api/documents")) return json({ documents: [] });
    return json({}, 404);
  }) as typeof fetch;
  played = [];
  observer = new window.MutationObserver((records) => {
    for (const r of records) {
      const v = (r.target as Element).getAttribute("data-beat");
      if (v) played.push(v);
    }
  });
  observer.observe(document.body, { attributes: true, subtree: true, attributeFilter: ["data-beat"] });
});
afterEach(() => {
  observer?.disconnect();
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("EdwardHero", () => {
  it("does not replay a beat that was already current when the tab opened; a new one plays", async () => {
    mount({ beat: "wave", key: 4, at: Date.now() - 60_000 });
    await settle();
    assert.deepEqual(played, [], "the morning wave is not replayed on a /settings visit");
    act(() => setBeat({ beat: "nod", key: 5, at: Date.now() }));
    await settle();
    assert.deepEqual(played, ["nod"]);
  });

  it("before the first answer about his mind, says no status at all (not a guessed 'Resting')", async () => {
    mount(null, { status: "", label: "Edward" });
    await settle();
    const hero = screen.getByRole("region", { name: "Edward now" });
    assert.equal(/Resting/.test(hero.textContent ?? ""), false, hero.textContent ?? "");
    // A next wake his own fetch knows may stay, but never behind a dangling separator.
    for (const p of Array.from(hero.querySelectorAll("p"))) {
      assert.equal(/^\s*·/.test(p.textContent ?? ""), false, `dangling separator: "${p.textContent}"`);
    }
  });

  it("the status line is not a live region (a countdown would be re-announced every 20 s)", async () => {
    mount(null);
    await settle();
    const hero = screen.getByRole("region", { name: "Edward now" });
    assert.equal(hero.querySelectorAll("[aria-live]").length, 0);
    assert.equal(/Resting/.test(hero.textContent ?? ""), true, "the known line still shows");
  });
});
