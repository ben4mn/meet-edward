// R/G red: the conversation header's words (lib/conversationHeader.ts).
//
// A new chat has no row in the list until its first turn lands, so the
// header used to be blank and then jump in. Now it shows a provisional
// title from Ellis's first line at once, and the real title replaces it in
// place. Where the thread came from is a neutral dot and a plain word —
// no amber "heartbeat", no "you" pill on Ellis's own chats.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { headerTitle, headerSource } from "../lib/conversationHeader.ts";

const conv = (over: Record<string, unknown> = {}) =>
  ({ id: "c", title: "Cabin weekend", source: "user", channel: "text", updated_at: "2026-09-26T19:00:00Z", ...over }) as never;

describe("headerTitle", () => {
  it("the conversation's own title when there is one", () => {
    assert.equal(headerTitle(conv(), [{ role: "user", content: "hi" }]), "Cabin weekend");
  });
  it("a new chat: Ellis's first line, trimmed and clipped", () => {
    assert.equal(headerTitle(null, [{ role: "user", content: "  Plan the cabin weekend\nwith dogs" }]), "Plan the cabin weekend");
    const long = "x".repeat(120);
    const t = headerTitle(null, [{ role: "user", content: long }]);
    assert.ok(t.length <= 60, t);
    assert.ok(t.endsWith("…"), t);
  });
  it("a placeholder title ('New conversation', 'Untitled') loses to Ellis's line", () => {
    assert.equal(headerTitle(conv({ title: "New conversation" }), [{ role: "user", content: "vet on Friday?" }]), "vet on Friday?");
    assert.equal(headerTitle(conv({ title: "" }), [{ role: "user", content: "vet on Friday?" }]), "vet on Friday?");
  });
  it("a memo or an attachment alone: 'New chat'", () => {
    assert.equal(headerTitle(null, [{ role: "user", content: "   " }]), "New chat");
    assert.equal(headerTitle(null, []), "New chat");
  });
  it("heartbeat threads lose the iMessage: prefix", () => {
    assert.equal(headerTitle(conv({ title: "iMessage: Family", source: "heartbeat" }), []), "Family");
  });
});

describe("headerSource", () => {
  it("Ellis's own text chat: nothing", () => {
    assert.equal(headerSource(conv()), null);
  });
  it("voice", () => {
    assert.deepEqual(headerSource(conv({ channel: "voice" })), { label: "Voice", edward: false });
  });
  it("inbound threads: the channel, neutral", () => {
    assert.deepEqual(headerSource(conv({ source: "heartbeat", channel: "imessage" })), { label: "iMessage", edward: false });
    assert.deepEqual(headerSource(conv({ source: "external_message", channel: "sms" })), { label: "Text", edward: false });
  });
  it("what Edward started says so, in his colour", () => {
    assert.deepEqual(headerSource(conv({ source: "edward_thought" })), { label: "Edward", edward: true });
    assert.deepEqual(headerSource(conv({ source: "scheduled_event" })), { label: "Scheduled", edward: true });
    assert.deepEqual(headerSource(conv({ source: "orchestrator_worker" })), { label: "Worker", edward: true });
  });
  it("no conversation, no source", () => {
    assert.equal(headerSource(null), null);
  });
});
