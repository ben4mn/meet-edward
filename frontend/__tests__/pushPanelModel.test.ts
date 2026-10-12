import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { pushHeadline, describeSubscribeError } from "../lib/pushPanelModel.ts";

const base = { supported: true, reachable: true, configured: true, permission: "default", subscribed: false } as const;

describe("pushHeadline", () => {
  it("never blames missing keys when the server simply could not be reached", () => {
    const h = pushHeadline({ ...base, reachable: false, configured: false });
    assert.equal(h.tone, "warn");
    assert.match(h.label, /reach/i);
    assert.doesNotMatch(h.label, /VAPID/);
  });
  it("blames keys only when the server says it is not configured", () => {
    const h = pushHeadline({ ...base, configured: false });
    assert.match(h.label, /VAPID/);
    assert.equal(h.tone, "bad");
  });
  it("walks the happy path", () => {
    assert.equal(pushHeadline({ ...base, permission: "granted", subscribed: true }).tone, "good");
    assert.match(pushHeadline({ ...base, permission: "granted" }).label, /no subscription/i);
    assert.match(pushHeadline({ ...base, permission: "denied" }).label, /blocked/i);
    assert.match(pushHeadline({ ...base, supported: false }).label, /not supported/i);
    assert.match(pushHeadline({ ...base, permission: "default" }).label, /turn it on/i);
  });
});

describe("describeSubscribeError", () => {
  it("keeps the browser's error name so the text is diagnostic", () => {
    const e = Object.assign(new Error("Registration failed - push service error"), { name: "AbortError" });
    assert.match(describeSubscribeError(e), /^AbortError: Registration failed - push service error/);
  });
  it("adds the iOS hint for a permission error", () => {
    const e = Object.assign(new Error("User denied push permission"), { name: "NotAllowedError" });
    assert.match(describeSubscribeError(e), /NotAllowedError.*Settings/);
  });
  it("copes with non-errors", () => {
    assert.equal(describeSubscribeError("boom"), "boom");
    assert.equal(describeSubscribeError(undefined), "Unknown error");
  });
});

// 2026-09-26 (Jarvis polish): one headline sentence in Edward's voice, and
// the last ten pushes from the ledger — what reached Ellis, from which part
// of Edward, and whether he opened it.
import { pushLogRows, pushSourceLabel } from "../lib/pushPanelModel.ts";

describe("pushHeadline, in Edward's voice", () => {
  it("says plainly whether he can reach this device", () => {
    assert.equal(pushHeadline({ ...base, permission: "granted", subscribed: true }).label, "I can reach you on this device.");
    assert.equal(pushHeadline({ ...base }).label, "I can't reach you on this device yet — turn it on below.");
  });
});

describe("pushSourceLabel", () => {
  it("names the part of Edward that spoke", () => {
    assert.equal(pushSourceLabel("mind"), "from a wake");
    assert.equal(pushSourceLabel("edward_thought"), "from a wake");
    assert.equal(pushSourceLabel("tool"), "from a chat");
    assert.equal(pushSourceLabel("approval"), "needs your approval");
    assert.equal(pushSourceLabel("voice_handoff"), "from a voice chat");
    assert.equal(pushSourceLabel("codex_limit"), "about my fuel");
    assert.equal(pushSourceLabel("some_new_thing"), "some new thing");
    assert.equal(pushSourceLabel(""), "");
  });
});

describe("pushLogRows", () => {
  const now = new Date("2026-09-26T18:00:00Z");
  const row = (i: number, over: Record<string, unknown> = {}) => ({
    id: `p${i}`, source: "mind", title: `Push ${i}`, body: "", url: `/chat?c=t${i}`,
    sent_at: new Date(now.getTime() - i * 3600e3).toISOString(), opened_at: null, held_reason: null, sent: 1, failed: 0,
    ...over,
  });
  it("keeps the ten newest, newest first, with where the tap goes", () => {
    const rows = pushLogRows([row(3), row(1), ...Array.from({ length: 10 }, (_, i) => row(i + 4)), row(2)], now);
    assert.equal(rows.length, 10);
    assert.deepEqual(rows.slice(0, 3).map((r) => r.id), ["p1", "p2", "p3"]);
    assert.equal(rows[0].href, "/chat?c=t1");
    assert.equal(rows[0].meta, "from a wake · 1h ago");
  });
  it("says opened, not opened, or held", () => {
    const [opened] = pushLogRows([row(1, { opened_at: now.toISOString() })], now);
    assert.equal(opened.state, "opened");
    const [unopened] = pushLogRows([row(1)], now);
    assert.equal(unopened.state, "not opened");
    const [held] = pushLogRows([row(1, { held_reason: "quiet_hours", sent: 0 })], now);
    assert.equal(held.state, "held");
    const [failed] = pushLogRows([row(1, { sent: 0, failed: 2 })], now);
    assert.equal(failed.state, "didn't arrive");
  });
  it("copes with junk", () => {
    assert.deepEqual(pushLogRows(null, now), []);
    assert.deepEqual(pushLogRows([{ nope: true }] as never, now), []);
  });
});
