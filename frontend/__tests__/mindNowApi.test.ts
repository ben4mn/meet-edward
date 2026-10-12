// GET /api/mind/now — Edward's own live state (a wake running now, his last
// wakes, what waits on Ellis, the quota, his hands). The PWA's presence reads
// it every 20 s, so the client must never throw on a partial or odd body
// (the backend degrades each source to null/0 on its own), and must tell
// "the backend is unreachable" (offline) apart from "the backend answers
// but has no such route" (an older backend: no mind signals, not offline).

import { describe, it, afterEach } from "node:test";
import assert from "node:assert/strict";

import { normalizeMindNow, fetchMindNow, lastActedKey, EMPTY_MIND_NOW } from "../lib/mindNowApi.ts";

const FULL = {
  now: "2026-09-26T15:00:00Z",
  waking: { reason: "messages", started_at: "2026-09-26T14:59:30Z", conversation_id: "edward-mind" },
  last: { at: "2026-09-26T14:00:00Z", reason: "morning", outcome: "acted", line: "Told Ellis about the vet.", thought_id: "t-1" },
  recent: [
    { at: "2026-09-26T14:00:00Z", reason: "morning", outcome: "acted", line: "Told Ellis about the vet.", thought_id: "t-1" },
    { at: "2026-09-26T07:00:00Z", reason: "self", outcome: "left_alone", line: null, thought_id: null },
  ],
  next_wake_at: "2026-09-26T18:00:00Z",
  approvals_waiting: 1,
  unread_thoughts: 2,
  quota: { used_percent: 41.5, exhausted: false, resets_at: "2026-09-29T09:00:00Z" },
  hands: { connected: true, down_for_s: null },
};

describe("normalizeMindNow", () => {
  it("keeps a full body as it is", () => {
    const n = normalizeMindNow(FULL);
    assert.deepEqual(n, FULL);
  });

  it("an empty or junk body is the empty state, never a throw", () => {
    assert.deepEqual(normalizeMindNow(null), EMPTY_MIND_NOW);
    assert.deepEqual(normalizeMindNow("nope"), EMPTY_MIND_NOW);
    assert.deepEqual(normalizeMindNow({}), EMPTY_MIND_NOW);
    assert.deepEqual(normalizeMindNow([]), EMPTY_MIND_NOW);
  });

  it("each degraded source reads as unknown on its own", () => {
    const n = normalizeMindNow({
      ...FULL,
      waking: { started_at: "x" }, // no reason: not a wake
      last: null,
      recent: [null, { reason: "evening" }, FULL.recent[1]],
      approvals_waiting: null,
      unread_thoughts: "3",
      quota: null,
      hands: { connected: null, down_for_s: 95 },
    });
    assert.equal(n.waking, null);
    assert.equal(n.last, null);
    assert.equal(n.recent.length, 1, "rows without a time are dropped");
    assert.equal(n.approvals_waiting, 0);
    assert.equal(n.unread_thoughts, 0);
    assert.deepEqual(n.quota, { used_percent: null, exhausted: false, resets_at: null });
    assert.deepEqual(n.hands, { connected: null, down_for_s: 95 });
    assert.equal(n.next_wake_at, FULL.next_wake_at);
  });

  it("keeps the quota's period only when it is the Claude day (absent = the Codex week)", () => {
    assert.equal(normalizeMindNow({ ...FULL, quota: { ...FULL.quota, period: "day" } }).quota.period, "day");
    assert.equal(normalizeMindNow({ ...FULL, quota: { ...FULL.quota, period: "week" } }).quota.period, undefined);
    assert.equal(normalizeMindNow({ ...FULL, quota: { ...FULL.quota, period: "month" } }).quota.period, undefined);
  });

  it("keeps at most five recent wakes and never a negative count", () => {
    const row = FULL.recent[0];
    const n = normalizeMindNow({ ...FULL, recent: Array.from({ length: 9 }, () => row), approvals_waiting: -2 });
    assert.equal(n.recent.length, 5);
    assert.equal(n.approvals_waiting, 0);
  });
});

describe("lastActedKey", () => {
  it("names the last wake only when it acted", () => {
    assert.equal(lastActedKey(normalizeMindNow(FULL)), "2026-09-26T14:00:00Z|morning");
    assert.equal(lastActedKey(normalizeMindNow({ ...FULL, last: FULL.recent[1] })), null);
    assert.equal(lastActedKey(null), null);
  });
});

describe("fetchMindNow", () => {
  const original = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = original;
  });

  function respond(fn: (url: string, init?: RequestInit) => Promise<Response> | Response) {
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => fn(String(input), init)) as typeof fetch;
  }

  it("asks /api/mind/now with the session cookie", async () => {
    let seen: { url: string; credentials?: RequestCredentials } | null = null;
    respond((url, init) => {
      seen = { url, credentials: init?.credentials };
      return new Response(JSON.stringify(FULL), { status: 200 });
    });
    const r = await fetchMindNow();
    assert.equal(r.status, "ok");
    assert.deepEqual(r.status === "ok" ? r.data : null, FULL);
    assert.ok(seen!.url.endsWith("/api/mind/now"));
    assert.equal(seen!.credentials, "include");
  });

  it("a network error or a 5xx is unreachable (the tunnel answers 502/530 when the mini is down)", async () => {
    respond(() => Promise.reject(new TypeError("Failed to fetch")));
    assert.equal((await fetchMindNow()).status, "unreachable");
    respond(() => new Response("bad gateway", { status: 502 }));
    assert.equal((await fetchMindNow()).status, "unreachable");
  });

  it("a 404 is an older backend: reachable, no mind signals", async () => {
    respond(() => new Response("{}", { status: 404 }));
    assert.equal((await fetchMindNow()).status, "missing");
  });

  it("a body that is not JSON is still reachable, read as the empty state", async () => {
    respond(() => new Response("<html>", { status: 200 }));
    const r = await fetchMindNow();
    assert.equal(r.status, "ok");
    assert.deepEqual(r.status === "ok" ? r.data : null, EMPTY_MIND_NOW);
  });

  it("an aborted poll is reported as aborted, not as the backend being down", async () => {
    respond((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const c = new AbortController();
    const p = fetchMindNow({ signal: c.signal });
    c.abort();
    assert.equal((await p).status, "aborted");
  });
});
