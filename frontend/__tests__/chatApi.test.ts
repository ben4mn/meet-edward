// The chat's own API calls (2026-09-26 review).
//
// - A conversation delete may be sent as the page goes away (the undo
//   window flushed on pagehide / hidden): it must be a keepalive request, or
//   Safari cancels it with the page and the thread is back next launch.
// - Read aloud can be cancelled while the voice is still being made: the
//   request takes an AbortSignal, and an aborted one is never played.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";

import { deleteConversation, synthesizeSpeech } from "../lib/api.ts";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

function capture(response: () => Response) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    return response();
  }) as typeof fetch;
  return calls;
}

describe("deleteConversation", () => {
  it("is a keepalive DELETE, so it survives the page going away", async () => {
    const calls = capture(() => new Response(JSON.stringify({ status: "deleted", id: "c1" }), { status: 200 }));
    await deleteConversation("c1");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].init?.method, "DELETE");
    assert.equal(calls[0].init?.keepalive, true);
  });
});

describe("synthesizeSpeech", () => {
  it("passes the caller's AbortSignal to the request", async () => {
    const calls = capture(() => new Response(new ArrayBuffer(4), { status: 200 }));
    const ac = new AbortController();
    await synthesizeSpeech("hello", ac.signal);
    assert.equal(calls[0].init?.signal, ac.signal);
  });
});
