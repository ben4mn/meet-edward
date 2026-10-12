// Defensive pin: frontend SSE reader yields each parsed event as the
// underlying ReadableStream chunk arrives — it does NOT collect into
// a buffer and yield at the end.
//
// This is the layer the user is staring at when "the response comes
// at one big payload". The backend fix (X-Accel-Buffering: no) makes
// chunks arrive incrementally over the wire; this test pins that the
// frontend reader doesn't undo that work.
//
// Run: node --test frontend/__tests__/streamChatEvents.test.ts

import { describe, it, before, after } from "node:test";
import assert from "node:assert/strict";

import { streamChatEvents } from "../lib/api.ts";


type FetchFn = typeof globalThis.fetch;
let originalFetch: FetchFn;


before(() => {
  originalFetch = globalThis.fetch;
});

after(() => {
  globalThis.fetch = originalFetch;
});


/** Make a Response whose body is a ReadableStream we can push chunks into. */
function makeControlledResponse(): {
  response: Response;
  enqueue: (data: string) => void;
  close: () => void;
} {
  let controller: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });

  const encoder = new TextEncoder();

  return {
    response: new Response(stream, {
      status: 200,
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-cache, no-transform",
        "x-accel-buffering": "no",
      },
    }),
    enqueue: (data: string) => controller.enqueue(encoder.encode(data)),
    close: () => controller.close(),
  };
}


describe("streamChatEvents", () => {
  it("yields each event as soon as its SSE frame is received", async () => {
    const { response, enqueue, close } = makeControlledResponse();

    globalThis.fetch = (async () => response) as unknown as FetchFn;

    const gen = streamChatEvents("hello");
    const arrivals: { event: unknown; t: number }[] = [];
    const t0 = Date.now();

    // Drive the generator concurrently with the producer so the
    // assertion is "the first event was delivered before we pushed
    // the second."
    const consumer = (async () => {
      for await (const event of gen) {
        arrivals.push({ event, t: Date.now() - t0 });
      }
    })();

    // Push the first frame and wait long enough that any reasonable
    // microtask flush has had a chance to deliver it.
    enqueue('data: {"type":"content","content":"hello"}\n\n');
    await new Promise((r) => setTimeout(r, 80));

    const firstSeen = arrivals.length;
    assert.equal(
      firstSeen,
      1,
      `expected the first event to be yielded after the first frame is enqueued, got ${firstSeen} events: ${JSON.stringify(arrivals)}`,
    );
    const firstT = arrivals[0].t;

    // Push the second frame after a deliberate 80ms gap.
    await new Promise((r) => setTimeout(r, 80));
    enqueue('data: {"type":"content","content":" world"}\n\n');
    await new Promise((r) => setTimeout(r, 80));
    close();
    await consumer;

    assert.equal(arrivals.length, 2, `expected 2 events total, got ${arrivals.length}`);

    const gap = arrivals[1].t - firstT;
    assert.ok(
      gap >= 100,
      `second event should arrive at least ~100ms after the first (we wait 80+80ms before pushing it). Got gap=${gap}ms; arrivals=${JSON.stringify(arrivals)}`,
    );
  });

  it("handles a frame that's split across multiple chunks", async () => {
    const { response, enqueue, close } = makeControlledResponse();

    globalThis.fetch = (async () => response) as unknown as FetchFn;

    const gen = streamChatEvents("hello");
    const events: unknown[] = [];

    const consumer = (async () => {
      for await (const event of gen) {
        events.push(event);
      }
    })();

    // Split a single SSE frame across three chunks.
    enqueue('data: {"type":"con');
    await new Promise((r) => setTimeout(r, 20));
    enqueue('tent","content":"hi"}');
    await new Promise((r) => setTimeout(r, 20));
    enqueue("\n\n");
    await new Promise((r) => setTimeout(r, 20));
    close();
    await consumer;

    assert.equal(events.length, 1, `expected 1 reassembled event, got ${events.length}`);
    assert.deepEqual(events[0], { type: "content", content: "hi" });
  });
});


// A reply to a journal entry Edward is still writing is refused with 409
// and a plain sentence (2026-09-25). The sentence has to reach Ellis, not
// the generic "Failed to send message".
describe("streamChatEvents refusals", () => {
  it("throws the server's own words on a 409", async () => {
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ detail: "Edward is still writing this entry — open it again in a moment." }), {
        status: 409,
        headers: { "content-type": "application/json" },
      })) as FetchFn;
    const { ChatRefusedError } = await import("../lib/api.ts");
    await assert.rejects(
      async () => {
        for await (const _ of streamChatEvents("why?", "entry-1")) void _;
      },
      (e: unknown) => e instanceof ChatRefusedError && e.message === "Edward is still writing this entry — open it again in a moment.",
    );
  });

  it("keeps the generic error for other failures", async () => {
    globalThis.fetch = (async () => new Response("boom", { status: 500 })) as FetchFn;
    const { ChatRefusedError } = await import("../lib/api.ts");
    await assert.rejects(
      async () => {
        for await (const _ of streamChatEvents("hi", "c-1")) void _;
      },
      (e: unknown) => e instanceof Error && !(e instanceof ChatRefusedError) && e.message === "Failed to send message",
    );
  });
});
