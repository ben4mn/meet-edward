// EdwardPresenceProvider end to end (fetch mocked, the chat SSE stream
// scripted frame by frame, real timers at test speed). Pins what the pure
// model cannot on its own:
//
//  - mind/now is polled on mount, never while the tab is hidden, at once on
//    return (with a perk after a while away), and backs off on failure;
//  - two failed polls in a row read as offline, and so does the browser
//    going offline; a 404 (older backend) is not offline;
//  - the chat turn drives his mood and status, a tool ending well nods, and
//    a wake that acted since the last poll is the acted beat;
//  - the day's first open waves once;
//  - outside a provider the hook is a safe resting fallback.

import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render, act, cleanup } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider, useChat } from "../lib/ChatContext.tsx";
import { EdwardPresenceProvider, useEdwardPresence, LAST_WAVE_KEY } from "../lib/presence.tsx";
import { MIND_NOW_CACHE_KEY, encodeMindNowCache } from "../lib/presenceModel.ts";
import { normalizeMindNow } from "../lib/mindNowApi.ts";

type Presence = ReturnType<typeof useEdwardPresence>;
let presence: Presence;
let chat: ReturnType<typeof useChat> | null = null;
const beats: string[] = [];

function Probe() {
  presence = useEdwardPresence();
  return null;
}
function ChatProbe() {
  chat = useChat();
  return null;
}
function BeatLog() {
  const p = useEdwardPresence();
  React.useEffect(() => {
    if (p.beat) beats.push(p.beat.beat);
  }, [p.beat?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };

// ---- a scripted backend ---------------------------------------------------

const originalFetch = globalThis.fetch;
let mindCalls = 0;
let mindReply: () => Response | Promise<Response>;
let chatStreams: Array<{ send: (e: Record<string, unknown>) => void; close: () => void }> = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const QUIET = {
  now: "2026-09-26T15:00:00Z",
  waking: null,
  last: { at: "2026-09-26T14:00:00Z", reason: "self", outcome: "left_alone", line: null, thought_id: null },
  recent: [],
  next_wake_at: null,
  approvals_waiting: 0,
  unread_thoughts: 0,
  quota: { used_percent: 30, exhausted: false, resets_at: null },
  hands: { connected: true, down_for_s: null },
};

function installFetch() {
  mindCalls = 0;
  chatStreams = [];
  mindReply = () => json(QUIET);
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (path.startsWith("/api/mind/now")) {
      mindCalls += 1;
      return mindReply();
    }
    if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
    if (path.startsWith("/api/voice/status")) return json({ enabled: false, status: "disabled" });
    if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
    if (path.startsWith("/api/conversations?")) return json({ conversations: [], has_more: false });
    if (path === "/api/chat") {
      let controller!: ReadableStreamDefaultController<Uint8Array>;
      const stream = new ReadableStream<Uint8Array>({ start: (c) => { controller = c; } });
      const enc = new TextEncoder();
      init?.signal?.addEventListener("abort", () => {
        try { controller.error(new DOMException("aborted", "AbortError")); } catch { /* closed */ }
      });
      chatStreams.push({
        send: (e) => controller.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)),
        close: () => controller.close(),
      });
      return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
    }
    return json({});
  }) as typeof fetch;
}

let visibility: DocumentVisibilityState = "visible";
function setVisibility(v: DocumentVisibilityState) {
  visibility = v;
  document.dispatchEvent(new window.Event("visibilitychange"));
}

let clockNow = Date.parse("2026-09-26T15:00:00Z");
const clock = () => clockNow;

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function flush(ms = 20) {
  await act(async () => {
    await tick(ms);
  });
}

const FAST = { pollMs: 60_000, retryMs: 20 };

async function mountBare(timing = FAST) {
  render(
    <EdwardPresenceProvider timing={timing} clock={clock}>
      <Probe />
      <BeatLog />
    </EdwardPresenceProvider>,
  );
  await flush(30);
}

async function mountWithChat(timing = FAST) {
  try { window.localStorage.setItem("edward_auth_hint", "1"); } catch { /* none */ }
  render(
    <AppRouterContext.Provider value={router as never}>
      <AuthProvider>
        <ChatProvider>
          <EdwardPresenceProvider timing={timing} clock={clock}>
            <ChatProbe />
            <Probe />
            <BeatLog />
          </EdwardPresenceProvider>
        </ChatProvider>
      </AuthProvider>
    </AppRouterContext.Provider>,
  );
  await flush(30);
}

async function emit(...events: Array<Record<string, unknown>>) {
  await act(async () => {
    for (const e of events) chatStreams[0].send(e);
    await tick(25);
  });
}

beforeEach(() => {
  installFetch();
  beats.length = 0;
  chat = null;
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
  Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => true });
  clockNow = Date.parse("2026-09-26T15:00:00Z");
  // Already waved today, so the wave test is the only one that sees it.
  try { window.localStorage.setItem(LAST_WAVE_KEY, new Date(clockNow).toISOString()); } catch { /* none */ }
  try { window.localStorage.removeItem(MIND_NOW_CACHE_KEY); } catch { /* none */ }
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("polling mind/now", () => {
  it("polls on mount and reads his state from it", async () => {
    mindReply = () => json({ ...QUIET, waking: { reason: "morning", started_at: "2026-09-26T14:59:00Z", conversation_id: "edward-mind" } });
    await mountBare();
    assert.equal(mindCalls, 1);
    assert.equal(presence.available, true);
    assert.equal(presence.online, true);
    assert.equal(presence.mindNow?.waking?.reason, "morning");
    assert.equal(presence.mood, "think");
    assert.equal(presence.status, "Thinking · morning wake");
  });

  it("never polls while hidden; polls at once on return, and perks after a while away", async () => {
    await mountBare({ pollMs: 40, retryMs: 20 });
    const before = mindCalls;
    await act(async () => {
      setVisibility("hidden");
      await tick(120);
    });
    assert.equal(mindCalls, before, "no poll while hidden");
    clockNow += 20_000;
    await act(async () => {
      setVisibility("visible");
      await tick(10);
    });
    assert.equal(mindCalls, before + 1, "one poll straight away on return");
    assert.deepEqual(beats, ["perk"]);
  });

  it("a flick away and back is no perk", async () => {
    await mountBare();
    await act(async () => {
      setVisibility("hidden");
      await tick(5);
    });
    clockNow += 2_000;
    await act(async () => {
      setVisibility("visible");
      await tick(10);
    });
    assert.deepEqual(beats, []);
  });

  it("two failed polls in a row read as offline; the next answer brings him back", async () => {
    // Wide margins (2026-09-26): with retryMs 80 the "still 2 calls" check
    // sat 10 ms before the backoff retry and failed about one run in three
    // under node --test's parallel workers. Now every check is ≥ 100 ms from
    // the timer it races: retry at ~250 ms, backoff try at ~750 ms.
    mindReply = () => new Response("bad gateway", { status: 502 });
    await mountBare({ pollMs: 60_000, retryMs: 250 });
    assert.equal(mindCalls, 1);
    assert.equal(presence.online, true, "one failure is a hiccup");
    await flush(350); // ~380 ms: the quick retry (at ~250) failed too
    assert.equal(mindCalls, 2);
    assert.equal(presence.online, false);
    assert.equal(presence.mood, "offline");
    assert.equal(presence.status, "Offline");
    mindReply = () => json(QUIET);
    await flush(250); // ~630 ms: the next try waits twice as long (~750)
    assert.equal(mindCalls, 2, "backing off: the next try waits twice as long");
    await flush(300); // ~930 ms
    assert.equal(mindCalls, 3);
    assert.equal(presence.online, true);
  });

  it("an older backend without the route is not offline", async () => {
    mindReply = () => json({ detail: "Not Found" }, 404);
    await mountBare();
    await flush(60);
    assert.equal(presence.online, true);
    assert.equal(presence.mindNow, null);
  });

  it("the browser going offline is offline", async () => {
    await mountBare();
    await act(async () => {
      Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => false });
      window.dispatchEvent(new window.Event("offline"));
      await tick(5);
    });
    assert.equal(presence.mood, "offline");
  });

  it("a wake that acted since the last poll is the acted beat; the first poll is only a baseline", async () => {
    const acted = { ...QUIET, last: { at: "2026-09-26T15:00:00Z", reason: "messages", outcome: "acted", line: "Replied to Parker", thought_id: "t-1" } };
    mindReply = () => json(acted);
    await mountBare();
    assert.deepEqual(beats, [], "an old acted wake is not replayed on open");
    mindReply = () => json({ ...acted, last: { ...acted.last, at: "2026-09-26T15:05:00Z" } });
    await act(async () => {
      presence.refresh();
      await tick(20);
    });
    assert.deepEqual(beats, ["acted"]);
  });
});

describe("before the first answer (2026-09-26)", () => {
  const pending = () => new Promise<Response>(() => {});
  const cached = (over: Record<string, unknown>, ageMs: number) =>
    window.localStorage.setItem(
      MIND_NOW_CACHE_KEY,
      encodeMindNowCache(normalizeMindNow({ ...QUIET, ...over }), clockNow - ageMs),
    );

  it("makes no claim: no 'Resting' while an approval may be waiting", async () => {
    mindReply = pending;
    await mountBare();
    assert.equal(presence.status, "");
    assert.equal(presence.label, "Edward");
    assert.equal(presence.mood, "idle");
    assert.equal(presence.signals.known, false);
  });

  it("paints the last answer at once when it is under ten minutes old", async () => {
    cached({ approvals_waiting: 2 }, 3 * 60_000);
    mindReply = pending;
    await mountBare();
    assert.equal(presence.status, "Waiting on your approval · 2");
    assert.equal(presence.mood, "attention");
  });

  it("ignores an answer older than ten minutes", async () => {
    cached({ approvals_waiting: 2 }, 11 * 60_000);
    mindReply = pending;
    await mountBare();
    assert.equal(presence.status, "");
  });

  it("keeps each answer for the next cold open", async () => {
    mindReply = () => json({ ...QUIET, unread_thoughts: 4 });
    await mountBare();
    const raw = window.localStorage.getItem(MIND_NOW_CACHE_KEY);
    assert.ok(raw, "cached");
    assert.equal(JSON.parse(raw!).data.unread_thoughts, 4);
    assert.equal(presence.status, "Wants you · 4 new thoughts");
  });

  it("an older backend (404) drops a cached answer rather than keep claiming it", async () => {
    cached({ approvals_waiting: 2 }, 60_000);
    mindReply = () => json({ detail: "Not Found" }, 404);
    await mountBare();
    assert.equal(presence.mindNow, null);
    assert.equal(presence.status, "Resting", "it answered: now resting is known");
    assert.equal(window.localStorage.getItem(MIND_NOW_CACHE_KEY), null);
  });

  it("a cached acted wake is not replayed as a beat when the first poll lands", async () => {
    const acted = { last: { at: "2026-09-26T14:58:00Z", reason: "messages", outcome: "acted", line: "Replied", thought_id: "t" } };
    cached({ last: { at: "2026-09-26T14:00:00Z", reason: "self", outcome: "left_alone", line: null, thought_id: null } }, 60_000);
    mindReply = () => json({ ...QUIET, ...acted });
    await mountBare();
    assert.deepEqual(beats, [], "the first poll is still only a baseline");
  });
});

describe("the chat turn", () => {
  it("drives his mood and status; a tool that went well nods", async () => {
    await mountWithChat();
    await act(async () => {
      void chat!.sendMessage("look it up");
      await tick(20);
    });
    await emit({ type: "tool_start", conversation_id: "c-new", tool_name: "web_search" });
    assert.equal(presence.mood, "work");
    assert.equal(presence.status, "Working · 1 tool");
    await emit({ type: "tool_end", conversation_id: "c-new", tool_name: "web_search", result: "3 results" });
    assert.deepEqual(beats, ["nod"]);
    await emit({ type: "content", conversation_id: "c-new", content: "Found it." });
    assert.equal(presence.mood, "speak");
    assert.equal(presence.status, "Writing");
    await act(async () => {
      chatStreams[0].send({ type: "done", conversation_id: "c-new" });
      chatStreams[0].close();
      await tick(30);
    });
    assert.equal(presence.mood, "idle");
    assert.equal(presence.signals.turn, null);
  });
});

describe("the day's first open", () => {
  it("waves once a local day", async () => {
    window.localStorage.removeItem(LAST_WAVE_KEY);
    await mountBare();
    assert.deepEqual(beats, ["wave"]);
    cleanup();
    beats.length = 0;
    await mountBare();
    assert.deepEqual(beats, [], "not twice the same day");
  });
});

describe("outside a provider", () => {
  it("is a resting fallback, never a throw", async () => {
    render(<Probe />);
    await flush(5);
    assert.equal(presence.available, false);
    assert.equal(presence.online, true);
    assert.equal(presence.beat, null);
    assert.equal(presence.mindNow, null);
    assert.equal(mindCalls, 0);
  });
});
