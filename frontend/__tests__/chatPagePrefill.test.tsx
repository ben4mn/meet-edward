// An idea's "Try it" twice (2026-09-27 review of L2). InputBar refills on a
// new {text, nonce}, but the chat page handed it the ?prompt= as a plain
// string: the same suggestion a second time, after Ellis cleared the box,
// changed nothing and the box stayed empty while /chat stayed mounted.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import ChatPage from "../app/(app)/chat/page.tsx";
import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider } from "../lib/ChatContext.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";

// The page is written for Next's automatic JSX runtime (no React import).
(globalThis as unknown as { React: typeof React }).React = React;
const originalFetch = globalThis.fetch;
function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };

function Page({ search }: { search: string }) {
  return (
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value="/chat">
        <SearchParamsContext.Provider value={new URLSearchParams(search) as never}>
          <ToastProvider>
            <AuthProvider>
              <ChatProvider>
                <ChatPage />
              </ChatProvider>
            </AuthProvider>
          </ToastProvider>
        </SearchParamsContext.Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>
  );
}

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
  window.sessionStorage.clear();
});

describe("the chat page's ?prompt=", () => {
  it("the same suggestion twice refills a box Ellis cleared", async () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const p = String(input).replace(/^https?:\/\/[^/]+/, "");
      if (p.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
      if (p.startsWith("/api/voice/status")) return json({ enabled: false, status: "disabled" });
      if (p.startsWith("/api/conversations")) return json({ conversations: [], has_more: false });
      return json({});
    }) as typeof fetch;
    const box = () => screen.getByRole("textbox", { name: "Message Edward" }) as HTMLTextAreaElement;
    const { rerender } = render(<Page search="prompt=Plan+the+cabin+trip" />);
    await act(async () => void (await new Promise((r) => setTimeout(r, 20))));
    assert.equal(box().value, "Plan the cabin trip");

    fireEvent.change(box(), { target: { value: "" } });
    rerender(<Page search="" />); // the page dropped ?prompt= from the URL
    await act(async () => void (await new Promise((r) => setTimeout(r, 20))));
    rerender(<Page search="prompt=Plan+the+cabin+trip" />); // the same suggestion again
    await act(async () => void (await new Promise((r) => setTimeout(r, 20))));
    assert.equal(box().value, "Plan the cabin trip", "the second Try it refills");
  });
});
