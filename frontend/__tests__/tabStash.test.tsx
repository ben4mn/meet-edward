// What only memory held survives a page load in this tab (pre-deploy
// review, 2026-09-27).
//
// Attachments kept for a thread (lib/draftFiles.ts) and messages queued in
// any thread (the ChatProvider's queues) lived only in memory, so
// AppBuildReload held a deploy's reload while there were any. That hold
// never covered Next's own hard load: on a build-id mismatch Next 14.1's
// router answers any navigation that fetches the server with a full page
// load (fetch-server-response.js, doMpaNavigation). Ellis attaches a photo in
// thread A and taps thread B; a deploy lands; he taps A again and the photo
// is gone — the hold only kept the old bundle running a little longer.
//
// Now both are mirrored into this tab's stash (lib/tabStash.ts: IndexedDB
// in the browser, keyed by a per-tab id in sessionStorage, a day at most)
// and restored on the next load: attachments back with their thread (the
// composer on screen picks its own up), queued messages back paused, so
// they go only when Ellis says "Send now".

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import {
  STASH_MAX_AGE_MS,
  clearStash,
  loadStash,
  memoryStashBackend,
  saveStash,
  setStashBackend,
  tabId,
} from "../lib/tabStash.ts";
import {
  dropDraftFilesInMemory,
  forgetDraftFiles,
  readDraftFiles,
  resetDraftFiles,
  restoreDraftFiles,
  writeDraftFiles,
} from "../lib/draftFiles.ts";
import { mergeQueues, type ComposerQueues } from "../lib/composerQueue.ts";
import { SIGNED_OUT_EVENT } from "../lib/authSession.ts";
import { InputBar } from "../components/chat/InputBar.tsx";
import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider, useChat } from "../lib/ChatContext.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";

const originalFetch = globalThis.fetch;
const photo = (name = "cabin.jpg") => new File(["jpeg"], name, { type: "image/jpeg", lastModified: 1_790_000_000_000 });
const settle = () => act(async () => void (await new Promise((r) => setTimeout(r, 20))));

let backend: ReturnType<typeof memoryStashBackend>;

beforeEach(() => {
  backend = memoryStashBackend();
  setStashBackend(backend);
});

afterEach(async () => {
  cleanup();
  resetDraftFiles();
  await clearStash();
  setStashBackend(null);
  globalThis.fetch = originalFetch;
  window.sessionStorage.clear();
});

describe("the tab's stash", () => {
  it("keeps what is saved for this tab and gives it back", async () => {
    await saveStash("queues", { a: 1 });
    assert.deepEqual(await loadStash("queues"), { a: 1 });
    assert.equal(await loadStash("files"), null);
  });

  it("is this tab's: another tab's entry is not read", async () => {
    await backend.put("some-other-tab:queues", { at: Date.now(), value: { b: 2 } });
    assert.equal(await loadStash("queues"), null);
    assert.equal(tabId(), tabId(), "one id for the life of the tab");
    assert.equal(window.sessionStorage.getItem("edward:tab:v1"), tabId());
  });

  it("forgets anything older than a day, any tab's", async () => {
    const now = Date.now();
    await saveStash("queues", { old: true }, now - STASH_MAX_AGE_MS - 1);
    await backend.put("gone-tab:files", { at: now - STASH_MAX_AGE_MS - 1, value: [] });
    await backend.put("live-tab:files", { at: now - 60_000, value: [] });
    assert.equal(await loadStash("queues", now), null);
    assert.deepEqual([...backend.data.keys()].sort(), ["live-tab:files"]);
  });

  it("is cleared by a sign-out", async () => {
    await saveStash("queues", { a: 1 });
    window.dispatchEvent(new window.Event(SIGNED_OUT_EVENT));
    await settle();
    assert.equal(await loadStash("queues"), null);
  });
});

describe("attachments kept for a thread survive a page load", () => {
  it("come back with their thread", async () => {
    writeDraftFiles("c-a", [photo()]);
    await settle();
    dropDraftFilesInMemory(); // the page loads again
    assert.equal(readDraftFiles("c-a").length, 0);
    await restoreDraftFiles();
    const back = readDraftFiles("c-a");
    assert.equal(back.length, 1);
    assert.equal(back[0].name, "cabin.jpg");
  });

  it("a deleted thread's are not brought back", async () => {
    writeDraftFiles("c-a", [photo()]);
    writeDraftFiles("c-gone", [photo("gone.jpg")]);
    forgetDraftFiles("c-gone");
    await settle();
    dropDraftFilesInMemory();
    await restoreDraftFiles();
    assert.equal(readDraftFiles("c-a").length, 1);
    assert.equal(readDraftFiles("c-gone").length, 0);
  });

  it("join what was attached before they came back, once each", async () => {
    writeDraftFiles("c-a", [photo()]);
    await settle();
    dropDraftFilesInMemory();
    writeDraftFiles("c-a", [photo("new.jpg")]);
    await restoreDraftFiles();
    await restoreDraftFiles();
    assert.deepEqual(readDraftFiles("c-a").map((f) => f.name).sort(), ["cabin.jpg", "new.jpg"]);
  });

  it("the composer on screen picks its own up", async () => {
    writeDraftFiles(null, [photo()]); // a new chat's attachment
    await settle();
    dropDraftFilesInMemory();
    stubServer();
    render(
      <Shell>
        <InputBar onSend={() => {}} isLoading={false} />
      </Shell>,
    );
    assert.equal(screen.queryByRole("button", { name: "Remove cabin.jpg" }), null);
    await act(async () => void (await restoreDraftFiles()));
    await waitFor(() => assert.notEqual(screen.queryByRole("button", { name: "Remove cabin.jpg" }), null));
  });
});

describe("mergeQueues", () => {
  it("puts what was saved first, keeps what is new, never twice", () => {
    const saved: ComposerQueues<string> = { "c-a": [{ id: "q1", text: "one" }], "c-b": [{ id: "q2", text: "two" }] };
    const current: ComposerQueues<string> = { "c-a": [{ id: "q1", text: "one" }, { id: "q3", text: "three" }] };
    assert.deepEqual(mergeQueues(current, saved), {
      "c-a": [{ id: "q1", text: "one" }, { id: "q3", text: "three" }],
      "c-b": [{ id: "q2", text: "two" }],
    });
    assert.deepEqual(mergeQueues({}, { "c-x": [] }), {});
  });
});

describe("queued messages survive a page load, paused", () => {
  it("come back in their thread and wait for Ellis", async () => {
    stubServer();
    const first = render(
      <Shell>
        <QueueProbe />
      </Shell>,
    );
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "send" })); // a turn that is still running
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "queue" }));
    await settle();
    assert.equal(screen.getByTestId("queued").textContent, "1");
    assert.equal(sent.length, 1);
    first.unmount(); // the page loads again

    render(
      <Shell>
        <QueueProbe />
      </Shell>,
    );
    await waitFor(() => assert.equal(screen.getByTestId("queued").textContent, "1"));
    assert.equal(screen.getByTestId("paused").textContent, "paused", "they go when Ellis says so");
    await settle();
    assert.equal(sent.length, 1, "nothing was sent on its own");
  });
});

// ---- harness --------------------------------------------------------------

let sent: string[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function stubServer() {
  sent = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (path.startsWith("/api/chat") && init?.method === "POST") {
      sent.push(path);
      return new Promise<Response>(() => {}); // the turn runs on
    }
    if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
    if (path.startsWith("/api/voice/status")) return json({ enabled: false, status: "disabled" });
    if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
    if (path.startsWith("/api/conversations")) return json({ conversations: [], has_more: false });
    return json({});
  }) as typeof fetch;
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

function QueueProbe() {
  const { sendMessage, queueMessage, queuedMessages, queuePaused } = useChat();
  return (
    <>
      <button type="button" onClick={() => void sendMessage("book the cabin")}>
        send
      </button>
      <button type="button" onClick={() => queueMessage("and the cabin photos", [photo()])}>
        queue
      </button>
      <span data-testid="queued">{queuedMessages.length}</span>
      <span data-testid="paused">{queuePaused ? "paused" : "live"}</span>
    </>
  );
}
