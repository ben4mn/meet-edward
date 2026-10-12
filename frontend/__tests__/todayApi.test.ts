// Today's two requests (review 2026-09-26): an action hands back what the
// server said (a held send's `delivered`, its result text), and the feed
// request gives up rather than hang with a stale, tappable feed on screen.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";

import { getToday, runTodayAction } from "../lib/api.ts";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

const approve = { id: "approve", label: "Approve", method: "POST", url: "/api/approvals/31", body: { decision: "approve" }, primary: true };

describe("runTodayAction", () => {
  it("returns the server's answer", async () => {
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ status: "approved", delivered: false, result_text: "Failed to send iMessage: timeout" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })) as typeof fetch;
    const out = (await runTodayAction(approve)) as { delivered?: boolean };
    assert.equal(out?.delivered, false);
  });
  it("returns null when the answer is not JSON", async () => {
    globalThis.fetch = (async () => new Response(null, { status: 204 })) as typeof fetch;
    assert.equal(await runTodayAction(approve), null);
  });
  it("still throws with the server's detail on a failure", async () => {
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ detail: "That dose was for yesterday" }), { status: 409, headers: { "Content-Type": "application/json" } })) as typeof fetch;
    await assert.rejects(runTodayAction(approve), /That dose was for yesterday/);
  });
});

describe("getToday", () => {
  it("gives up on a request that never answers", async () => {
    let aborted = false;
    globalThis.fetch = ((_url: RequestInfo | URL, init?: RequestInit) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          aborted = true;
          reject(new DOMException("aborted", "AbortError"));
        });
      })) as typeof fetch;
    // Guarded, so an unbounded request fails this test instead of hanging it.
    const hung = new Promise((_r, reject) => setTimeout(() => reject(new Error("still waiting after 1 s")), 1000).unref());
    await assert.rejects(Promise.race([getToday({ timeoutMs: 30 }), hung]), (e: Error) => e.message !== "still waiting after 1 s");
    assert.equal(aborted, true);
  });
});
