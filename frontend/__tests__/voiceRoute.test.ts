// R/G red (2026-09-26 review, "Listening after unmount"): leaving /chat
// while live voice is on (a push tap, ⌘-shortcuts, back) left isVoiceMode
// on — Today then read "Listening" with no mic open, queued messages held,
// and /chat reopened the mic unasked. ChatContext ends voice on the route
// change *away* from chat — never on "not on chat", which would break the
// palette's Talk live (push /chat, then enter voice 60 ms later).

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { shouldEndVoiceOnRoute } from "../lib/voiceRoute.ts";

describe("shouldEndVoiceOnRoute", () => {
  it("leaving chat ends voice", () => {
    assert.equal(shouldEndVoiceOnRoute("/chat", "/today", true), true);
    assert.equal(shouldEndVoiceOnRoute("/chat", "/settings/thoughts", true), true);
  });
  it("arriving at chat, or moving within it, does not", () => {
    assert.equal(shouldEndVoiceOnRoute("/today", "/chat", true), false, "the palette's Talk live");
    assert.equal(shouldEndVoiceOnRoute("/chat", "/chat", true), false);
    assert.equal(shouldEndVoiceOnRoute("/chat", "/chat?c=x", true), false);
  });
  it("nothing to end when voice is off", () => {
    assert.equal(shouldEndVoiceOnRoute("/chat", "/today", false), false);
  });
});
