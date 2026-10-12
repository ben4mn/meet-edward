// R/G red (2026-09-26 review, "hidden desktop sidebar re-renders every
// token"): the shell mounts the chrome for the size it is drawn at. On the
// phone the desktop header and the chat sidebar (30–50 rows, each calling
// useChat) were mounted under display:none and reconciled on every streamed
// token — about three quarters of React's work per token. Now the phone gets
// the presence bar only; md+ gets the header and, on chat, the sidebar.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, render } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider } from "../lib/ChatContext.tsx";
import { EdwardPresenceProvider } from "../lib/presence.tsx";
import { ClientLayout } from "../components/layout/ClientLayout.tsx";

const originalFetch = globalThis.fetch;
const originalMatchMedia = window.matchMedia;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
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

async function mount(path: string) {
  const [pathname, query = ""] = path.split("?");
  render(
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value={pathname}>
        <SearchParamsContext.Provider value={new URLSearchParams(query)}>
          <AuthProvider>
            <ChatProvider>
              <EdwardPresenceProvider timing={{ pollMs: 60_000, retryMs: 50 }}>
                <ClientLayout>
                  <p>page</p>
                </ClientLayout>
              </EdwardPresenceProvider>
            </ChatProvider>
          </AuthProvider>
        </SearchParamsContext.Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>,
  );
  await act(async () => {
    await new Promise((r) => setTimeout(r, 40));
  });
}

beforeEach(() => {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
    if (path.startsWith("/api/voice/status")) return json({ enabled: false, status: "disabled" });
    if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
    if (path.startsWith("/api/conversations")) return json({ conversations: [], has_more: false });
    if (path.startsWith("/api/push/status")) return json({ configured: false });
    return json({});
  }) as typeof fetch;
  try {
    window.localStorage.setItem("edward_auth_hint", "1");
  } catch {
    /* none */
  }
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
  window.matchMedia = originalMatchMedia;
});

// Booleans only: an AssertionError that has to print a jsdom element runs
// the process out of memory.
const has = (sel: string) => document.querySelector(sel) !== null;

describe("ClientLayout", () => {
  it("on the phone mounts the presence bar, not the desktop header or sidebar", async () => {
    desktop(false);
    await mount("/chat");
    assert.equal(has("[data-presence-cluster]"), true, "the presence bar");
    assert.equal(has("[data-presence-desktop]"), false, "no hidden desktop header");
    assert.equal(has('aside[aria-label="Conversations"]'), false, "no hidden desktop sidebar");
  });

  it("on md+ mounts the header and, on chat, the sidebar — not the phone bar", async () => {
    desktop(true);
    await mount("/chat");
    assert.equal(has("[data-presence-desktop]"), true, "the desktop header");
    assert.equal(has('aside[aria-label="Conversations"]'), true, "the chat sidebar");
    assert.equal(has("[data-presence-cluster]"), false, "no hidden phone bar");
  });
});
