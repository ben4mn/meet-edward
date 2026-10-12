// ChatContext end to end against a scripted backend (fetch mocked, the
// chat SSE stream driven frame by frame). Pins what the pure reducers
// cannot on their own:
//
//  - the turn: streamingMessageId + turn.phase follow the stream, and every
//    ending (done, Stop, a failed request) leaves nothing live;
//  - a stream whose conversation is no longer on screen never touches the
//    screen (it used to yank conversationId back on its first event), and a
//    thread re-opened mid-answer gets the live turn back;
//  - a recently opened thread renders from cache at once and revalidates;
//  - a send while a thread is still loading waits for it (queued, then sent);
//  - the work rail is rebuilt from the history trace.

import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render, act, cleanup } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider, useChat } from "../lib/ChatContext.tsx";

type Ctx = ReturnType<typeof useChat>;
let ctx: Ctx;
function Probe() {
  ctx = useChat();
  return null;
}

const router = {
  push: () => {},
  replace: () => {},
  back: () => {},
  forward: () => {},
  refresh: () => {},
  prefetch: () => {},
};

// ---- a scripted backend ---------------------------------------------------

interface SseHandle {
  send: (event: Record<string, unknown>) => void;
  close: () => void;
  fail: (err?: Error) => void;
  body: { message: string; conversation_id?: string };
}

const originalFetch = globalThis.fetch;
let chatCalls: SseHandle[] = [];
let conversations: Record<string, { messages: Array<Record<string, unknown>>; title?: string }> = {};
let conversationGets: string[] = [];
let holdConversation: Record<string, Promise<void>> = {};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

// A test can answer a request itself first (search, DELETE, a held /processing).
let extraRoute: ((path: string, init?: RequestInit) => Response | Promise<Response> | undefined) | null = null;
let voiceStatusCalls = 0;

function installFetch() {
  chatCalls = [];
  conversationGets = [];
  holdConversation = {};
  extraRoute = null;
  voiceStatusCalls = 0;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const extra = extraRoute?.(path, init);
    if (extra) return extra;
    if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
    if (path.startsWith("/api/voice/status")) {
      voiceStatusCalls++;
      return json({ enabled: false, status: "disabled" });
    }
    if (path.startsWith("/api/push/ack")) return json({ ok: true });
    if (path.match(/^\/api\/chat\/[^/]+\/processing/)) return json({ processing: false });
    if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
    if (path.startsWith("/api/conversations?")) {
      return json({ conversations: [], has_more: false });
    }
    const conv = path.match(/^\/api\/conversations\/([^/?]+)$/);
    if (conv) {
      const id = decodeURIComponent(conv[1]);
      conversationGets.push(id);
      if (holdConversation[id]) await holdConversation[id];
      const c = conversations[id];
      if (!c) return json({ detail: "not found" }, 404);
      return json({
        id, title: c.title ?? id, source: "user", channel: "text", notified_user: false,
        created_at: "2026-09-25T10:00:00Z", updated_at: "2026-09-25T10:00:00Z",
        message_count: c.messages.length, messages: c.messages, cc_sessions: [],
      });
    }
    if (path === "/api/chat") {
      let controller!: ReadableStreamDefaultController<Uint8Array>;
      const stream = new ReadableStream<Uint8Array>({ start: (c) => { controller = c; } });
      const enc = new TextEncoder();
      const signal = init?.signal;
      signal?.addEventListener("abort", () => {
        try { controller.error(new DOMException("aborted", "AbortError")); } catch { /* closed */ }
      });
      const raw = init?.body as unknown;
      const form = raw && typeof raw === "object" && typeof (raw as FormData).entries === "function" ? (raw as FormData) : null;
      chatCalls.push({
        // A send with files is multipart; its text fields are what the tests read.
        body: form
          ? (Object.fromEntries(Array.from(form.entries()).filter(([, v]) => typeof v === "string")) as SseHandle["body"])
          : JSON.parse(String(init?.body ?? "{}")),
        send: (e) => controller.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)),
        close: () => controller.close(),
        fail: (err) => controller.error(err ?? new TypeError("network error")),
      });
      return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
    }
    return json({});
  }) as typeof fetch;
}

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));

async function flush(ms = 15) {
  await act(async () => {
    await tick(ms);
  });
}

async function mount() {
  try { window.localStorage.setItem("edward_auth_hint", "1"); } catch { /* none */ }
  render(
    <AppRouterContext.Provider value={router as never}>
      <AuthProvider>
        <ChatProvider>
          <Probe />
        </ChatProvider>
      </AuthProvider>
    </AppRouterContext.Provider>,
  );
  await flush(30);
}

async function send(text: string) {
  await act(async () => {
    void ctx.sendMessage(text);
    await tick(20);
  });
}

async function emit(i: number, ...events: Array<Record<string, unknown>>) {
  await act(async () => {
    for (const e of events) chatCalls[i].send(e);
    await tick(25);
  });
}

function assistant() {
  const m = ctx.messages[ctx.messages.length - 1];
  assert.equal(m.role, "assistant");
  return m;
}

beforeEach(() => {
  conversations = {
    "c-a": { messages: [{ role: "user", content: "hi" }, { role: "assistant", content: "hey" }] },
    "c-b": {
      messages: [
        { role: "user", content: "cabin?" },
        {
          role: "assistant",
          content: "Sunny.",
          trace: {
            reasoning: "**Checking the forecast**\n\nBig Bear.",
            tools: [{ name: "web_search", args_summary: "Big Bear weather", status: "ok", duration_ms: 640, result_preview: "Sunny" }],
            memory_hits: 2,
          },
        },
      ],
    },
  };
  installFetch();
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("the turn", () => {
  it("follows the stream and ends with nothing live", async () => {
    await mount();
    await send("check the calendar");
    assert.equal(chatCalls.length, 1);
    const id = assistant().id;
    assert.equal(ctx.streamingMessageId, id);
    assert.equal(ctx.turn?.id, id);
    assert.equal(ctx.isLoading, true);

    await emit(0, { type: "progress", conversation_id: "c-new", step: "memory_search", status: "started", message: "Searching memory..." });
    assert.equal(ctx.turn?.phase, "recalling");
    assert.equal(ctx.conversationId, "c-new");
    assert.equal(ctx.turn?.conversationId, "c-new");
    assert.equal(ctx.streamingConversationId, "c-new");

    await emit(0,
      { type: "progress", conversation_id: "c-new", step: "memory_search", status: "completed", message: "Found 1 memories", count: 1 },
      { type: "content", conversation_id: "c-new", content: "Let me look." },
      { type: "tool_start", conversation_id: "c-new", tool_name: "web_search" },
    );
    assert.equal(ctx.turn?.phase, "working");
    assert.equal(ctx.turn?.tools.running, 1);

    await emit(0,
      { type: "tool_end", conversation_id: "c-new", tool_name: "web_search", result: "ok" },
      { type: "content", conversation_id: "c-new", content: "Found it." },
    );
    assert.equal(ctx.turn?.phase, "writing");
    assert.equal(assistant().content, "Let me look.\n\nFound it.");

    await act(async () => {
      chatCalls[0].send({ type: "done", conversation_id: "c-new" });
      chatCalls[0].close();
      await tick(30);
    });
    assert.equal(ctx.turn?.phase, "done");
    assert.equal(ctx.turn?.tools.ok, 1);
    assert.equal(ctx.streamingMessageId, null);
    assert.equal(ctx.isLoading, false);
    assert.equal(assistant().isThinking, false);
  });

  it("a failed request mid-turn leaves nothing live and keeps the partial answer", async () => {
    await mount();
    await send("slow one");
    await emit(0,
      { type: "thinking", conversation_id: "c-new", content: "Thinking..." },
      { type: "content", conversation_id: "c-new", content: "Half" },
      { type: "tool_args_delta", conversation_id: "c-new", tool_name: "web_search", call_id: "k", delta: "{" },
      { type: "tool_start", conversation_id: "c-new", tool_name: "browser_open" },
    );
    await act(async () => {
      chatCalls[0].fail();
      await tick(30);
    });
    const m = assistant();
    assert.equal(ctx.turn?.phase, "error");
    assert.equal(ctx.streamingMessageId, null);
    assert.equal(m.isThinking, false);
    assert.equal(m.pendingToolArgs, undefined);
    assert.equal(m.toolActivities![0].status, "stopped");
    assert.equal(m.content, "Half");
    assert.equal(m.wasCutOff, true);
  });

  it("Stop ends the turn as stopped and marks the message interrupted", async () => {
    await mount();
    await send("go");
    await emit(0, { type: "content", conversation_id: "c-new", content: "Part" });
    await act(async () => {
      ctx.stopGeneration();
      await tick(30);
    });
    assert.equal(ctx.turn?.phase, "stopped");
    assert.equal(assistant().wasInterrupted, true);
    assert.equal(ctx.isLoading, false);
    assert.equal(ctx.canStop, false);
  });
});

describe("a stream whose conversation is no longer on screen", () => {
  it("never yanks the screen back, and the live turn returns with its thread", async () => {
    await mount();
    await send("new question");
    const liveId = assistant().id;
    // Ellis opens another thread before the first event names the new chat.
    await act(async () => {
      void ctx.loadConversation("c-a");
      await tick(30);
    });
    assert.equal(ctx.conversationId, "c-a");
    await emit(0,
      { type: "progress", conversation_id: "c-new", step: "memory_search", status: "started", message: "Searching memory..." },
      { type: "content", conversation_id: "c-new", content: "Answer" },
    );
    assert.equal(ctx.conversationId, "c-a", "the stream's first event must not switch the screen");
    assert.deepEqual(ctx.messages.map((m) => m.content), ["hi", "hey"]);
    assert.equal(ctx.isLoading, false, "the composer on another thread is not busy");
    assert.equal(ctx.turn?.phase, "writing", "the turn still tracks Edward's work");
    assert.equal(ctx.streamingConversationId, "c-new");

    // Back to the new chat while it still streams: the live turn is on the end.
    conversations["c-new"] = { messages: [{ role: "user", content: "new question" }] };
    await act(async () => {
      void ctx.loadConversation("c-new");
      await tick(30);
    });
    const last = ctx.messages[ctx.messages.length - 1];
    assert.equal(last.id, liveId);
    assert.equal(last.content, "Answer");
    assert.equal(ctx.messages.filter((m) => m.content === "new question").length, 1);
    assert.equal(ctx.isLoading, true);
    await emit(0, { type: "content", conversation_id: "c-new", content: " more" });
    assert.equal(ctx.messages[ctx.messages.length - 1].content, "Answer more");
  });
});

describe("loading a conversation", () => {
  it("rebuilds the work rail from the history trace", async () => {
    await mount();
    await act(async () => {
      await ctx.loadConversation("c-b");
    });
    const a = ctx.messages[1];
    assert.equal(a.toolActivities?.[0].tool_name, "web_search");
    assert.equal(a.toolActivities?.[0].duration_ms, 640);
    assert.equal(a.reasoningContent, "**Checking the forecast**\n\nBig Bear.");
    assert.equal(a.progressSteps?.[0].count, 2);
  });

  it("renders a recently opened thread from cache at once, then revalidates", async () => {
    await mount();
    await act(async () => { await ctx.loadConversation("c-a"); });
    await act(async () => { await ctx.loadConversation("c-b"); });
    let release!: () => void;
    holdConversation["c-a"] = new Promise<void>((r) => { release = r; });
    conversations["c-a"].messages.push({ role: "user", content: "again" });
    await act(async () => {
      void ctx.loadConversation("c-a");
      await tick(10);
    });
    assert.deepEqual(ctx.messages.map((m) => m.content), ["hi", "hey"], "the cached copy, before the fetch returns");
    assert.equal(ctx.isLoadingConversation, false);
    assert.equal(ctx.isRefreshingConversation, true);
    await act(async () => {
      release();
      await tick(30);
    });
    assert.deepEqual(ctx.messages.map((m) => m.content), ["hi", "hey", "again"]);
    assert.equal(ctx.isRefreshingConversation, false);
  });

  it("a slow load that was superseded never overwrites the thread on screen", async () => {
    await mount();
    let release!: () => void;
    holdConversation["c-a"] = new Promise<void>((r) => { release = r; });
    await act(async () => {
      void ctx.loadConversation("c-a");
      await tick(5);
      void ctx.loadConversation("c-b");
      await tick(30);
    });
    await act(async () => {
      release();
      await tick(30);
    });
    assert.equal(ctx.conversationId, "c-b");
    assert.deepEqual(ctx.messages.map((m) => m.content), ["cabin?", "Sunny."]);
  });

  it("a send while the thread is still loading waits for it", async () => {
    await mount();
    let release!: () => void;
    holdConversation["c-a"] = new Promise<void>((r) => { release = r; });
    await act(async () => {
      void ctx.loadConversation("c-a");
      await tick(5);
    });
    assert.equal(ctx.isLoadingConversation, true);
    await send("typed too early");
    assert.equal(chatCalls.length, 0, "no request while history loads");
    assert.equal(ctx.queuedMessages.length, 1);
    await act(async () => {
      release();
      await tick(40);
    });
    assert.equal(chatCalls.length, 1);
    assert.equal(chatCalls[0].body.message, "typed too early");
    assert.equal(chatCalls[0].body.conversation_id, "c-a");
    assert.deepEqual(ctx.messages.slice(0, 2).map((m) => m.content), ["hi", "hey"]);
  });
});

// ---------------------------------------------------------------------------
// 2026-09-26 review fixes.
// ---------------------------------------------------------------------------

const convRow = (id: string, title = id) => ({
  id, title, source: "user", channel: "text", notified_user: false,
  created_at: "2026-09-25T10:00:00Z", updated_at: "2026-09-25T10:00:00Z", message_count: 2,
});

async function finish(i: number, conversationId: string) {
  await act(async () => {
    chatCalls[i].send({ type: "done", conversation_id: conversationId });
    chatCalls[i].close();
    await tick(40);
  });
}

describe("a queued message that sends itself", () => {
  it("is not Ellis's send: the transcript is not pinned to the bottom for it", async () => {
    await mount();
    await send("long answer about the lake trails");
    const seq = ctx.userSendSeq;
    assert.equal(typeof seq, "number", "Ellis's own sends are counted");
    assert.ok(seq >= 1);
    await act(async () => {
      ctx.queueMessage("quick thanks");
      await tick(5);
    });
    await finish(0, "c-new");
    assert.equal(chatCalls.length, 2, "the queue drained");
    assert.equal(chatCalls[1].body.message, "quick thanks");
    assert.equal(ctx.userSendSeq, seq, "a drain is not a send Ellis just made");
    await send("and another");
    assert.equal(ctx.userSendSeq, seq, "queued behind the drained turn: not sent yet either");
  });
});

describe("Stop with messages queued", () => {
  it("stops: the queue waits until Ellis sends it on", async () => {
    await mount();
    await send("go");
    await emit(0, { type: "content", conversation_id: "c-new", content: "Part" });
    await act(async () => {
      ctx.queueMessage("next thing");
      await tick(5);
    });
    await act(async () => {
      ctx.stopGeneration();
      await tick(40);
    });
    assert.equal(chatCalls.length, 1, "Stop means stop");
    assert.equal(ctx.queuedMessages.length, 1, "the chip stays");
    assert.equal(ctx.queuePaused, true);
    await act(async () => {
      ctx.resumeQueue();
      await tick(40);
    });
    assert.equal(chatCalls.length, 2);
    assert.equal(chatCalls[1].body.message, "next thing");
    assert.equal(ctx.queuePaused, false);
  });
});

describe("deleting a conversation", () => {
  it("takes it out of the search results too", async () => {
    await mount();
    extraRoute = (path, init) => {
      if (init?.method === "DELETE") return json({ status: "deleted", id: "c-z" });
      if (path.startsWith("/api/conversations?") && path.includes("zebra")) return json({ conversations: [convRow("c-z", "quick zebra")], has_more: false });
      return undefined;
    };
    await act(async () => {
      ctx.setSearchQuery("zebra");
      await tick(400);
    });
    assert.equal(ctx.searchResults?.length, 1);
    await act(async () => {
      await ctx.deleteConversation("c-z");
    });
    assert.deepEqual(ctx.searchResults, []);
  });

  it("a failed delete rejects (so Undo's toast can say so) and the row comes back", async () => {
    extraRoute = (path, init) => {
      if (init?.method === "DELETE") return json({ detail: "boom" }, 500);
      if (path.startsWith("/api/conversations?")) return json({ conversations: [convRow("c-a")], has_more: false });
      return undefined;
    };
    await mount();
    assert.deepEqual(ctx.conversations.map((c) => c.id), ["c-a"]);
    let failed = false;
    await act(async () => {
      await ctx.deleteConversation("c-a").catch(() => (failed = true));
      await tick(30);
    });
    assert.equal(failed, true);
    assert.deepEqual(ctx.conversations.map((c) => c.id), ["c-a"], "the server still has it");
  });
});

describe("a stream cut while the app is visible", () => {
  it("holds the queue until the cut turn has been recovered", async () => {
    let releaseProc!: () => void;
    const held = new Promise<void>((r) => (releaseProc = r));
    extraRoute = (path) => {
      if (/^\/api\/chat\/c-new\/processing/.test(path)) return held.then(() => json({ processing: false }));
      return undefined;
    };
    await mount();
    await send("first");
    await emit(0, { type: "content", conversation_id: "c-new", content: "Half" });
    await act(async () => {
      ctx.queueMessage("second");
      await tick(5);
    });
    conversations["c-new"] = { messages: [{ role: "user", content: "first" }, { role: "assistant", content: "Half and the rest" }] };
    await act(async () => {
      chatCalls[0].close(); // closed without `done`
      await tick(40);
    });
    assert.equal(chatCalls.length, 1, "no second writer while the server may still be finishing");
    await act(async () => {
      releaseProc();
      await tick(80);
    });
    assert.equal(chatCalls.length, 2, "sent once the thread is recovered");
    assert.equal(chatCalls[1].body.message, "second");
  });
});

describe("a cached thread refreshing while a turn finishes in it", () => {
  it("keeps the finished exchange: the older server copy never erases it", async () => {
    await mount();
    await act(async () => { await ctx.loadConversation("c-a"); });
    await act(async () => { await ctx.loadConversation("c-b"); });
    let release!: () => void;
    holdConversation["c-a"] = new Promise<void>((r) => { release = r; });
    await act(async () => {
      void ctx.loadConversation("c-a");
      await tick(10);
    });
    assert.equal(ctx.isRefreshingConversation, true);
    await send("quick");
    assert.equal(chatCalls.length, 1);
    await emit(0, { type: "content", conversation_id: "c-a", content: "Sure." });
    await finish(0, "c-a");
    await act(async () => {
      release(); // the copy fetched before the turn: ["hi", "hey"]
      await tick(30);
    });
    assert.deepEqual(ctx.messages.map((m) => m.content), ["hi", "hey", "quick", "Sure."]);
  });
});

describe("voice status", () => {
  it("is checked on open and when the app comes back — not polled in a hidden tab", async () => {
    await mount();
    await flush(600);
    const first = voiceStatusCalls;
    assert.equal(first, 1);
    await act(async () => {
      document.dispatchEvent(new window.Event("visibilitychange"));
      await tick(20);
    });
    assert.equal(voiceStatusCalls, 2);
  });
});

describe("photos Ellis sent", () => {
  it("release their preview once nothing on screen or in the cache shows it", async () => {
    const created: string[] = [];
    const revoked: string[] = [];
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    URL.createObjectURL = () => {
      const u = `blob:test/${created.length + 1}`;
      created.push(u);
      return u;
    };
    URL.revokeObjectURL = (u: string) => void revoked.push(u);
    try {
      await mount();
      const photo = new File([new Uint8Array(8)], "dock.jpg", { type: "image/jpeg" });
      await act(async () => {
        void ctx.sendMessage("look at this", [photo]);
        await tick(20);
      });
      assert.equal(created.length, 1);
      await emit(0, { type: "content", conversation_id: "c-new", content: "Nice dock." });
      await finish(0, "c-new");
      conversations["c-new"] = { messages: [{ role: "user", content: "look at this" }, { role: "assistant", content: "Nice dock." }] };
      await act(async () => { await ctx.loadConversation("c-a"); });
      assert.deepEqual(revoked, [], "the cached copy of the thread still shows it");
      await act(async () => { await ctx.loadConversation("c-new"); });
      assert.deepEqual(revoked, [created[0]], "the server's copy replaced it everywhere");
    } finally {
      URL.createObjectURL = origCreate;
      URL.revokeObjectURL = origRevoke;
    }
  });
});
