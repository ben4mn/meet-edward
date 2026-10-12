// R/G red: the app shell (2026-09-26, design system §1, §5).
//
//  - The dock is a native tab bar: tapping the tab you are on scrolls Today
//    to the top, pops an Edward sub-page to its root and starts a new chat
//    on chat; the Edward tab carries a pip when he wants Ellis; it tells the
//    toasts how tall it is (--dock-h) and folds away while Ellis types.
//  - The phone presence bar is the live Edward and his honest line; tapping
//    him opens the presence sheet; its one contextual slot is history + new
//    chat on chat and search elsewhere; a page's hero Edward takes over.
//  - The presence sheet says what he is doing and did, in plain words, and
//    "Wake him now" posts a manual wake and shows it at once.
//  - The keyboard layer: Ctrl/⌘K palette, "/" composer, Esc stops a turn.
//  - AuthGuard waits with a still Edward and a pulse, never a spinner.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider } from "../lib/ChatContext.tsx";
import { EdwardPresenceProvider } from "../lib/presence.tsx";
import { heroStore, useRegisterPresenceHero } from "../lib/presenceHero.tsx";
import { setComposing } from "../lib/useComposing.ts";
import { ThumbDock } from "../components/chat/ThumbDock.tsx";
import { PresenceBar } from "../components/layout/PresenceBar.tsx";
import { PresenceSheet } from "../components/layout/PresenceSheet.tsx";
import { KeyboardLayer } from "../components/layout/KeyboardLayer.tsx";
import { paletteStore } from "../components/layout/shellStores.ts";
import { AuthGuard } from "../components/auth/AuthGuard.tsx";
import { DesktopHeader } from "../components/layout/DesktopHeader.tsx";
import { signOutStore } from "../components/layout/shellStores.ts";
import { ThemeProvider } from "../lib/theme.tsx";

// ---- a scripted backend -----------------------------------------------------

const originalFetch = globalThis.fetch;
let mindNow: Record<string, unknown>;
const posts: Array<{ path: string; body: unknown }> = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const QUIET = {
  now: "2026-09-26T18:00:00Z",
  waking: null,
  last: null,
  recent: [
    { at: "2026-09-26T17:18:00Z", reason: "messages", outcome: "acted", line: "Replied to Parker about the flight.", thought_id: "t-parker" },
    { at: "2026-09-26T16:20:00Z", reason: "evening", outcome: "failed", line: "ValueError: HTTP 503", thought_id: null },
  ],
  next_wake_at: "2026-09-27T12:00:00Z",
  approvals_waiting: 2,
  unread_thoughts: 3,
  quota: { used_percent: 41, exhausted: false, resets_at: "2026-09-27T02:30:00Z" },
  hands: { connected: false, down_for_s: 95 },
};

function installFetch() {
  posts.length = 0;
  mindNow = { ...QUIET };
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (init?.method === "POST") {
      posts.push({ path, body: init.body ? JSON.parse(String(init.body)) : null });
      if (path.startsWith("/api/mind/wake")) return json({ accepted: true, reason: "manual", joined: false }, 202);
      return json({ ok: true });
    }
    if (path.startsWith("/api/mind/now")) return json(mindNow);
    if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
    if (path.startsWith("/api/voice/status")) return json({ enabled: false, status: "disabled" });
    if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
    if (path.startsWith("/api/conversations")) return json({ conversations: [], has_more: false });
    return json({});
  }) as typeof fetch;
}

const pushes: string[] = [];
const router = {
  push: (u: string) => void pushes.push(u),
  replace: () => {},
  back: () => {},
  forward: () => {},
  refresh: () => {},
  prefetch: () => {},
};

function Shell({ path, children }: { path: string; children: React.ReactNode }) {
  const [pathname, query = ""] = path.split("?");
  return (
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value={pathname}>
        <SearchParamsContext.Provider value={new URLSearchParams(query)}>
          <AuthProvider>
            <ChatProvider>
              <EdwardPresenceProvider timing={{ pollMs: 60_000, retryMs: 20 }}>{children}</EdwardPresenceProvider>
            </ChatProvider>
          </AuthProvider>
        </SearchParamsContext.Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>
  );
}

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function flush(ms = 40) {
  await act(async () => {
    await tick(ms);
  });
}

async function mount(path: string, ui: React.ReactNode) {
  try {
    window.localStorage.setItem("edward_auth_hint", "1");
  } catch {
    /* none */
  }
  const r = render(<Shell path={path}>{ui}</Shell>);
  await flush();
  return r;
}

const present = (el: Element | null) => el !== null;

beforeEach(() => {
  installFetch();
  pushes.length = 0;
});
afterEach(async () => {
  cleanup();
  act(() => {
    heroStore.closeSheet();
    paletteStore.close();
    signOutStore.close();
  });
  setComposing(false);
  await tick(300); // the composing blur debounce
  globalThis.fetch = originalFetch;
});

// ---------------------------------------------------------------------------

describe("ThumbDock", () => {
  it("marks the tab you are on and scrolls Today to the top when it is tapped again", async () => {
    await mount("/today", <ThumbDock />);
    const today = screen.getByRole("link", { name: "Today" });
    assert.equal(today.getAttribute("aria-current"), "page");
    let scrolled = 0;
    const on = () => scrolled++;
    window.addEventListener("edward:scroll-top", on);
    fireEvent.click(today);
    window.removeEventListener("edward:scroll-top", on);
    assert.equal(scrolled, 1);
    assert.deepEqual(pushes, [], "no navigation for a scroll-to-top");
  });

  it("on chat the Chat tab starts a new chat", async () => {
    await mount("/chat", <ThumbDock />);
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    assert.deepEqual(pushes, ["/chat"]);
  });

  it("the Edward tab pops a sub-page back to its root", async () => {
    await mount("/settings/room/mind", <ThumbDock />);
    const edward = document.querySelector('a[data-tab="/settings"]') as HTMLAnchorElement;
    fireEvent.click(edward);
    assert.deepEqual(pushes, ["/settings"]);
  });

  it("the Edward tab shows a pip while he wants Ellis", async () => {
    await mount("/today", <ThumbDock />);
    const edward = document.querySelector('a[data-tab="/settings"]') as HTMLAnchorElement;
    assert.equal(edward.getAttribute("aria-label"), "Edward, 2 waiting for your approval, 3 new thoughts");
    assert.equal(present(edward.querySelector("[data-pip]")), true);
    assert.equal(present(edward.querySelector(".is-live")), false, "the dock's Edward never breathes");
  });

  it("tells the toasts its height and folds away while Ellis types on chat", async () => {
    await mount("/chat", <ThumbDock />);
    assert.equal(document.documentElement.style.getPropertyValue("--dock-h"), "56px");
    act(() => setComposing(true));
    await flush();
    assert.equal(present(screen.queryByRole("navigation", { name: "Primary" })), false);
    assert.equal(document.documentElement.style.getPropertyValue("--dock-h"), "0px");
  });
});

// ---------------------------------------------------------------------------

function WithHero() {
  useRegisterPresenceHero(true);
  return null;
}

describe("PresenceBar", () => {
  it("is the live Edward with his honest line; tapping him opens the presence sheet", async () => {
    await mount("/today", <PresenceBar />);
    const who = screen.getByRole("button", { name: /Edward/ });
    assert.match(who.textContent ?? "", /Edward/);
    assert.match(who.textContent ?? "", /Waiting on your approval/);
    assert.equal(document.querySelectorAll("header .is-live").length, 1, "one live Edward");
    fireEvent.click(who);
    assert.equal(heroStore.sheetOpen(), true);
  });

  it("carries history and a new chat on chat, search elsewhere", async () => {
    await mount("/chat", <PresenceBar />);
    assert.equal(present(screen.queryByRole("button", { name: "Chat history" })), true);
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    assert.deepEqual(pushes, ["/chat"]);
    assert.equal(present(screen.queryByRole("button", { name: "Search" })), false);
    cleanup();
    await mount("/today", <PresenceBar />);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    assert.equal(paletteStore.get(), true);
  });

  it("hands over to a page's hero Edward", async () => {
    await mount(
      "/today",
      <>
        <PresenceBar />
        <WithHero />
      </>,
    );
    const cluster = document.querySelector("[data-presence-cluster]")!;
    assert.equal(cluster.getAttribute("aria-hidden"), "true");
    assert.equal(document.querySelectorAll("header .is-live").length, 0, "the hero is the live one");
    // Screen readers still hear his status.
    assert.equal(present(document.querySelector('header [aria-live="polite"]')), true);
  });

  it("sits at the scroll edge with no hairline while a hero is on screen", async () => {
    await mount("/today", <PresenceBar />);
    const bar = () => document.querySelector("header")!;
    assert.equal(bar().hasAttribute("data-edge"), false);
    assert.match(bar().className, /border-border/);
    cleanup();
    await mount(
      "/today",
      <>
        <PresenceBar />
        <WithHero />
      </>,
    );
    assert.equal(bar().hasAttribute("data-edge"), true);
    assert.match(bar().className, /border-transparent/);
    assert.doesNotMatch(bar().className, /backdrop-blur/);
  });
});

// ---------------------------------------------------------------------------

describe("PresenceSheet", () => {
  it("says what he did in plain words and links his thinking", async () => {
    await mount("/today", <PresenceSheet />);
    act(() => heroStore.openSheet());
    await flush();
    const dialog = screen.getByRole("dialog");
    const text = dialog.textContent ?? "";
    assert.match(text, /Replied to Parker about the flight\./);
    assert.match(text, /Acted/);
    assert.match(text, /Didn't finish/);
    assert.doesNotMatch(text, /ValueError|HTTP 503/, "no exceptions on Ellis's screen");
    assert.match(text, /2 messages are waiting for your approval/);
    assert.match(text, /41% of the week used/);
    assert.match(text, /Link to your Chrome down for 2m/);
    const thinking = screen.getByRole("link", { name: /Read my thinking/ });
    assert.equal(thinking.getAttribute("href"), "/chat?c=t-parker");
    assert.equal(dialog.querySelectorAll(".is-live").length, 1, "the sheet's Edward is the live one");
  });

  it("offline, it says it can't reach the Mac — no 'nothing running', no next wake, no Wake", async () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      if (path.startsWith("/api/mind/now")) return new Response("down", { status: 503 });
      if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
      if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
      if (path.startsWith("/api/conversations")) return json({ conversations: [], has_more: false });
      return json({});
    }) as typeof fetch;
    await mount("/today", <PresenceSheet />);
    await flush(120); // the quick retry fails too: offline
    act(() => heroStore.openSheet());
    await flush(80);
    const dialog = screen.getByRole("dialog");
    const text = dialog.textContent ?? "";
    assert.match(text, /I can't reach the Mac/);
    assert.doesNotMatch(text, /Nothing running right now/);
    assert.doesNotMatch(text, /Next wake/);
    assert.doesNotMatch(text, /Checking in/);
    const wake = screen.getByRole("button", { name: /Can't reach him/ });
    assert.equal((wake as HTMLButtonElement).disabled, true);
  });

  it("'Read my thinking' is a 44px target without growing the row", async () => {
    await mount("/today", <PresenceSheet />);
    act(() => heroStore.openSheet());
    await flush();
    const link = screen.getByRole("link", { name: /Read my thinking/ });
    assert.match(link.className, /(^|\s)relative(\s|$)/);
    assert.match(link.className, /after:-inset-y-1/);
  });

  it("Wake him now posts a manual wake and shows it at once", async () => {
    await mount("/today", <PresenceSheet />);
    act(() => heroStore.openSheet());
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Wake him now" }));
    assert.equal(present(screen.queryByRole("button", { name: /Waking/ })), true);
    await flush();
    assert.deepEqual(posts.find((p) => p.path.startsWith("/api/mind/wake"))?.body, { reason: "manual" });
  });
});

// ---------------------------------------------------------------------------

describe("KeyboardLayer", () => {
  it("⌘K opens the palette (even from a field); / asks for the composer; Esc asks to stop", async () => {
    await mount(
      "/chat",
      <>
        <KeyboardLayer />
        <textarea aria-label="composer" />
      </>,
    );
    const events: string[] = [];
    const on = (e: Event) => events.push(e.type);
    window.addEventListener("edward:focus-composer", on);
    window.addEventListener("edward:stop-turn", on);

    fireEvent.keyDown(document.body, { key: "/" });
    assert.deepEqual(events, ["edward:focus-composer"]);

    const ta = screen.getByLabelText("composer");
    ta.focus();
    fireEvent.keyDown(ta, { key: "/" });
    assert.deepEqual(events, ["edward:focus-composer"], "a / typed in a field is just a /");

    fireEvent.keyDown(ta, { key: "k", metaKey: true }); // jsdom reports MacIntel
    assert.equal(paletteStore.get(), true);
    act(() => paletteStore.close());

    fireEvent.keyDown(document.body, { key: "Escape" });
    assert.deepEqual(events, ["edward:focus-composer", "edward:stop-turn"]);

    window.removeEventListener("edward:focus-composer", on);
    window.removeEventListener("edward:stop-turn", on);
  });

  it("with a sheet open, / never moves focus to the composer behind it", async () => {
    await mount(
      "/chat",
      <>
        <KeyboardLayer />
        <PresenceSheet />
        <textarea aria-label="composer" data-composer="" />
      </>,
    );
    act(() => heroStore.openSheet());
    await flush();
    const events: string[] = [];
    const on = (e: Event) => events.push(e.type);
    window.addEventListener("edward:focus-composer", on);
    fireEvent.keyDown(document.body, { key: "/" });
    window.removeEventListener("edward:focus-composer", on);
    assert.deepEqual(events, [], "nothing asked the page for the composer");
    assert.equal(document.activeElement?.getAttribute("aria-label") === "composer", false);
  });

  it("⌘⇧O starts a new chat from inside the composer", async () => {
    await mount(
      "/chat",
      <>
        <KeyboardLayer />
        <textarea aria-label="composer" />
      </>,
    );
    const ta = screen.getByLabelText("composer");
    ta.focus();
    fireEvent.keyDown(ta, { key: "O", metaKey: true, shiftKey: true });
    assert.deepEqual(pushes, ["/chat"]);
  });

  it("g then t goes to Today", async () => {
    await mount("/chat", <KeyboardLayer />);
    fireEvent.keyDown(document.body, { key: "g" });
    fireEvent.keyDown(document.body, { key: "t" });
    assert.deepEqual(pushes, ["/today"]);
  });
});

// ---------------------------------------------------------------------------

describe("AuthGuard", () => {
  it("waits with a still Edward and a pulse, never a spinner", async () => {
    try {
      window.localStorage.removeItem("edward_auth_hint");
    } catch {
      /* none */
    }
    globalThis.fetch = (() => new Promise<Response>(() => {})) as typeof fetch; // the server never answers
    render(
      <AppRouterContext.Provider value={router as never}>
        <AuthProvider>
          <AuthGuard>
            <p>app</p>
          </AuthGuard>
        </AuthProvider>
      </AppRouterContext.Provider>,
    );
    assert.equal(present(screen.queryByRole("status")), true);
    assert.equal(present(document.querySelector(".animate-spin")), false);
    assert.equal(present(document.querySelector(".pane-avatar")), true);
    assert.equal(present(document.querySelector(".is-live")), false);
  });
});

// ---------------------------------------------------------------------------

describe("DesktopHeader", () => {
  const header = (path: string) =>
    mount(
      path,
      <ThemeProvider>
        <DesktopHeader />
      </ThemeProvider>,
    );

  it("segmented tabs: the page you are on is current; Chat on chat starts a new chat", async () => {
    await header("/chat?c=abc");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    const chat = Array.from(nav.querySelectorAll("a")).find((a) => a.getAttribute("href") === "/chat")!;
    assert.equal(chat.getAttribute("aria-current"), "page");
    fireEvent.click(chat);
    assert.deepEqual(pushes, ["/chat"]);
  });

  it("Search opens the palette; only one Sun on the page (inside the menu)", async () => {
    await header("/today");
    fireEvent.click(screen.getByRole("button", { name: /Search/ }));
    assert.equal(paletteStore.get(), true);
    assert.equal(document.querySelectorAll("header svg.lucide-sun").length, 0, "no theme toggle in the bar itself");
  });

  it("the avatar menu: arrow keys, Esc returns focus, sign out asks first", async () => {
    await header("/today");
    const button = screen.getByRole("button", { name: /appearance, screen, sign out/ });
    fireEvent.click(button);
    const menu = screen.getByRole("menu");
    const items = Array.from(menu.querySelectorAll('[role^="menuitem"]')) as HTMLElement[];
    assert.equal(items.length, 5, "three appearance choices, the screen, sign out");
    assert.equal(document.activeElement?.getAttribute("aria-checked"), "true", "focus starts on the current theme");
    fireEvent.keyDown(menu, { key: "End" });
    assert.equal(document.activeElement === items[items.length - 1], true);
    fireEvent.keyDown(menu, { key: "Escape" });
    await flush(500); // the exit animation
    assert.equal(present(screen.queryByRole("menu")), false, "the menu closed");
    assert.equal(document.activeElement === button, true);

    fireEvent.click(button);
    fireEvent.click(screen.getByRole("menuitem", { name: /Sign out/ }));
    assert.equal(signOutStore.get(), true);
  });

  it("announces his line politely on md+, where the phone bar's region is display:none (review)", async () => {
    await header("/today");
    await flush(100);
    const live = document.querySelector('header [aria-live="polite"]');
    assert.equal(present(live), true, "a polite live region in the desktop header");
    assert.equal(live!.textContent, "Edward · Waiting on your approval · 2 · Chrome link down");
  });

  it("every avatar-menu item shows a keyboard focus ring (review: weak-focus-indicators)", async () => {
    await header("/today");
    fireEvent.click(screen.getByRole("button", { name: /appearance, screen, sign out/ }));
    const items = Array.from(screen.getByRole("menu").querySelectorAll('[role^="menuitem"]')) as HTMLElement[];
    assert.equal(items.length > 0, true);
    for (const item of items) {
      assert.equal(/focus-visible:ring-2/.test(item.className), true, `no ring on ${item.textContent}`);
    }
  });
});
