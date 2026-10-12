// Smoke tests for the chat chrome (2026-09-26): the composer, the voice
// view's transcript and settings sheet, and the conversation drawer with
// its optimistic delete and Undo. The rules live in pure modules with their
// own tests; these pin that the components wire them up.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { InputBar } from "../components/chat/InputBar.tsx";
import { VoiceSettingsSheet, VoiceTranscript } from "../components/chat/ContinuousVoiceView.tsx";
import { MobileChatDrawer, openChatHistory } from "../components/chat/ChatSidebar.tsx";
import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider } from "../lib/ChatContext.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";
import { draftKey } from "../lib/drafts.ts";
import { UNDO_WINDOW_MS, pendingDeletes } from "../lib/pendingDeletes.ts";

const present = (el: Element | null) => el !== null;

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
});

// ---- the composer -----------------------------------------------------------

describe("InputBar", () => {
  it("empty: the slot summons Edward; typed: it sends, and Enter sends on a desktop keyboard", () => {
    const sent: string[] = [];
    let talked = 0;
    render(<InputBar onSend={(m) => sent.push(m)} onEnterVoiceMode={() => talked++} isLoading={false} voiceEnabled />);
    fireEvent.click(screen.getByRole("button", { name: "Talk live with Edward" }));
    assert.equal(talked, 1);

    const box = screen.getByRole("textbox", { name: "Message Edward" }) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "hello there" } });
    assert.equal(present(screen.queryByRole("button", { name: "Talk live with Edward" })), false);
    fireEvent.keyDown(box, { key: "Enter", keyCode: 13 });
    assert.deepEqual(sent, ["hello there"]);
    assert.equal(box.value, "");
  });

  it("Shift+Enter is a newline, not a send", () => {
    const sent: string[] = [];
    render(<InputBar onSend={(m) => sent.push(m)} isLoading={false} />);
    const box = screen.getByRole("textbox", { name: "Message Edward" });
    fireEvent.change(box, { target: { value: "one" } });
    fireEvent.keyDown(box, { key: "Enter", keyCode: 13, shiftKey: true });
    assert.deepEqual(sent, []);
  });

  it("keeps a draft for the conversation and restores it; a send clears it", () => {
    const first = render(<InputBar onSend={() => {}} isLoading={false} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Message Edward" }), { target: { value: "half a thought" } });
    assert.equal(window.sessionStorage.getItem(draftKey(undefined)), "half a thought");
    first.unmount();

    render(<InputBar onSend={() => {}} isLoading={false} />);
    const box = screen.getByRole("textbox", { name: "Message Edward" }) as HTMLTextAreaElement;
    assert.equal(box.value, "half a thought");
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    assert.equal(window.sessionStorage.getItem(draftKey(undefined)), null);
  });

  it("while Edward streams with nothing typed, the slot is a quiet Stop", () => {
    let stopped = 0;
    render(<InputBar onSend={() => {}} onStop={() => stopped++} isLoading canStop />);
    fireEvent.click(screen.getByRole("button", { name: "Stop Edward" }));
    assert.equal(stopped, 1);
  });

  it("the shell's Esc (edward:stop-turn) stops a running turn", () => {
    let stopped = 0;
    render(<InputBar onSend={() => {}} onStop={() => stopped++} isLoading canStop />);
    act(() => void window.dispatchEvent(new window.Event("edward:stop-turn")));
    assert.equal(stopped, 1);
  });
});

// ---- voice ------------------------------------------------------------------

describe("VoiceTranscript", () => {
  it("shows the last exchanges, marks a cut-off answer, and the live caption", () => {
    render(
      <VoiceTranscript
        exchanges={[
          { you: "what's left today?", edward: "Biscuit's meds." },
          { you: "tell me a story", edward: "Once upon", cut: true },
        ]}
        liveCaption="and then"
      />,
    );
    assert.equal(present(screen.queryByText("what's left today?")), true);
    assert.equal(present(screen.queryByText("Biscuit's meds.")), true);
    assert.equal(present(screen.queryByText(/cut off/)), true);
    assert.equal(present(screen.queryByText("and then")), true);
  });
  it("renders nothing before anything is said", () => {
    const { container } = render(<VoiceTranscript exchanges={[]} />);
    assert.equal(container.innerHTML, "");
  });
});

describe("VoiceSettingsSheet", () => {
  it("switches mode; sensitivity is only offered hands-free", () => {
    const modes: string[] = [];
    const { rerender } = render(
      <VoiceSettingsSheet open onClose={() => {}} mode="auto" sensitivity="normal" onMode={(m) => modes.push(m)} onSensitivity={() => {}} />,
    );
    assert.equal(present(screen.queryByRole("radiogroup", { name: "Mic sensitivity" })), true);
    fireEvent.click(screen.getByRole("radio", { name: /hold to talk/i }));
    assert.deepEqual(modes, ["ptt"]);
    rerender(<VoiceSettingsSheet open onClose={() => {}} mode="ptt" sensitivity="normal" onMode={() => {}} onSensitivity={() => {}} />);
    assert.equal(present(screen.queryByRole("radiogroup", { name: "Mic sensitivity" })), false);
  });
});

// ---- the drawer -------------------------------------------------------------

const originalFetch = globalThis.fetch;
const deleted: string[] = [];
let failDeletes = false;
const NOW = new Date().toISOString();

const CONVERSATIONS = [
  { id: "c-cabin", title: "Cabin weekend", source: "user", channel: "text", notified_user: false, created_at: NOW, updated_at: NOW, message_count: 4 },
  { id: "c-vet", title: "Biscuit's vet", source: "user", channel: "text", notified_user: true, created_at: NOW, updated_at: NOW, message_count: 2 },
];

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

describe("MobileChatDrawer", () => {
  beforeEach(() => {
    deleted.length = 0;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      if (init?.method === "DELETE") {
        deleted.push(path);
        return failDeletes ? json({ detail: "boom" }, 500) : json({ status: "deleted" });
      }
      if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
      if (path.startsWith("/api/voice/status")) return json({ enabled: false, status: "disabled" });
      if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
      if (path.startsWith("/api/conversations")) return json({ conversations: CONVERSATIONS, has_more: false });
      return json({});
    }) as typeof fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("opens from openChatHistory(), rows are links, and a delete can be undone", async () => {
    render(
      <Shell>
        <MobileChatDrawer />
      </Shell>,
    );
    assert.equal(present(screen.queryByRole("dialog", { name: "Conversations" })), false);
    act(() => openChatHistory());
    const dialog = await screen.findByRole("dialog", { name: "Conversations" });
    assert.equal(present(dialog), true);

    const row = await screen.findByRole("link", { name: /Cabin weekend/ });
    assert.equal(row.getAttribute("href"), "/chat?c=c-cabin");
    assert.equal(present(screen.queryByText("Today")), true);

    fireEvent.click(screen.getByRole("button", { name: "More for Cabin weekend" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
    await waitFor(() => assert.equal(present(screen.queryByRole("link", { name: /Cabin weekend/ })), false));
    assert.deepEqual(deleted, [], "nothing is sent inside the undo window");

    fireEvent.click(await screen.findByRole("button", { name: /undo/i }));
    await screen.findByRole("link", { name: /Cabin weekend/ });
    assert.ok(UNDO_WINDOW_MS >= 6000);
    assert.deepEqual(deleted, []);
  });

  // 2026-09-26 review: a DELETE that failed after the undo window brought
  // nothing back and said nothing (the error was swallowed).
  it("a delete the server refuses comes back, and Edward says so", async () => {
    failDeletes = true;
    try {
      render(
        <Shell>
          <MobileChatDrawer />
        </Shell>,
      );
      act(() => openChatHistory());
      await screen.findByRole("link", { name: /Biscuit's vet/ });
      fireEvent.click(screen.getByRole("button", { name: "More for Biscuit's vet" }));
      fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
      await waitFor(() => assert.equal(present(screen.queryByRole("link", { name: /Biscuit's vet/ })), false));
      await act(async () => {
        pendingDeletes.flush();
        await new Promise((r) => setTimeout(r, 40));
      });
      assert.equal(deleted.length, 1);
      await screen.findByText(/couldn't delete that conversation/i);
      await screen.findByRole("link", { name: /Biscuit's vet/ });
    } finally {
      failDeletes = false;
    }
  });

  it("Esc closes it", async () => {
    render(
      <Shell>
        <MobileChatDrawer />
      </Shell>,
    );
    act(() => openChatHistory());
    await screen.findByRole("dialog", { name: "Conversations" });
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => assert.equal(present(screen.queryByRole("dialog", { name: "Conversations" })), false));
  });
});
