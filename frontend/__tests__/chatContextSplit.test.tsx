// ChatContext, integration pass 2026-09-26:
//  - The conversation list reads its own context (useChatList): per-turn
//    state (messages, the turn, voice phase) never re-renders the sidebar
//    rows — on the phone every streamed token used to re-render 30–50 of
//    them (review: hidden-desktop-sidebar-rerenders-every-token).
//  - Leaving /chat ends live voice (review: voice-presence-listening-after-
//    unmount): presence stops reading "Listening" with no mic open.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React, { useState } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider, useChat, useChatList } from "../lib/ChatContext.tsx";

const originalFetch = globalThis.fetch;
function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };

beforeEach(() => {
  try {
    window.localStorage.setItem("edward_auth_hint", "1");
  } catch {
    /* none */
  }
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
    if (path.startsWith("/api/voice/status")) return json({ enabled: true, status: "connected" });
    if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
    if (path.startsWith("/api/conversations")) return json({ conversations: [], has_more: false });
    return json({});
  }) as typeof fetch;
});
afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));
const flush = (ms = 40) => act(async () => void (await tick(ms)));

let setPath: (p: string) => void = () => {};
function Frame({ children }: { children: React.ReactNode }) {
  const [path, set] = useState("/chat");
  setPath = set;
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

let chat: ReturnType<typeof useChat> | null = null;
function Turnish() {
  chat = useChat();
  return null;
}
let listRenders = 0;
let list: ReturnType<typeof useChatList> | null = null;
function ListReader() {
  list = useChatList();
  listRenders += 1;
  return null;
}

describe("the conversation list's own context", () => {
  it("per-turn state does not re-render a list reader; list state does", async () => {
    render(
      <Frame>
        <Turnish />
        <ListReader />
      </Frame>,
    );
    await flush();
    const before = listRenders;
    act(() => chat!.setVoicePhase("listening"));
    act(() => chat!.setVoicePhase("thinking"));
    act(() => chat!.setVoicePhase(null));
    assert.equal(listRenders, before, "the list reader stayed still through per-turn changes");
    act(() => list!.toggleSidebar());
    assert.equal(listRenders > before, true, "a list change re-renders it");
    assert.equal(list!.sidebarCollapsed, true);
  });
});

describe("leaving /chat ends live voice", () => {
  it("voice on in chat, then the route moves to Today: voice mode is off", async () => {
    render(
      <Frame>
        <Turnish />
      </Frame>,
    );
    await flush(60);
    await act(async () => chat!.refreshVoiceStatus());
    act(() => chat!.enterVoiceMode());
    act(() => chat!.setVoicePhase("listening"));
    assert.equal(chat!.isVoiceMode, true);
    act(() => setPath("/today"));
    await flush();
    assert.equal(chat!.isVoiceMode, false);
    assert.equal(chat!.voicePhase, null);
  });

  it("arriving at chat keeps a voice mode started on the way (the palette's Talk live)", async () => {
    render(
      <Frame>
        <Turnish />
      </Frame>,
    );
    await flush(60);
    await act(async () => chat!.refreshVoiceStatus());
    act(() => setPath("/today"));
    await flush();
    act(() => chat!.enterVoiceMode());
    act(() => setPath("/chat"));
    await flush();
    assert.equal(chat!.isVoiceMode, true);
  });
});

// The chat view always carries the turn's one screen-reader voice, whichever
// face it shows (the empty state, the transcript, live voice).
import { ChatWindow } from "../components/chat/ChatWindow.tsx";

describe("ChatWindow mounts the turn announcer", () => {
  it("on the empty chat as well as the transcript", async () => {
    const { container, rerender } = render(
      <Frame>
        <ChatWindow messages={[]} isLoading={false} />
      </Frame>,
    );
    await flush();
    assert.equal(container.querySelectorAll('[aria-live="polite"][aria-atomic="true"]').length, 1);
    rerender(
      <Frame>
        <ChatWindow messages={[{ id: "u1", role: "user", content: "hi" }]} isLoading={false} />
      </Frame>,
    );
    await flush();
    assert.equal(container.querySelectorAll('[aria-live="polite"][aria-atomic="true"]').length, 1);
  });
});
