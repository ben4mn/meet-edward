// Regression for the "thinking…" forever bug:
//
// fetchGreetingStream() consumes the SSE response from
// /api/greeting/stream. The bug we just fixed lived in ChatWindow's
// useEffect dep array — when loadingGreeting was in the deps, the
// state flip from setLoadingGreeting(true) re-ran the effect, which
// fired the *previous* render's cleanup, which set cancelled=true on
// the in-flight stream. Stream's chunks then short-circuited and
// setGreeting was never called.
//
// We can't easily test the React lifecycle from node:test, but we
// can pin the behavior of fetchGreetingStream itself: feed it a
// fake fetch that returns SSE chunks, assert it yields each chunk
// in order and stops at [DONE]. That's the contract ChatWindow
// depends on; if it ever changes we want a red test before the user
// sees "thinking" forever again.

import { describe, it, before, after } from "node:test";
import assert from "node:assert/strict";

import { fetchGreetingStream } from "../lib/api.ts";


function sseStream(blocks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(ctl) {
      if (i >= blocks.length) {
        ctl.close();
        return;
      }
      ctl.enqueue(enc.encode(blocks[i++]));
    },
  });
}

function fakeResponse(blocks: string[]): Response {
  // node-test runs in node — Response is the global undici impl.
  return new Response(sseStream(blocks), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}


describe("fetchGreetingStream", () => {
  const origFetch = globalThis.fetch;

  after(() => {
    globalThis.fetch = origFetch;
  });

  it("yields one item per data: chunk and stops at [DONE]", async () => {
    globalThis.fetch = (async () =>
      fakeResponse([
        'data: {"chunk": "Hey"}\n\n',
        'data: {"chunk": " "}\n\n',
        'data: {"chunk": "Ellis."}\n\n',
        'data: [DONE]\n\n',
      ])) as typeof fetch;

    const out: string[] = [];
    for await (const piece of fetchGreetingStream()) {
      out.push(piece);
    }
    assert.deepEqual(out, ["Hey", " ", "Ellis."]);
  });

  it("survives chunk boundaries inside a data: line", async () => {
    globalThis.fetch = (async () =>
      fakeResponse([
        'data: {"chunk": "He',
        'llo"}\n\n',
        'data: [DONE]\n\n',
      ])) as typeof fetch;

    const out: string[] = [];
    for await (const piece of fetchGreetingStream()) out.push(piece);
    assert.deepEqual(out, ["Hello"]);
  });

  it("ignores malformed data: lines without crashing", async () => {
    globalThis.fetch = (async () =>
      fakeResponse([
        'data: {not json}\n\n',
        'data: {"chunk": "ok"}\n\n',
        'data: [DONE]\n\n',
      ])) as typeof fetch;

    const out: string[] = [];
    for await (const piece of fetchGreetingStream()) out.push(piece);
    assert.deepEqual(out, ["ok"]);
  });

  it("ignores empty-chunk events", async () => {
    globalThis.fetch = (async () =>
      fakeResponse([
        'data: {"chunk": ""}\n\n',
        'data: {"chunk": "real"}\n\n',
        'data: [DONE]\n\n',
      ])) as typeof fetch;

    const out: string[] = [];
    for await (const piece of fetchGreetingStream()) out.push(piece);
    assert.deepEqual(out, ["real"]);
  });
});
