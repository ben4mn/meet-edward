// R/G red for "Where this came from" in the Memory room (plan Phase 5).
//
// The backend returns only stored records; the panel turns them into a
// few labelled lines and never invents a source that is not there.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { provenanceRows, type MemoryProvenance } from "../lib/provenance.ts";

function record(over: Partial<MemoryProvenance> = {}): MemoryProvenance {
  return {
    memory: {
      id: "m1",
      content: "Theo is moving to Denver",
      origin: "read",
      about: "contact",
      source_type: "tool_derived",
      created_at: "2026-09-12T14:03:00Z",
      source_conversation_id: "c-theo",
    },
    conversation: { id: "c-theo", title: "Theo", source: "heartbeat", channel: "text" },
    chat: { chat_identifier: "+15551230000", chat_name: "Theo", is_group: false },
    contact: null,
    fold: null,
    source_ref: { kind: "extraction", profile: "heartbeat", turn_source: "heartbeat", event_ids: ["e1", "e2"] },
    summary: "you read it in “Theo”",
    ...over,
  };
}

describe("provenanceRows", () => {
  it("labels what the records hold, with a link to the thread", () => {
    const rows = provenanceRows(record());
    const by = Object.fromEntries(rows.map((r) => [r.label, r]));
    assert.match(by["How"].value, /read/);
    assert.equal(by["Conversation"].value, "Theo (messages)");
    assert.equal(by["Conversation"].href, "/chat?c=c-theo");
    assert.equal(by["iMessage"].value, "Theo · +15551230000");
    assert.equal(by["Kept"].value, "I noted it after reading messages");
    assert.equal(by["Record ids"].value, "e1, e2");
  });

  it("speaks plainly about how it was kept (no internal vocabulary)", () => {
    const chat = provenanceRows(record({
      conversation: { id: "c1", title: "Weekend", source: "user", channel: null },
      source_ref: { kind: "extraction", turn_source: "chat" },
    }));
    const by = Object.fromEntries(chat.map((r) => [r.label, r.value]));
    assert.equal(by["Conversation"], "Weekend");
    assert.equal(by["Kept"], "I noted it after a chat");
    const mine = provenanceRows(record({ source_ref: { kind: "remember", turn_source: "edward_thought" } }));
    assert.equal(mine.find((r) => r.label === "Kept")?.value, "I wrote it down myself, during a wake");
    for (const r of [...chat, ...mine]) assert.doesNotMatch(`${r.label} ${r.value}`, /extraction|heartbeat|turn\b|chat\.db/i);
  });

  it("says a row from the household hub came from a voice chat there", () => {
    const rows = provenanceRows(record({ source_ref: { kind: "extraction", turn_source: "hub_voice" } }));
    assert.equal(rows.find((r) => r.label === "Kept")?.value, "I noted it after a voice chat at the hub");
  });

  it("says a thread is gone rather than inventing one", () => {
    const rows = provenanceRows(record({ conversation: null, chat: null, source_ref: {} }));
    const conv = rows.find((r) => r.label === "Conversation");
    assert.equal(conv?.value, "c-theo (no longer exists)");
    assert.equal(conv?.href, undefined);
  });

  it("names a folded row", () => {
    const rows = provenanceRows(record({
      conversation: null, chat: null, source_ref: {}, fold: ["preferences", "17"],
      memory: { ...record().memory, source_conversation_id: "fold:preferences:17" },
    }));
    assert.equal(rows.find((r) => r.label === "Conversation")?.value, "folded from the old preferences table (row 17)");
  });

  it("says not recorded when there is nothing", () => {
    const rows = provenanceRows(record({
      conversation: null, chat: null, source_ref: {},
      memory: { ...record().memory, source_conversation_id: null },
    }));
    assert.equal(rows.find((r) => r.label === "Conversation")?.value, "not recorded");
  });
});
