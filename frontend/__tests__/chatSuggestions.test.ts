// R/G for the new-chat suggestions (2026-09-26).
//
// A new chat opens on Edward and his greeting, then two or three quiet ways
// in, taken from Today: the top thing that needs Ellis (as a prompt to handle
// it) — and "Resume" for the last thread (goals and ideas left Today with
// unit G, 2026-10-11). Tapping one only fills the composer; Ellis still
// presses send.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { chatSuggestions, resumeTarget } from "../lib/chatSuggestions.ts";

const FEED = {
  needs: [
    { id: "n1", kind: "dose", tone: "warn", priority: 1, title: "Biscuit · Carprofen 100mg", detail: "Due 18:00, 42 min late.", href: "/hub/pets", actions: [] },
    { id: "n2", kind: "calendar", tone: "info", priority: 0.8, title: "Call with the contractor", detail: "", href: "/hub/today", actions: [] },
  ],
  conversations: [
    { id: "hb", title: "Family", source: "heartbeat", updated_at: "", href: "/chat?c=hb" },
    { id: "c1", title: "Cabin weekend at Lake Tahoe", source: "user", updated_at: "", href: "/chat?c=c1" },
  ],
};

describe("chatSuggestions", () => {
  it("offers the top need", () => {
    const s = chatSuggestions(FEED);
    assert.deepEqual(s.map((x) => [x.kind, x.label]), [["need", "Biscuit · Carprofen 100mg"]]);
    assert.equal(s[0].prompt, "Biscuit · Carprofen 100mg — due 18:00, 42 min late. What should I do?");
  });

  it("words other needs as a request for help", () => {
    const s = chatSuggestions({ needs: [FEED.needs[1]] });
    assert.equal(s[0].prompt, "Help me with this: Call with the contractor.");
  });

  it("is empty without a feed, and caps the count", () => {
    assert.deepEqual(chatSuggestions(null), []);
    assert.equal(chatSuggestions(FEED, 0).length, 0);
  });
});

describe("resumeTarget", () => {
  it("is Ellis's last conversation, not one of Edward's threads", () => {
    assert.deepEqual(resumeTarget(FEED), { id: "c1", title: "Cabin weekend at Lake Tahoe", href: "/chat?c=c1" });
    assert.equal(resumeTarget({ conversations: [FEED.conversations[0]] }), null);
    assert.equal(resumeTarget(null), null);
  });
});
