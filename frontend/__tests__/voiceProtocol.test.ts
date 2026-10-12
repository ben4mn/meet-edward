// Server -> client frame parsing for the voice socket.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { parseServerFrame } from "../lib/voice/voiceProtocol.ts";

describe("parseServerFrame", () => {
  it("splits 0x02 audio frames off their prefix", () => {
    const buf = new Uint8Array([0x02, 1, 2, 3, 4]).buffer;
    const out = parseServerFrame(buf);
    assert.equal(out?.kind, "audio");
    if (out?.kind === "audio") assert.deepEqual(Array.from(new Uint8Array(out.pcm)), [1, 2, 3, 4]);
  });

  it("ignores binary frames with an unknown prefix or no payload", () => {
    assert.equal(parseServerFrame(new Uint8Array([0x09, 1]).buffer), null);
    assert.equal(parseServerFrame(new Uint8Array([0x02]).buffer), null);
  });

  it("parses JSON text frames and rejects garbage", () => {
    const out = parseServerFrame('{"type":"transcript","text":"hi"}');
    assert.equal(out?.kind, "json");
    if (out?.kind === "json") assert.equal(out.msg.type, "transcript");
    assert.equal(parseServerFrame("nope"), null);
  });
});
