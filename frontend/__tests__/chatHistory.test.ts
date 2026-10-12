// Loading a conversation: the server's messages → the chat's Message list.
//
//  - The work rail survives a reload: an assistant message may carry a
//    compact `trace` ({reasoning, tools[], memory_hits}); it hydrates the
//    reasoning, the tool activities, the memory step and message.trace.
//    Old turns have no trace and stay plain.
//  - Sidebar taps render a cached copy at once (LRU of 8) while the server
//    copy is fetched — no full-screen spinner for a thread seen recently.
//  - A turn still streaming for the conversation being opened is put back
//    on the end of it (the server does not have it yet, or has half of it).

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  hydrateHistoryTrace,
  mapHistoryMessages,
  withLiveTurn,
  ConversationCache,
  type HistoryTrace,
} from "../lib/chatHistory.ts";
import type { Message } from "../lib/ChatContext.tsx";
import type { ConversationWithMessages } from "../lib/api.ts";

const TRACE: HistoryTrace = {
  reasoning: "**Checking the forecast**\n\nEllis asked about the cabin weekend.",
  tools: [
    { name: "web_search", args_summary: "Big Bear forecast", status: "ok", duration_ms: 812, result_preview: "Sunny, 64°F" },
    { name: "list_scheduled_events", args_summary: null, status: "error", duration_ms: null, result_preview: "Tool error: timeout" },
  ],
  memory_hits: 4,
};

describe("hydrateHistoryTrace", () => {
  it("rebuilds the rail from a stored trace", () => {
    const h = hydrateHistoryTrace("c1-3", TRACE, "It'll be sunny.");
    assert.equal(h.reasoningContent, TRACE.reasoning);
    assert.equal(h.toolActivities!.length, 2);
    const [a, b] = h.toolActivities!;
    assert.equal(a.id, "c1-3-tool-0");
    assert.equal(a.tool_name, "web_search");
    assert.equal(a.status, "completed");
    assert.equal(a.args_summary, "Big Bear forecast");
    assert.equal(a.result, "Sunny, 64°F");
    assert.equal(a.duration_ms, 812);
    assert.equal(b.status, "error");
    assert.equal(b.args_summary, undefined);
    assert.equal(b.duration_ms, undefined);
    const mem = h.progressSteps!.find((s) => s.step === "memory_search")!;
    assert.equal(mem.status, "completed");
    assert.equal(mem.count, 4);
    assert.deepEqual(
      h.trace!.map((t) => t.kind),
      ["memory", "reasoning", "tool", "tool", "text"],
    );
  });

  it("a trace with zero memory hits still shows the search", () => {
    const h = hydrateHistoryTrace("m", { reasoning: null, tools: [], memory_hits: 0 }, "hi");
    assert.equal(h.progressSteps![0].count, 0);
    assert.equal(h.progressSteps![0].message, "No relevant memories");
  });

  it("no trace, or an empty one, adds nothing", () => {
    assert.deepEqual(hydrateHistoryTrace("m", undefined, "x"), {});
    assert.deepEqual(hydrateHistoryTrace("m", null, "x"), {});
    assert.deepEqual(hydrateHistoryTrace("m", { reasoning: null, tools: [], memory_hits: null }, "x"), {});
  });
});

describe("mapHistoryMessages", () => {
  const data = {
    id: "c1",
    title: "Cabin",
    source: "user",
    channel: "text",
    notified_user: false,
    created_at: "2026-09-20T10:00:00Z",
    updated_at: "2026-09-20T10:05:00Z",
    message_count: 4,
    messages: [
      { role: "user", content: "Weather at the cabin?", attachments: [{ file_id: "f1", filename: "map.png", mime_type: "image/png", size: 10 }] },
      { role: "assistant", content: "It'll be sunny.", trace: TRACE },
      { role: "user", content: "[Scheduled]", is_trigger: true, trigger_type: "scheduled_event" },
      { role: "assistant", content: "Reminder sent." },
    ],
    cc_sessions: [{ task_id: "t1", description: "fix it", status: "completed", result_summary: "done", error: null }],
  } as unknown as ConversationWithMessages;

  it("maps ids, attachments, triggers and traces", () => {
    const m = mapHistoryMessages("c1", data);
    assert.deepEqual(m.map((x) => x.id), ["c1-0", "c1-1", "c1-2", "c1-3"]);
    assert.equal(m[0].attachments![0].file_id, "f1");
    assert.equal(m[0].attachments![0].id, "c1-0-att-0");
    assert.equal(m[1].toolActivities!.length, 2);
    assert.equal(m[1].reasoningContent, TRACE.reasoning);
    assert.equal(m[2].isTrigger, true);
    assert.equal(m[2].triggerType, "scheduled_event");
    assert.equal(m[3].toolActivities, undefined, "an old turn without a trace stays plain");
  });

  it("puts Claude Code sessions on the last assistant message", () => {
    const m = mapHistoryMessages("c1", data);
    assert.equal(m[3].ccSessions![0].id, "t1");
    assert.equal(m[3].ccSessions![0].status, "completed");
    assert.equal(m[1].ccSessions, undefined);
  });
});

describe("withLiveTurn", () => {
  const user: Message = { id: "u-live", role: "user", content: "What's next?" };
  const assistant: Message = { id: "a-live", role: "assistant", content: "Look" };
  const server: Message[] = [
    { id: "c1-0", role: "user", content: "hi" },
    { id: "c1-1", role: "assistant", content: "hey" },
  ];

  it("no live turn: the server's list as it is", () => {
    assert.equal(withLiveTurn(server, null), server);
  });

  it("appends a turn the server has not saved yet", () => {
    assert.deepEqual(withLiveTurn(server, { user, assistant }).map((m) => m.id), ["c1-0", "c1-1", "u-live", "a-live"]);
  });

  it("replaces the server's copy of the same turn (saved question, half an answer)", () => {
    const withCopy = [...server, { id: "c1-2", role: "user" as const, content: "What's next?" }, { id: "c1-3", role: "assistant" as const, content: "Lo" }];
    assert.deepEqual(withLiveTurn(withCopy, { user, assistant, baseUserCount: 1 }).map((m) => m.id), ["c1-0", "c1-1", "u-live", "a-live"]);
    const questionOnly = [...server, { id: "c1-2", role: "user" as const, content: "What's next?" }];
    assert.deepEqual(withLiveTurn(questionOnly, { user, assistant, baseUserCount: 1 }).map((m) => m.id), ["c1-0", "c1-1", "u-live", "a-live"]);
  });

  // 2026-09-26 review: Ellis said "continue", Edward finished step one, Ellis
  // said "continue" again. Mid-turn the server copy still ends with the
  // *previous* "continue" / "Done step one." (the checkpoint is written at
  // the end of the turn), and a text match stripped it as if it were the
  // live question — the earlier exchange vanished from the transcript.
  describe("a repeated question ('continue', 'yes')", () => {
    const again: Message = { id: "u-live", role: "user", content: "continue" };
    const prior: Message[] = [
      { id: "c-0", role: "user", content: "hi" },
      { id: "c-1", role: "assistant", content: "hey" },
      { id: "c-2", role: "user", content: "continue" },
      { id: "c-3", role: "assistant", content: "Done step one." },
    ];
    const live = { user: again, assistant, baseUserCount: 2 };

    it("the server's copy from before this turn keeps the earlier exchange", () => {
      assert.deepEqual(withLiveTurn(prior, live).map((m) => m.id), ["c-0", "c-1", "c-2", "c-3", "u-live", "a-live"]);
    });

    it("so does a cached snapshot that already holds the live pair", () => {
      const cached = [...prior, again, { ...assistant, content: "L" }];
      assert.deepEqual(withLiveTurn(cached, live).map((m) => m.id), ["c-0", "c-1", "c-2", "c-3", "u-live", "a-live"]);
    });

    it("once the server has saved this turn, its copy is replaced", () => {
      const saved = [...prior, { id: "c-4", role: "user" as const, content: "continue" }, { id: "c-5", role: "assistant" as const, content: "Done step tw" }];
      assert.deepEqual(withLiveTurn(saved, live).map((m) => m.id), ["c-0", "c-1", "c-2", "c-3", "u-live", "a-live"]);
    });
  });

  it("drops a stale copy of the live messages from a cached snapshot", () => {
    const cached = [...server, user, { ...assistant, content: "L" }];
    const out = withLiveTurn(cached, { user, assistant });
    assert.deepEqual(out.map((m) => m.id), ["c1-0", "c1-1", "u-live", "a-live"]);
    assert.equal(out[3].content, "Look");
  });
});

describe("ConversationCache", () => {
  const entry = (n: string) => ({ conversation: null, messages: [{ id: n, role: "user" as const, content: n }] });

  it("keeps the most recent eight", () => {
    const c = new ConversationCache(8);
    for (let i = 0; i < 10; i++) c.set(`c${i}`, entry(`m${i}`));
    assert.equal(c.get("c0"), undefined);
    assert.equal(c.get("c1"), undefined);
    assert.equal(c.get("c9")!.messages[0].id, "m9");
    assert.equal(c.size, 8);
  });

  it("a read makes an entry recent", () => {
    const c = new ConversationCache(2);
    c.set("a", entry("a"));
    c.set("b", entry("b"));
    c.get("a");
    c.set("c", entry("c"));
    assert.ok(c.get("a"));
    assert.equal(c.get("b"), undefined);
  });

  it("patch edits an entry in place of a refetch; missing ids are ignored", () => {
    const c = new ConversationCache();
    c.set("a", entry("a"));
    c.patch("a", (e) => ({ ...e, messages: [...e.messages, { id: "x", role: "assistant", content: "done" }] }));
    assert.equal(c.get("a")!.messages.length, 2);
    c.patch("zzz", (e) => e);
    assert.equal(c.get("zzz"), undefined);
  });

  it("delete forgets a conversation", () => {
    const c = new ConversationCache();
    c.set("a", entry("a"));
    c.delete("a");
    assert.equal(c.get("a"), undefined);
  });
});

// 2026-09-26 review: every photo or memo Ellis sends gets an object URL for its
// preview, and none was ever revoked — the PWA stays open for days, and each
// multi-MB File stayed alive in the blob registry. What still references
// one decides when it goes.
import { referencedBlobUrls } from "../lib/chatHistory.ts";

describe("referencedBlobUrls", () => {
  it("collects the blob previews still on screen or in the cache", () => {
    const withBlob: Message = { id: "u1", role: "user", content: "", attachments: [{ id: "a", filename: "p.jpg", mime_type: "image/jpeg", size: 1, preview_url: "blob:http://x/1" }] };
    const served: Message = { id: "u2", role: "user", content: "", attachments: [{ id: "b", file_id: "f", filename: "p.jpg", mime_type: "image/jpeg", size: 1 }] };
    const set = referencedBlobUrls([[withBlob], [served], []]);
    assert.deepEqual([...set], ["blob:http://x/1"]);
  });
});
