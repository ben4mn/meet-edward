// R/G red for swipe-back in chat.
//
// The URL (/chat?c=<id>) is the source of truth for which conversation is
// on screen. Every way of opening a conversation must go through it, and
// the page must reconcile URL vs state on every navigation — including
// the browser's back/forward — without reloading a conversation that is
// already showing (that would clobber a live stream).

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { decideChatRoute } from "../lib/chatRoute.ts";

describe("decideChatRoute", () => {
  it("loads the conversation named in the URL when it is not the one on screen", () => {
    assert.equal(decideChatRoute({ cid: "A", currentId: undefined }), "load");
    assert.equal(decideChatRoute({ cid: "A", currentId: "B" }), "load");
  });
  it("does nothing when the URL already matches what is on screen", () => {
    assert.equal(decideChatRoute({ cid: "A", currentId: "A" }), "skip");
  });
  it("starts a fresh chat when the URL has no conversation but one is on screen", () => {
    assert.equal(decideChatRoute({ cid: null, currentId: "A" }), "new");
  });
  it("leaves a fresh chat alone when the URL has no conversation and none is on screen", () => {
    assert.equal(decideChatRoute({ cid: null, currentId: undefined }), "skip");
  });
});

// /chat?voice=1 opens live voice (the "Talk live" home-screen shortcut,
// 2026-09-26). Read once, then dropped from the URL so back or a reload
// does not reopen the mic.
import { readVoiceParam, withoutVoiceParam } from "../lib/chatRoute.ts";

describe("voice param", () => {
  it("voice=1 (or true) asks for live voice", () => {
    assert.equal(readVoiceParam(new URLSearchParams("voice=1")), true);
    assert.equal(readVoiceParam(new URLSearchParams("voice=true")), true);
    assert.equal(readVoiceParam(new URLSearchParams("c=abc&voice=1")), true);
  });
  it("anything else does not", () => {
    assert.equal(readVoiceParam(new URLSearchParams("")), false);
    assert.equal(readVoiceParam(new URLSearchParams("voice=0")), false);
    assert.equal(readVoiceParam(null), false);
  });
  it("drops voice and keeps the rest", () => {
    assert.equal(withoutVoiceParam("?c=abc&voice=1"), "?c=abc");
    assert.equal(withoutVoiceParam("voice=1"), "");
  });
});
