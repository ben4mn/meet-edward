// R/G red (2026-09-26 review): Edward's one-shot beats in the shell are
// true only once.
//
//  - A beat the presence provider already played (the morning wave, a
//    wince, the "acted" sonar) is never replayed when an avatar appears or
//    comes back to life: opening the presence sheet, closing it, the bar
//    unfolding after the composer, a hero scrolling away. useFreshBeat
//    plays only beats that arrive while the avatar is the live one.
//  - Coming back to the app earns one perk, not two: inside the provider
//    the provider owns the perk; an avatar perks on its own only where no
//    provider feeds it (/login, /screen, the hub).
//  - The desktop header keeps his name (and a still Edward) while a page's
//    hero is the live one — its left third is never blank.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React, { useState } from "react";
import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider } from "../lib/ChatContext.tsx";
import { EdwardPresenceProvider, LAST_WAVE_KEY, useFreshBeat, type EdwardPresence, type PresenceBeat } from "../lib/presence.tsx";
import { PresenceContext } from "../lib/presenceContext.ts";
import { presenceSignals } from "../lib/presenceModel.ts";
import { heroStore, useRegisterPresenceHero } from "../lib/presenceHero.tsx";
import { setComposing } from "../lib/useComposing.ts";
import { PresenceBar } from "../components/layout/PresenceBar.tsx";
import { PresenceSheet } from "../components/layout/PresenceSheet.tsx";
import { DesktopHeader } from "../components/layout/DesktopHeader.tsx";
import { ThemeProvider } from "../lib/theme.tsx";

// ---- plumbing -------------------------------------------------------------

const originalFetch = globalThis.fetch;
const originalMatchMedia = window.matchMedia;
const realNow = Date.now.bind(Date);
let offset = 0;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const QUIET = {
  now: "2026-09-26T18:00:00Z",
  waking: null,
  last: null,
  recent: [],
  next_wake_at: null,
  approvals_waiting: 0,
  unread_thoughts: 0,
  quota: { used_percent: 20, exhausted: false, resets_at: null },
  hands: { connected: true, down_for_s: null },
};

function installFetch() {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (path.startsWith("/api/mind/now")) return json(QUIET);
    if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
    if (path.startsWith("/api/voice/status")) return json({ enabled: false, status: "disabled" });
    if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
    if (path.startsWith("/api/conversations")) return json({ conversations: [], has_more: false });
    return json({});
  }) as typeof fetch;
}

function desktop(on: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: on && /min-width:\s*768px/.test(query),
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };

function Frame({ path = "/today", children }: { path?: string; children: React.ReactNode }) {
  return (
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value={path}>
        <SearchParamsContext.Provider value={new URLSearchParams()}>
          <AuthProvider>
            <ChatProvider>{children}</ChatProvider>
          </AuthProvider>
        </SearchParamsContext.Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>
  );
}

function stub(beat: PresenceBeat | null, over: Partial<EdwardPresence> = {}): EdwardPresence {
  const signals = presenceSignals({ chat: null, online: true, mindNow: null, hour: 14 });
  return {
    signals,
    mood: "idle",
    status: "Resting",
    label: "Edward · Resting",
    ...over,
    beat,
    mindNow: null,
    online: true,
    available: true,
    refresh: () => {},
    ...over,
  };
}

let setBeat: (b: PresenceBeat | null) => void = () => {};
function StubPresence({ initial, children }: { initial: PresenceBeat | null; children: React.ReactNode }) {
  const [beat, set] = useState<PresenceBeat | null>(initial);
  setBeat = set;
  return <PresenceContext.Provider value={stub(beat)}>{children}</PresenceContext.Provider>;
}

/** Every data-beat written anywhere, with where it happened. */
let played: Array<{ beat: string; where: string }> = [];
let observer: MutationObserver | null = null;
function watchBeats() {
  played = [];
  observer = new window.MutationObserver((records) => {
    for (const r of records) {
      const el = r.target as Element;
      const v = el.getAttribute("data-beat");
      if (r.attributeName === "data-beat" && v) {
        const where = el.closest('[role="dialog"]') ? "sheet" : el.closest("header") ? "header" : "other";
        played.push({ beat: v, where });
      }
    }
  });
  observer.observe(document.body, { attributes: true, subtree: true, attributeFilter: ["data-beat"] });
}

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function flush(ms = 40) {
  await act(async () => {
    await tick(ms);
  });
}

const STALE: PresenceBeat = { beat: "wave", key: 3, at: 0 };

beforeEach(() => {
  installFetch();
  offset = 0;
  Date.now = () => realNow() + offset;
  desktop(false);
  try {
    window.localStorage.setItem("edward_auth_hint", "1");
    window.localStorage.setItem(LAST_WAVE_KEY, new Date().toISOString());
  } catch {
    /* none */
  }
  watchBeats();
});

afterEach(async () => {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  observer?.disconnect();
  cleanup();
  act(() => heroStore.closeSheet());
  setComposing(false);
  await tick(300);
  globalThis.fetch = originalFetch;
  window.matchMedia = originalMatchMedia;
  Date.now = realNow;
});

// ---------------------------------------------------------------------------

describe("useFreshBeat", () => {
  it("plays only beats that arrive while the avatar is active", () => {
    const { result, rerender } = renderHook(
      ({ beat, active }: { beat: PresenceBeat | null; active: boolean }) => useFreshBeat(beat, active),
      { initialProps: { beat: STALE, active: true } },
    );
    assert.equal(result.current, null, "a beat already current at mount is spent");
    rerender({ beat: STALE, active: false });
    rerender({ beat: STALE, active: true });
    assert.equal(result.current, null, "coming back to life replays nothing");
    rerender({ beat: { beat: "nod", key: 4, at: 1 }, active: true });
    assert.equal(result.current, "nod", "a new beat while active plays");
    rerender({ beat: { beat: "wince", key: 5, at: 2 }, active: false });
    rerender({ beat: { beat: "wince", key: 5, at: 2 }, active: true });
    assert.equal(result.current, null, "a beat that came while inactive is spent, not queued");
  });
});

describe("the presence bar (phone)", () => {
  it("never replays the last beat: sheet open/close, fold/unfold; a new beat plays", async () => {
    render(
      <Frame>
        <StubPresence initial={STALE}>
          <PresenceBar />
        </StubPresence>
      </Frame>,
    );
    await flush();
    assert.deepEqual(played, [], "mount");
    act(() => heroStore.openSheet());
    await flush();
    act(() => heroStore.closeSheet());
    await flush();
    assert.deepEqual(played, [], "the sheet closing brings him back to life without a replay");
    act(() => setComposing(true));
    await flush();
    act(() => setComposing(false));
    await flush(320);
    assert.equal(document.querySelectorAll("header .is-live").length, 1, "unfolded and live again");
    assert.deepEqual(played, [], "unfolding after the composer replays nothing");
    act(() => setBeat({ beat: "nod", key: 4, at: 1 }));
    await flush();
    assert.deepEqual(played, [{ beat: "nod", where: "header" }]);
  });
});

describe("the presence sheet", () => {
  it("opens without replaying the last beat, and plays one that arrives while open", async () => {
    render(
      <Frame>
        <StubPresence initial={STALE}>
          <PresenceSheet />
        </StubPresence>
      </Frame>,
    );
    await flush();
    act(() => heroStore.openSheet());
    await flush();
    assert.ok(screen.getByRole("dialog"));
    act(() => heroStore.closeSheet());
    await flush(400);
    act(() => heroStore.openSheet());
    await flush();
    assert.deepEqual(played, [], "open, close, open: no wave replayed");
    act(() => setBeat({ beat: "acted", key: 4, at: 1 }));
    await flush();
    assert.deepEqual(played, [{ beat: "acted", where: "sheet" }]);
  });
});

describe("the desktop header", () => {
  function WithHero() {
    useRegisterPresenceHero(true);
    return null;
  }

  it("never replays the last beat when he comes back to life", async () => {
    desktop(true);
    render(
      <Frame>
        <StubPresence initial={STALE}>
          <ThemeProvider>
            <DesktopHeader />
          </ThemeProvider>
        </StubPresence>
      </Frame>,
    );
    await flush();
    assert.equal(document.querySelectorAll("header .is-live").length, 1);
    act(() => heroStore.openSheet());
    await flush();
    act(() => heroStore.closeSheet());
    await flush();
    assert.deepEqual(played, []);
    act(() => setBeat({ beat: "wince", key: 4, at: 1 }));
    await flush();
    assert.deepEqual(played, [{ beat: "wince", where: "header" }]);
  });

  it("keeps his name and a still Edward while a page's hero is the live one", async () => {
    desktop(true);
    render(
      <Frame>
        <StubPresence initial={null}>
          <ThemeProvider>
            <DesktopHeader />
            <WithHero />
          </ThemeProvider>
        </StubPresence>
      </Frame>,
    );
    await flush();
    const cluster = document.querySelector("[data-presence-desktop]")!;
    assert.notEqual(cluster.getAttribute("aria-hidden"), "true", "not hidden from anyone");
    assert.doesNotMatch(cluster.className, /(^|\s)opacity-0(\s|$)/, "the cluster itself stays visible");
    assert.match(cluster.textContent ?? "", /Edward/);
    assert.ok(cluster.querySelector(".pane-avatar"), "a still Edward beside his name");
    assert.equal(document.querySelectorAll("header .is-live").length, 0, "the hero is the one live Edward");
  });
});

describe("coming back to the app", () => {
  it("earns exactly one perk inside the provider", async () => {
    let visibility: DocumentVisibilityState = "visible";
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
    Object.defineProperty(document, "hidden", { configurable: true, get: () => visibility === "hidden" });
    render(
      <Frame>
        <EdwardPresenceProvider timing={{ pollMs: 60_000, retryMs: 50 }}>
          <PresenceBar />
        </EdwardPresenceProvider>
      </Frame>,
    );
    await flush(60);
    assert.equal(document.querySelectorAll("header .is-live").length, 1);
    played = [];
    await act(async () => {
      visibility = "hidden";
      document.dispatchEvent(new window.Event("visibilitychange"));
      await tick(5);
    });
    offset += 30_000;
    await act(async () => {
      visibility = "visible";
      document.dispatchEvent(new window.Event("visibilitychange"));
      await tick(1_700); // longer than the 1.2 s beat gap: a held second perk would show
    });
    assert.deepEqual(
      played.filter((p) => p.beat === "perk"),
      [{ beat: "perk", where: "header" }],
      "one perk, from the provider",
    );
  });
});

describe("the presence bar's live region (2026-09-26 review)", () => {
  const working = (tools: number): Partial<EdwardPresence> => ({
    signals: { ...presenceSignals({ chat: null, online: true, mindNow: null, hour: 14 }), turn: { phase: "working", runningTools: tools } },
    mood: "work",
    status: `Working · ${tools} tool${tools === 1 ? "" : "s"}`,
    label: `Edward · Working · ${tools} tool${tools === 1 ? "" : "s"}`,
  });
  let setOver: (o: Partial<EdwardPresence>) => void = () => {};
  function Live({ children }: { children: React.ReactNode }) {
    const [over, set] = useState<Partial<EdwardPresence>>({});
    setOver = set;
    return <PresenceContext.Provider value={stub(null, over)}>{children}</PresenceContext.Provider>;
  }
  const region = () => document.querySelector('header [aria-live="polite"]')?.textContent;

  it("never reads out running-tool counts", async () => {
    render(
      <Frame>
        <Live>
          <PresenceBar />
        </Live>
      </Frame>,
    );
    await flush();
    act(() => setOver(working(1)));
    await flush();
    assert.equal(region(), "Edward · Working");
    act(() => setOver(working(3)));
    await flush();
    assert.equal(region(), "Edward · Working");
  });

  it("on chat, a live turn is the chat's to announce: the bar holds its line", async () => {
    render(
      <Frame path="/chat">
        <Live>
          <PresenceBar />
        </Live>
      </Frame>,
    );
    await flush();
    assert.equal(region(), "Edward · Resting");
    act(() => setOver(working(2)));
    await flush();
    assert.equal(region(), "Edward · Resting");
  });
});
