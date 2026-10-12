// The composer keeps what Ellis put in it with its thread (L2, 2026-09-27
// follow-ups).
//
// - Typed drafts already stayed per conversation (lib/drafts.ts), but
//   switching threads called setFiles([]): an attached photo vanished the
//   moment Ellis looked at another thread. Attachments now live in an
//   in-memory map per conversation (lib/draftFiles.ts), like drafts.
// - An idea's "Try it" fills the box through `prefill`. It only reacted
//   when the text changed, so the same "Try it" twice (after Ellis cleared
//   the box) did nothing. A prefill is now {text, nonce}: a new nonce
//   refills even with the same text.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { InputBar, type InputBarHandle } from "../components/chat/InputBar.tsx";
import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider, useChat } from "../lib/ChatContext.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";
import { hasDraftFiles, readDraftFiles, resetDraftFiles, writeDraftFiles } from "../lib/draftFiles.ts";
import { SIGNED_OUT_EVENT, clearSessionContent } from "../lib/authSession.ts";

const present = (el: Element | null) => el !== null;
const originalFetch = globalThis.fetch;

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

function Switcher() {
  const { loadConversation } = useChat();
  return (
    <>
      <button type="button" onClick={() => void loadConversation("c-a")}>open A</button>
      <button type="button" onClick={() => void loadConversation("c-b")}>open B</button>
    </>
  );
}

function stubServer() {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
    if (path.startsWith("/api/voice/status")) return json({ enabled: false, status: "disabled" });
    if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
    const one = /^\/api\/conversations\/(c-[ab])/.exec(path);
    if (one) return json({ id: one[1], title: one[1], source: "user", messages: [] });
    if (path.startsWith("/api/conversations")) return json({ conversations: [], has_more: false });
    return json({});
  }) as typeof fetch;
}

afterEach(() => {
  cleanup();
  resetDraftFiles();
  globalThis.fetch = originalFetch;
  window.sessionStorage.clear();
});

const note = () => new File(["packing list"], "packing.txt", { type: "text/plain" });

describe("attachments stay with their thread", () => {
  it("switching away and back keeps an attached file, and the other thread's box is its own", async () => {
    stubServer();
    const ref = React.createRef<InputBarHandle>();
    render(
      <Shell>
        <Switcher />
        <InputBar ref={ref} onSend={() => {}} isLoading={false} />
      </Shell>,
    );
    await act(async () => void fireEvent.click(screen.getByRole("button", { name: "open A" })));
    act(() => ref.current!.addFiles([note()]));
    fireEvent.change(screen.getByRole("textbox", { name: "Message Edward" }), { target: { value: "for the cabin" } });
    assert.equal(present(screen.queryByRole("button", { name: "Remove packing.txt" })), true);

    await act(async () => void fireEvent.click(screen.getByRole("button", { name: "open B" })));
    assert.equal(present(screen.queryByRole("button", { name: "Remove packing.txt" })), false, "B has no attachment");
    assert.equal((screen.getByRole("textbox", { name: "Message Edward" }) as HTMLTextAreaElement).value, "");

    await act(async () => void fireEvent.click(screen.getByRole("button", { name: "open A" })));
    assert.equal(present(screen.queryByRole("button", { name: "Remove packing.txt" })), true, "A's attachment is back");
    assert.equal((screen.getByRole("textbox", { name: "Message Edward" }) as HTMLTextAreaElement).value, "for the cabin");
  });

  it("a send clears the thread's attachments for good", () => {
    const sent: { text: string; files?: File[] }[] = [];
    const ref = React.createRef<InputBarHandle>();
    const first = render(<InputBar ref={ref} onSend={(text, files) => sent.push({ text, files })} isLoading={false} />);
    act(() => ref.current!.addFiles([note()]));
    first.unmount();

    // Leaving the chat and coming back (a remount) keeps it, like a draft.
    const ref2 = React.createRef<InputBarHandle>();
    render(<InputBar ref={ref2} onSend={(text, files) => sent.push({ text, files })} isLoading={false} />);
    assert.equal(present(screen.queryByRole("button", { name: "Remove packing.txt" })), true);
    fireEvent.change(screen.getByRole("textbox", { name: "Message Edward" }), { target: { value: "here" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    assert.equal(sent.length, 1);
    assert.equal(sent[0].files?.[0]?.name, "packing.txt");
    cleanup();

    render(<InputBar onSend={() => {}} isLoading={false} />);
    assert.equal(present(screen.queryByRole("button", { name: "Remove packing.txt" })), false);
  });
});

// Sign-out clears what the tab holds of Ellis's (lib/authSession.ts
// clearSessionContent + SIGNED_OUT_EVENT). The typed drafts went; the
// attachments did not: signing out is a client navigation, so the map
// outlived it and the next sign-in on the tab put Ellis's files back in the
// composer (2026-09-27 review).
describe("sign-out", () => {
  it("forgets every thread's attachments, like the typed drafts", () => {
    writeDraftFiles("c-a", [note()]);
    const ref = React.createRef<InputBarHandle>();
    const composer = render(<InputBar ref={ref} onSend={() => {}} isLoading={false} />);
    act(() => ref.current!.addFiles([note()]));
    assert.equal(present(screen.queryByRole("button", { name: "Remove packing.txt" })), true);

    // What AuthContext.logout does before the page leaves for /login.
    act(() => {
      clearSessionContent();
      window.dispatchEvent(new window.CustomEvent(SIGNED_OUT_EVENT));
    });
    composer.unmount();

    assert.equal(readDraftFiles("c-a").length, 0, "another thread's attachments");
    assert.equal(readDraftFiles(null).length, 0, "the new chat's attachments");
    assert.equal(hasDraftFiles(), false, "nothing left to hold a reload for");
    render(<InputBar onSend={() => {}} isLoading={false} />);
    assert.equal(present(screen.queryByRole("button", { name: "Remove packing.txt" })), false, "signing back in finds an empty composer");
  });
});

describe("prefill", () => {
  it("the same text twice refills the box when the nonce changes", () => {
    const { rerender } = render(<InputBar onSend={() => {}} isLoading={false} prefill={{ text: "Plan the cabin trip", nonce: 1 }} />);
    const box = () => screen.getByRole("textbox", { name: "Message Edward" }) as HTMLTextAreaElement;
    assert.equal(box().value, "Plan the cabin trip");
    fireEvent.change(box(), { target: { value: "" } });

    rerender(<InputBar onSend={() => {}} isLoading={false} prefill={{ text: "Plan the cabin trip", nonce: 1 }} />);
    assert.equal(box().value, "", "the same prefill re-rendered is not a new one");

    rerender(<InputBar onSend={() => {}} isLoading={false} prefill={{ text: "Plan the cabin trip", nonce: 2 }} />);
    assert.equal(box().value, "Plan the cabin trip", "a second Try it with the same text refills");
  });

  it("a plain string still works", () => {
    render(<InputBar onSend={() => {}} isLoading={false} prefill="Walk Biscuit" />);
    assert.equal((screen.getByRole("textbox", { name: "Message Edward" }) as HTMLTextAreaElement).value, "Walk Biscuit");
  });
});
