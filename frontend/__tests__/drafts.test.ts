// R/G red: per-conversation composer drafts (lib/drafts.ts).
//
// What Ellis types in one thread stays with that thread: switch to another
// and back, and the half-written message is still there. sessionStorage,
// keyed like the composer queue (a brand-new chat is "__new__"), so a
// reload keeps it but a new app session starts clean.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { DRAFT_PREFIX, MAX_DRAFT_CHARS, draftKey, readDraft, writeDraft, clearDraft, type DraftStorage } from "../lib/drafts.ts";

function memory(): DraftStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k) => (data.has(k) ? data.get(k)! : null),
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
  };
}

const broken: DraftStorage = {
  getItem: () => {
    throw new Error("SecurityError");
  },
  setItem: () => {
    throw new Error("QuotaExceededError");
  },
  removeItem: () => {
    throw new Error("SecurityError");
  },
};

describe("draftKey", () => {
  it("keys a conversation by its id", () => {
    assert.equal(draftKey("c-cabin-weekend"), `${DRAFT_PREFIX}c-cabin-weekend`);
  });
  it("a new chat (no id yet) shares the queue's new-chat key", () => {
    assert.equal(draftKey(undefined), `${DRAFT_PREFIX}__new__`);
    assert.equal(draftKey(null), `${DRAFT_PREFIX}__new__`);
    assert.equal(draftKey(""), `${DRAFT_PREFIX}__new__`);
  });
});

describe("readDraft / writeDraft", () => {
  it("round-trips a draft per conversation", () => {
    const s = memory();
    writeDraft("a", "half a thought", s);
    writeDraft("b", "another", s);
    assert.equal(readDraft("a", s), "half a thought");
    assert.equal(readDraft("b", s), "another");
    assert.equal(readDraft("c", s), "");
  });

  it("keeps the text exactly, newlines and trailing space included", () => {
    const s = memory();
    writeDraft("a", "line one\nline two ", s);
    assert.equal(readDraft("a", s), "line one\nline two ");
  });

  it("an empty or whitespace-only draft removes the entry", () => {
    const s = memory();
    writeDraft("a", "x", s);
    writeDraft("a", "   \n", s);
    assert.equal(s.data.size, 0);
    assert.equal(readDraft("a", s), "");
  });

  it("clearDraft forgets it (after a send)", () => {
    const s = memory();
    writeDraft(undefined, "new chat text", s);
    clearDraft(undefined, s);
    assert.equal(readDraft(undefined, s), "");
  });

  it("caps an enormous paste rather than failing on quota", () => {
    const s = memory();
    writeDraft("a", "x".repeat(MAX_DRAFT_CHARS + 50), s);
    assert.equal(readDraft("a", s).length, MAX_DRAFT_CHARS);
  });

  it("a storage that throws (private mode, blocked site data) is harmless", () => {
    assert.doesNotThrow(() => writeDraft("a", "x", broken));
    assert.doesNotThrow(() => clearDraft("a", broken));
    assert.equal(readDraft("a", broken), "");
  });

  it("no storage at all (server render) reads empty and writes nothing", () => {
    assert.equal(readDraft("a", null), "");
    assert.doesNotThrow(() => writeDraft("a", "x", null));
  });
});

// When the conversation on screen changes, the composer either moves to
// another thread's draft ("switch") or — a brand-new chat learning its id
// from the first stream event — keeps what Ellis is typing and files it
// under the new id ("rekey"). Treating the second as a switch would wipe
// the box mid-sentence.
import { draftTransition } from "../lib/drafts.ts";

describe("draftTransition", () => {
  // The 4th argument is the stream's id as the composer last saw it: a
  // rekey is only the id being *assigned* (null → next in the same render).
  it("a new chat gaining the id its own turn is streaming into: rekey", () => {
    assert.equal(draftTransition(undefined, "c-new", "c-new", null), "rekey");
  });
  it("a new chat opening a thread that was already streaming: switch, never a rekey (2026-09-26 review)", () => {
    // Ellis asks in X, taps New chat, types half a question, taps X again:
    // the new-chat text must not move into X's box (nor overwrite X's draft).
    assert.equal(draftTransition(undefined, "X", "X", "X"), "switch");
    // the same when X itself began as a new chat and has had its id a while
    assert.equal(draftTransition(undefined, "c-new", "c-new", "c-new"), "switch");
  });
  it("leaving a new chat for an existing conversation: switch", () => {
    assert.equal(draftTransition(undefined, "c-old", null, null), "switch");
    assert.equal(draftTransition(undefined, "c-old", "c-other", "c-other"), "switch");
  });
  it("between two conversations, or back to a new chat: switch", () => {
    assert.equal(draftTransition("a", "b", "b", null), "switch");
    assert.equal(draftTransition("a", undefined, null, null), "switch");
  });
  it("no change: none", () => {
    assert.equal(draftTransition("a", "a", null, null), "none");
    assert.equal(draftTransition(undefined, undefined, null, null), "none");
  });
});
