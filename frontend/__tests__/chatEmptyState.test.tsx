// The new chat (2026-09-26): Edward himself, large and alive, his greeting
// streaming in word by word, and a few quiet ways in from Today — each one
// fills the composer (/chat?prompt=…), it never sends — plus "Resume".

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { ChatEmptyState } from "../components/chat/ChatEmptyState.tsx";

const present = (el: Element | null) => el !== null;
afterEach(cleanup);

const FEED = {
  needs: [{ id: "n1", kind: "calendar", tone: "info", priority: 1, title: "Call with the contractor", detail: "", href: "", actions: [] }],
  conversations: [{ id: "c1", title: "Cabin weekend", source: "user", updated_at: "", href: "/chat?c=c1" }],
};

async function* words() {
  yield "Evening, Ellis. ";
  yield "Biscuit's meds are waiting.";
}

describe("ChatEmptyState", () => {
  it("shows one large Edward and streams the greeting word by word", async () => {
    const { container } = render(
      <ChatEmptyState loadFeed={async () => FEED as never} greeting={{ stream: words, once: async () => "Hey." }} />,
    );
    const avatars = container.querySelectorAll(".pane-avatar");
    assert.equal(avatars.length, 1);
    assert.equal(avatars[0].getAttribute("data-variant"), "full");
    await waitFor(() => assert.equal(present(screen.queryByText("Biscuit's")), true));
    const heading = screen.getByRole("heading", { level: 1 });
    assert.equal(heading.textContent?.replace(/\s+/g, " ").trim(), "Evening, Ellis. Biscuit's meds are waiting.");
  });

  it("offers ways in that fill the composer, and resumes Ellis's last thread", async () => {
    render(<ChatEmptyState loadFeed={async () => FEED as never} greeting={{ stream: words, once: async () => "Hey." }} />);
    const need = await screen.findByRole("link", { name: /call with the contractor/i });
    assert.equal(need.getAttribute("href"), `/chat?prompt=${encodeURIComponent("Help me with this: Call with the contractor.")}`);
    const resume = screen.getByRole("link", { name: /resume: cabin weekend/i });
    assert.equal(resume.getAttribute("href"), "/chat?c=c1");
  });

  it("stays calm when Today cannot be read", async () => {
    render(<ChatEmptyState loadFeed={async () => { throw new Error("down"); }} greeting={{ stream: words, once: async () => "Hey." }} />);
    await waitFor(() => assert.equal(present(screen.queryByText("Biscuit's")), true));
    assert.equal(screen.queryAllByRole("link").length, 0);
  });
});

// 2026-09-26 review: on a new chat the presence bar steps back for the hero,
// so "Waiting on your approval" had no words anywhere on the screen Ellis
// lands on most. The hero carries the status as a caption (tap → the sheet)
// whenever there is something to say — not for a plain "Resting".
import { heroCaption } from "../components/chat/ChatEmptyState.tsx";

describe("heroCaption", () => {
  it("says what he is doing or waiting on", () => {
    assert.equal(heroCaption({ available: true, mood: "attention", status: "Waiting on your approval" }), "Waiting on your approval");
    assert.equal(heroCaption({ available: true, mood: "work", status: "Working · 2 tools" }), "Working · 2 tools");
    assert.equal(heroCaption({ available: true, mood: "offline", status: "Offline" }), "Offline");
  });
  it("stays quiet when he is simply here", () => {
    assert.equal(heroCaption({ available: true, mood: "idle", status: "Resting" }), null);
    assert.equal(heroCaption({ available: true, mood: "sleepy", status: "Resting" }), null);
    assert.equal(heroCaption({ available: false, mood: "attention", status: "x" }), null);
  });
  it("a resting Edward whose hands are down still says so (never 'fine' when his hands are down)", () => {
    assert.equal(
      heroCaption({ available: true, mood: "idle", status: "Resting · Chrome link down", signals: { handsDown: true } }),
      "Resting · Chrome link down",
    );
    assert.equal(heroCaption({ available: true, mood: "sleepy", status: "Resting", signals: { handsDown: false } }), null);
  });
});
