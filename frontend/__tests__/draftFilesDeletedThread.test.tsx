// A deleted thread takes its kept attachments with it (2026-09-27 review of
// L2 + H3).
//
// Attachments wait per thread in memory (lib/draftFiles.ts), and while any
// thread keeps some the app never reloads onto a new build
// (AppBuildReload: they would be lost). Only a send cleared them, so a
// photo left in a thread Ellis then deleted stayed in the map where nothing
// could reach it, and that tab never picked up another deploy. Worse, when
// the deleted thread was the one on screen, the composer's switch to a new
// chat wrote the photo back under the dead id.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { MobileChatDrawer, openChatHistory } from "../components/chat/ChatSidebar.tsx";
import { InputBar, type InputBarHandle } from "../components/chat/InputBar.tsx";
import { AppBuildReload } from "../components/layout/AppBuildReload.tsx";
import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider, useChat } from "../lib/ChatContext.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";
import { pendingDeletes } from "../lib/pendingDeletes.ts";
import { forgetDraftFiles, hasDraftFiles, readDraftFiles, resetDraftFiles, writeDraftFiles } from "../lib/draftFiles.ts";
import type { BuildReloadDeps } from "../lib/buildReload.ts";

const originalFetch = globalThis.fetch;
const originalMatchMedia = window.matchMedia;
const NOW = new Date().toISOString();
const photo = () => new File(["jpeg"], "cabin.jpg", { type: "image/jpeg" });
let deletes: string[] = [];
let refuseDelete = false;

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

function OpenCabin() {
  const { loadConversation } = useChat();
  return (
    <button type="button" onClick={() => void loadConversation("c-cabin")}>
      open cabin
    </button>
  );
}

beforeEach(() => {
  deletes = [];
  refuseDelete = false;
  // A phone: the drawer's rows swipe to Rename and Delete.
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
    if (init?.method === "DELETE") {
      deletes.push(path);
      return refuseDelete ? json({ detail: "no" }, 500) : json({ ok: true });
    }
    if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
    if (path.startsWith("/api/voice/status")) return json({ enabled: false, status: "disabled" });
    if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
    if (path.startsWith("/api/conversations/c-cabin")) return json({ id: "c-cabin", title: "Cabin weekend", source: "user", messages: [] });
    if (path.startsWith("/api/conversations")) {
      return json({
        conversations: [{ id: "c-cabin", title: "Cabin weekend", source: "user", channel: "text", notified_user: false, created_at: NOW, updated_at: NOW, message_count: 4 }],
        has_more: false,
      });
    }
    return json({});
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  pendingDeletes.flush();
  resetDraftFiles();
  globalThis.fetch = originalFetch;
  window.matchMedia = originalMatchMedia;
  window.sessionStorage.clear();
});

async function deleteCabinFromTheDrawer() {
  act(() => openChatHistory());
  await screen.findByRole("link", { name: /Cabin weekend/ });
  fireEvent.click(screen.getByRole("button", { name: "Delete Cabin weekend" }));
  await act(async () => {
    pendingDeletes.flush(); // the Undo window ends
    await new Promise((r) => setTimeout(r, 20));
  });
  await waitFor(() => assert.deepEqual(deletes, ["/api/conversations/c-cabin"]));
  await act(async () => void (await new Promise((r) => setTimeout(r, 20))));
}

describe("deleting a thread that keeps attachments", () => {
  it("forgets them, so the tab is free to pick up a new build", async () => {
    writeDraftFiles("c-cabin", [photo()]);
    const h = { reloads: 0 };
    const deps: BuildReloadDeps = {
      bakedId: "old-build",
      fetchServed: async () => "new-build",
      reload: () => void h.reloads++,
      firstCheckMs: 60 * 60_000,
      everyMs: 60 * 60_000,
    };
    render(
      <Shell>
        <AppBuildReload deps={deps} />
        <MobileChatDrawer />
      </Shell>,
    );
    await deleteCabinFromTheDrawer();
    assert.equal(readDraftFiles("c-cabin").length, 0);
    assert.equal(hasDraftFiles(), false);

    await act(async () => {
      document.dispatchEvent(new window.Event("visibilitychange"));
      await new Promise((r) => setTimeout(r, 5));
    });
    assert.equal(h.reloads, 1, "nothing is held for a thread that no longer exists");
  });

  it("the thread on screen: the composer's move to a new chat does not write them back", async () => {
    const ref = React.createRef<InputBarHandle>();
    render(
      <Shell>
        <OpenCabin />
        <InputBar ref={ref} onSend={() => {}} isLoading={false} />
        <MobileChatDrawer />
      </Shell>,
    );
    await act(async () => void fireEvent.click(screen.getByRole("button", { name: "open cabin" })));
    act(() => ref.current!.addFiles([photo()]));
    assert.equal(screen.queryByRole("button", { name: "Remove cabin.jpg" }) !== null, true);

    await deleteCabinFromTheDrawer();
    assert.equal(readDraftFiles("c-cabin").length, 0, "nothing kept under the deleted id");
    assert.equal(hasDraftFiles(), false);
  });

  it("a refused delete keeps them: the thread is back", async () => {
    refuseDelete = true;
    writeDraftFiles("c-cabin", [photo()]);
    render(
      <Shell>
        <MobileChatDrawer />
      </Shell>,
    );
    await deleteCabinFromTheDrawer();
    assert.equal(readDraftFiles("c-cabin").length, 1);
  });

  it("an id once forgotten never takes attachments again (ids are never reused)", () => {
    writeDraftFiles("c-gone", [photo()]);
    forgetDraftFiles("c-gone");
    writeDraftFiles("c-gone", [photo()]);
    assert.equal(readDraftFiles("c-gone").length, 0);
    assert.equal(hasDraftFiles(), false);
  });
});
