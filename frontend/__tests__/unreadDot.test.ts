import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { shouldShowUnreadDot } from "../lib/unreadDot.ts";

describe("shouldShowUnreadDot", () => {
  it("shows for Edward's own thought conversations he pinged about", () => {
    assert.equal(shouldShowUnreadDot({ notified_user: true, source: "edward_thought" }), true);
  });
  it("still shows for heartbeat and scheduled", () => {
    assert.equal(shouldShowUnreadDot({ notified_user: true, source: "heartbeat" }), true);
    assert.equal(shouldShowUnreadDot({ notified_user: true, source: "scheduled_event" }), true);
  });
  it("never shows for Ellis's own conversations or read ones", () => {
    assert.equal(shouldShowUnreadDot({ notified_user: true, source: "user" }), false);
    assert.equal(shouldShowUnreadDot({ notified_user: false, source: "edward_thought" }), false);
  });
});
