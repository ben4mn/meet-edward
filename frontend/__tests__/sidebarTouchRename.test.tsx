// Rename on the phone (L14, 2026-09-27 follow-ups). The desktop "⋯" menu
// offers Rename and Delete; on touch the row's swipe offered Delete only,
// so a conversation could not be renamed from the phone at all. The swipe
// now shows Rename beside Delete; Delete (and its Undo) is unchanged.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { MobileChatDrawer, openChatHistory } from "../components/chat/ChatSidebar.tsx";
import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider } from "../lib/ChatContext.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";
import { pendingDeletes } from "../lib/pendingDeletes.ts";

const present = (el: Element | null) => el !== null;
const originalFetch = globalThis.fetch;
const originalMatchMedia = window.matchMedia;
const NOW = new Date().toISOString();
let serverTitle = "Cabin weekend";
const conversations = () => [
  { id: "c-cabin", title: serverTitle, source: "user", channel: "text", notified_user: false, created_at: NOW, updated_at: NOW, message_count: 4 },
];
const patched: { path: string; body: string }[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value="/chat">
        <SearchParamsContext.Provider value={new URLSearchParams() as never}>
          <ToastProvider>
            <AuthProvider>
              <ChatProvider>{children}</ChatProvider>
            </AuthProvider>
          </ToastProvider>
        </SearchParamsContext.Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>
  );
}

beforeEach(() => {
  patched.length = 0;
  serverTitle = "Cabin weekend";
  // A phone: a coarse pointer.
  window.matchMedia = ((query: string) => ({
    matches: /pointer:\s*coarse/.test(query),
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {
      return false;
    },
  })) as unknown as typeof window.matchMedia;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (init?.method === "PATCH") {
      patched.push({ path, body: String(init.body) });
      serverTitle = JSON.parse(String(init.body)).title;
      return json({ id: "c-cabin", title: serverTitle });
    }
    if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
    if (path.startsWith("/api/voice/status")) return json({ enabled: false, status: "disabled" });
    if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
    if (path.startsWith("/api/conversations")) return json({ conversations: conversations(), has_more: false });
    return json({});
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  pendingDeletes.flush();
  globalThis.fetch = originalFetch;
  window.matchMedia = originalMatchMedia;
  window.sessionStorage.clear();
});

describe("the conversation drawer on a phone", () => {
  it("offers Rename beside Delete in the row's swipe actions, and renames", async () => {
    render(
      <Shell>
        <MobileChatDrawer />
      </Shell>,
    );
    act(() => openChatHistory());
    await screen.findByRole("link", { name: /Cabin weekend/ });
    assert.equal(present(screen.queryByRole("button", { name: "More for Cabin weekend" })), false, "no hover menu on touch");
    assert.equal(present(screen.queryByRole("button", { name: "Delete Cabin weekend" })), true, "swipe-to-delete is still there");

    fireEvent.click(screen.getByRole("button", { name: "Rename Cabin weekend" }));
    const box = (await screen.findByRole("textbox", { name: "Rename conversation" })) as HTMLInputElement;
    assert.equal(box.value, "Cabin weekend");
    fireEvent.change(box, { target: { value: "Cabin trip" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => assert.equal(patched.length, 1));
    assert.equal(patched[0].path, "/api/conversations/c-cabin");
    assert.deepEqual(JSON.parse(patched[0].body), { title: "Cabin trip" });
    await screen.findByRole("link", { name: /Cabin trip/ });
  });
});
