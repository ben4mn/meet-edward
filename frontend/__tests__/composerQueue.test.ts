// R/G red: messages Ellis types while Edward is still working.
//
// Plan Phase 5, "Queue while running" (2026-09-24): the composer used to
// show Stop while a turn streamed and refuse anything typed. Muse lets you
// line up the next task, so the composer queues it: a chip above the box
// with a remove (x), sent automatically in the same conversation when the
// current turn ends — done, stopped or errored — one at a time.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  NEW_CHAT_KEY,
  clearQueue,
  dequeue,
  enqueue,
  nextOnDone,
  queueChipLabel,
  queueKey,
  queuedFor,
  rekeyQueue,
  removeQueued,
  type ComposerQueues,
  type DrainGate,
} from "../lib/composerQueue.ts";

const idle: DrainGate = { isLoading: false, isLoadingConversation: false, interrupted: false, voiceMode: false };
const empty: ComposerQueues<string> = {};

function q(id: string, text: string, files?: string[]) {
  return files ? { id, text, files } : { id, text };
}

describe("queueKey", () => {
  it("a conversation queues under its id", () => {
    assert.equal(queueKey("c1"), "c1");
  });

  it("a brand-new chat has no id yet and queues under the new-chat key", () => {
    assert.equal(queueKey(undefined), NEW_CHAT_KEY);
    assert.equal(queueKey(null), NEW_CHAT_KEY);
    assert.equal(queueKey(""), NEW_CHAT_KEY);
  });
});

describe("enqueue", () => {
  it("adds to the end of that conversation's queue", () => {
    let s = enqueue(empty, "c1", q("a", "first")).queues;
    s = enqueue(s, "c1", q("b", "second")).queues;
    assert.deepEqual(queuedFor(s, "c1").map((m) => m.text), ["first", "second"]);
  });

  it("queues are per conversation", () => {
    let s = enqueue(empty, "c1", q("a", "for one")).queues;
    s = enqueue(s, "c2", q("b", "for two")).queues;
    assert.deepEqual(queuedFor(s, "c1").map((m) => m.id), ["a"]);
    assert.deepEqual(queuedFor(s, "c2").map((m) => m.id), ["b"]);
  });

  it("an identical message just queued is not queued twice (a double Enter)", () => {
    const first = enqueue(empty, "c1", q("a", "and book the table"));
    const again = enqueue(first.queues, "c1", q("b", "  and book the table \n"));
    assert.equal(first.added, true);
    assert.equal(again.added, false);
    assert.equal(again.queues, first.queues);
    assert.equal(queuedFor(again.queues, "c1").length, 1);
  });

  it("the same words later in the queue are fine; only the last one is compared", () => {
    let s = enqueue(empty, "c1", q("a", "yes")).queues;
    s = enqueue(s, "c1", q("b", "no")).queues;
    const r = enqueue(s, "c1", q("c", "yes"));
    assert.equal(r.added, true);
  });

  it("a message with attachments is never a duplicate", () => {
    const first = enqueue(empty, "c1", q("a", "look", ["photo.jpg"]));
    const again = enqueue(first.queues, "c1", q("b", "look", ["photo.jpg"]));
    assert.equal(again.added, true);
  });

  it("nothing to send is not queued", () => {
    const r = enqueue(empty, "c1", q("a", "   "));
    assert.equal(r.added, false);
    assert.deepEqual(queuedFor(r.queues, "c1"), []);
  });

  it("an attachment alone is queued", () => {
    const r = enqueue(empty, "c1", q("a", "", ["memo.m4a"]));
    assert.equal(r.added, true);
  });

  it("never mutates the state it was given", () => {
    const s = enqueue(empty, "c1", q("a", "one")).queues;
    const before = JSON.stringify(s);
    enqueue(s, "c1", q("b", "two"));
    assert.equal(JSON.stringify(s), before);
  });
});

describe("removeQueued / dequeue / clearQueue", () => {
  it("the x removes exactly that chip", () => {
    let s = enqueue(empty, "c1", q("a", "one")).queues;
    s = enqueue(s, "c1", q("b", "two")).queues;
    s = removeQueued(s, "c1", "a");
    assert.deepEqual(queuedFor(s, "c1").map((m) => m.id), ["b"]);
  });

  it("removing the last chip leaves no empty queue behind", () => {
    const s = removeQueued(enqueue(empty, "c1", q("a", "one")).queues, "c1", "a");
    assert.deepEqual(Object.keys(s), []);
  });

  it("removing an unknown id changes nothing", () => {
    const s = enqueue(empty, "c1", q("a", "one")).queues;
    assert.equal(removeQueued(s, "c1", "zzz"), s);
    assert.equal(removeQueued(s, "c9", "a"), s);
  });

  it("dequeue takes the oldest first", () => {
    let s = enqueue(empty, "c1", q("a", "one")).queues;
    s = enqueue(s, "c1", q("b", "two")).queues;
    const r = dequeue(s, "c1");
    assert.equal(r.item?.id, "a");
    assert.deepEqual(queuedFor(r.queues, "c1").map((m) => m.id), ["b"]);
  });

  it("dequeue on an empty queue returns nothing", () => {
    const r = dequeue(empty, "c1");
    assert.equal(r.item, null);
    assert.equal(r.queues, empty);
  });

  it("clearQueue drops one conversation's queue", () => {
    let s = enqueue(empty, "c1", q("a", "one")).queues;
    s = enqueue(s, "c2", q("b", "two")).queues;
    s = clearQueue(s, "c1");
    assert.deepEqual(Object.keys(s), ["c2"]);
  });
});

describe("rekeyQueue", () => {
  it("a new chat's queue follows it onto its real id", () => {
    const s = rekeyQueue(enqueue(empty, NEW_CHAT_KEY, q("a", "and then")).queues, NEW_CHAT_KEY, "c7");
    assert.deepEqual(queuedFor(s, "c7").map((m) => m.id), ["a"]);
    assert.deepEqual(queuedFor(s, NEW_CHAT_KEY), []);
  });

  it("appends after anything already queued under the real id", () => {
    let s = enqueue(empty, "c7", q("x", "earlier")).queues;
    s = enqueue(s, NEW_CHAT_KEY, q("a", "later")).queues;
    s = rekeyQueue(s, NEW_CHAT_KEY, "c7");
    assert.deepEqual(queuedFor(s, "c7").map((m) => m.id), ["x", "a"]);
  });

  it("nothing to move is a no-op", () => {
    assert.equal(rekeyQueue(empty, NEW_CHAT_KEY, "c7"), empty);
  });
});

describe("nextOnDone", () => {
  const one = enqueue(empty, "c1", q("a", "next please")).queues;

  it("when the turn is over the next queued message goes", () => {
    const r = nextOnDone(one, "c1", idle);
    assert.equal(r.item?.text, "next please");
    assert.deepEqual(queuedFor(r.queues, "c1"), []);
  });

  it("one at a time: nothing goes while a turn is streaming", () => {
    const r = nextOnDone(one, "c1", { ...idle, isLoading: true });
    assert.equal(r.item, null);
    assert.equal(r.queues, one);
  });

  it("waits while the conversation is still loading (history first, then the new message)", () => {
    assert.equal(nextOnDone(one, "c1", { ...idle, isLoadingConversation: true }).item, null);
  });

  it("holds while a stream cut by a hidden tab is being recovered — the server may still be working", () => {
    assert.equal(nextOnDone(one, "c1", { ...idle, interrupted: true }).item, null);
  });

  it("holds during live voice, which runs its own turns in the same conversation", () => {
    assert.equal(nextOnDone(one, "c1", { ...idle, voiceMode: true }).item, null);
  });

  it("only the conversation on screen drains", () => {
    assert.equal(nextOnDone(one, "c2", idle).item, null);
  });

  // 2026-09-26 review: Stop with messages queued sent the first one at once,
  // so Edward never stopped. A turn Ellis stopped pauses the queue; the chips
  // stay and go when he says so.
  it("holds while Ellis's Stop has paused the queue", () => {
    assert.equal(nextOnDone(one, "c1", { ...idle, paused: true }).item, null);
    assert.equal(nextOnDone(one, "c1", { ...idle, paused: false }).item?.text, "next please");
  });
});

describe("queueChipLabel", () => {
  it("shows the text, flattened to one line", () => {
    assert.equal(queueChipLabel(q("a", "  also\n  book the table ")), "also book the table");
  });

  it("an attachment-only message says what it carries", () => {
    assert.equal(queueChipLabel(q("a", "", ["one.jpg"])), "1 attachment");
    assert.equal(queueChipLabel(q("a", "", ["one.jpg", "two.pdf"])), "2 attachments");
  });

  it("text with attachments notes them", () => {
    assert.equal(queueChipLabel(q("a", "look", ["one.jpg"])), "look (+1 attachment)");
  });
});
