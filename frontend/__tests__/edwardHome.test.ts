// R/G red for the Edward tab's landing ("what he knows, watches and does").
//
// Everything the landing says is decided here, from real signals only:
//  - the first two sentences of his own "Edward: state" page (his voice);
//  - the three answers: Knows (memories), Watches (intentions + what waits
//    on Ellis), Does (his last wake, and where to read his thinking);
//  - one honest live line and a health tone per room, used by the rooms
//    list and the desktop rail alike. Mind is about his wakes and open
//    intentions — never "model · temp 0.7".

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  STATE_DOC_TITLE,
  pickStateDocument,
  stateExcerpt,
  knowsLine,
  watchesLine,
  doesAnswer,
  roomLines,
  type RoomLineInput,
} from "../components/settings/edwardHomeModel.ts";
import type { MindNow, MindWakeRow } from "../lib/mindNowApi.ts";

const NOW = new Date("2026-09-26T18:00:00Z");

function row(p: Partial<MindWakeRow>): MindWakeRow {
  return { at: "2026-09-26T17:48:00Z", reason: "messages", outcome: "left_alone", line: null, thought_id: null, ...p };
}

function mind(p: Partial<MindNow> = {}): MindNow {
  const recent = p.recent ?? [];
  return {
    now: NOW.toISOString(),
    waking: null,
    last: recent[0] ?? null,
    recent,
    next_wake_at: null,
    approvals_waiting: 0,
    unread_thoughts: 0,
    quota: { used_percent: 40, exhausted: false, resets_at: null },
    hands: { connected: true, down_for_s: null },
    ...p,
  };
}

describe("pickStateDocument", () => {
  it("takes only the document titled exactly 'Edward: state'", () => {
    const docs = [
      { id: "a", title: "Edward: state of the garage" },
      { id: "b", title: "Edward: state" },
      { id: "c", title: "state" },
    ];
    assert.equal(pickStateDocument(docs)?.id, "b");
    assert.equal(STATE_DOC_TITLE, "Edward: state");
  });
  it("forgives surrounding space and case, and is null when there is none", () => {
    assert.equal(pickStateDocument([{ id: "x", title: "  edward: STATE " }])?.id, "x");
    assert.equal(pickStateDocument([{ id: "y", title: "Notes" }]), null);
    assert.equal(pickStateDocument([]), null);
  });
});

describe("stateExcerpt", () => {
  it("is the first two sentences of his prose, before any heading", () => {
    const doc =
      "I'm Edward. I keep Ellis's days light. I also watch the pets.\n\n## What I'm watching\n- Biscuit's PM doses\n";
    assert.equal(stateExcerpt(doc), "I'm Edward. I keep Ellis's days light.");
  });
  it("skips a leading title heading and markdown emphasis and links", () => {
    const doc = "# Edward: state\n\nI keep **Ellis's** days [light](https://x.y). Second one here! Third.";
    assert.equal(stateExcerpt(doc), "I keep Ellis's days light. Second one here!");
  });
  it("does not split on a title like Mr. or Dr.", () => {
    assert.equal(
      stateExcerpt("Mr. Whiskers needs his pill at 8. Dr. Novak said so. Then more."),
      "Mr. Whiskers needs his pill at 8. Dr. Novak said so.",
    );
  });
  it("falls back to the first list items when there is no prose", () => {
    assert.equal(stateExcerpt("## Watching\n- Biscuit's PM doses\n- Parker's Saturday plans\n- Runs"), "Biscuit's PM doses. Parker's Saturday plans.");
  });
  it("clips a very long opening on a word boundary", () => {
    const long = `I ${"really ".repeat(60)}care.`;
    const out = stateExcerpt(long, { maxChars: 80 });
    assert.ok(out.length <= 81, `got ${out.length}`);
    assert.ok(out.endsWith("…"));
    assert.doesNotMatch(out, /\s…$/);
  });
  it("is empty for an empty page", () => {
    assert.equal(stateExcerpt("   \n\n"), "");
  });
});

describe("knowsLine", () => {
  it("counts memories and today's new ones", () => {
    assert.equal(knowsLine({ total: 4812, created_last_24h: 23 }), "4,812 memories · 23 today");
  });
  it("says so when nothing is new, and handles one", () => {
    assert.equal(knowsLine({ total: 1, created_last_24h: 0 }), "1 memory · nothing new today");
    assert.equal(knowsLine({ total: 12 }), "12 memories");
  });
  it("has an honest line without stats", () => {
    assert.equal(knowsLine(null), "Search what he knows");
  });
  it("counts live memories — the number the Memories panel shows — not retired ones", () => {
    assert.equal(knowsLine({ total: 4843, live: 4432, created_last_24h: 37 }), "4,432 memories · 37 today");
  });
});

describe("watchesLine", () => {
  it("counts open intentions and what waits on Ellis", () => {
    assert.equal(watchesLine(3, 1), "3 intentions · 1 waiting on you");
    assert.equal(watchesLine(1, 0), "1 intention · nothing waiting on you");
    assert.equal(watchesLine(0, 2), "No open intentions · 2 waiting on you");
  });
  it("leaves out an unknown intention count", () => {
    assert.equal(watchesLine(null, 0), "Nothing waiting on you");
  });
  it("never says nothing is waiting when it can't check (the mini is unreachable)", () => {
    assert.equal(watchesLine(null, null), "Can't check what's waiting right now");
    assert.equal(watchesLine(3, null), "3 intentions · can't check what's waiting");
  });
});

describe("doesAnswer", () => {
  it("says what the last wake did, with the thinking to read when it acted", () => {
    const a = doesAnswer(
      mind({ recent: [row({ outcome: "acted", line: "Replied to Parker about the flight.", thought_id: "t-parker" })] }),
      { now: NOW },
    );
    assert.equal(a.title, "Woke 12m ago · acted");
    assert.equal(a.detail, "Replied to Parker about the flight.");
    assert.equal(a.thoughtHref, "/chat?c=t-parker");
  });
  it("points at the last wake that acted when the latest one left things alone", () => {
    const a = doesAnswer(
      mind({
        recent: [
          row({ at: "2026-09-26T17:40:00Z", outcome: "left_alone" }),
          row({ at: "2026-09-26T16:58:00Z", outcome: "acted", line: "Left a note on the vet.", thought_id: "t-vet" }),
        ],
      }),
      { now: NOW },
    );
    assert.equal(a.title, "Woke 20m ago · nothing needed doing");
    assert.equal(a.detail, "Last acted 1h ago — Left a note on the vet.");
    assert.equal(a.thoughtHref, "/chat?c=t-vet");
  });
  it("tells the truth about a wake running now", () => {
    const a = doesAnswer(mind({ waking: { reason: "morning", started_at: null, conversation_id: "edward-mind" } }), { now: NOW });
    assert.equal(a.title, "Awake now · morning wake");
    assert.equal(a.live, true);
  });
  it("names a failed or skipped wake plainly", () => {
    assert.equal(doesAnswer(mind({ recent: [row({ outcome: "failed" })] }), { now: NOW }).title, "Woke 12m ago · didn't finish");
    assert.equal(doesAnswer(mind({ recent: [row({ outcome: "skipped" })] }), { now: NOW }).title, "Skipped a wake 12m ago · out of fuel");
  });
  it("has nothing to claim with no wakes or no answer", () => {
    assert.equal(doesAnswer(mind(), { now: NOW }).title, "No wakes yet");
    const unknown = doesAnswer(null, { now: NOW });
    assert.equal(unknown.title, "What he does on his own");
    assert.equal(unknown.thoughtHref, null);
  });
});

describe("roomLines", () => {
  const base: RoomLineInput = {
    mindNow: mind({ recent: [row({ at: "2026-09-26T17:10:00Z", outcome: "acted", line: "x" })] }),
    intentions: 4,
    memory: { total: 4843, created_last_24h: 37 },
    heartbeat: { running: true, enabled: true },
    workersRunning: 0,
    wishesOpen: 2,
    house: { overall: "healthy", problems: [], repairs: [] },
  };

  it("gives Mind his last wake and open intentions, never the model", () => {
    const l = roomLines(base, { now: NOW });
    assert.equal(l.mind.text, "Woke 50m ago · acted · 4 intentions");
    assert.equal(l.mind.tone, "ok");
    assert.doesNotMatch(l.mind.text, /temp|gpt/);
  });
  it("says Mind is thinking now while a wake runs, and warns after a failed one", () => {
    assert.equal(
      roomLines({ ...base, mindNow: mind({ waking: { reason: "mention", started_at: null, conversation_id: null } }) }, { now: NOW }).mind.text,
      "Thinking now · answering a mention",
    );
    const failed = roomLines({ ...base, mindNow: mind({ recent: [row({ outcome: "failed" })] }) }, { now: NOW });
    assert.equal(failed.mind.tone, "warn");
  });
  it("counts memories", () => {
    assert.deepEqual(roomLines(base, { now: NOW }).memory, { tone: "ok", text: "4,843 memories · 37 new today" });
  });
  it("counts live memories when the stats carry them", () => {
    assert.deepEqual(roomLines({ ...base, memory: { total: 4843, live: 4432, created_last_24h: 37 } }, { now: NOW }).memory, {
      tone: "ok",
      text: "4,432 memories · 37 new today",
    });
  });
  it("doesn't claim nothing waits on Ellis when his mind can't be read", () => {
    const l = roomLines({ ...base, mindNow: null }, { now: NOW });
    assert.deepEqual(l.attention, { tone: "neutral", text: "Watching · can't check what's waiting" });
  });
  it("marks Attention amber only when something waits on Ellis", () => {
    assert.deepEqual(roomLines(base, { now: NOW }).attention, { tone: "ok", text: "Watching · nothing waiting on you" });
    const waiting = roomLines({ ...base, mindNow: mind({ approvals_waiting: 1 }) }, { now: NOW });
    assert.deepEqual(waiting.attention, { tone: "warn", text: "Watching · 1 waiting on you" });
    assert.equal(roomLines({ ...base, heartbeat: { running: false, enabled: true } }, { now: NOW }).attention.text, "Not watching messages — paused");
    assert.equal(roomLines({ ...base, heartbeat: { running: false, enabled: false } }, { now: NOW }).attention.text, "Not watching messages — switched off");
  });
  it("says what Work is running, and when he is out of fuel", () => {
    assert.deepEqual(roomLines(base, { now: NOW }).work, { tone: "ok", text: "Nothing running · 2 open wishes" });
    assert.deepEqual(roomLines({ ...base, workersRunning: 1, wishesOpen: 1 }, { now: NOW }).work, {
      tone: "info",
      text: "1 worker running · 1 open wish",
    });
    const out = roomLines(
      { ...base, mindNow: mind({ quota: { used_percent: 100, exhausted: true, resets_at: "2026-09-29T20:00:00Z" } }) },
      { now: NOW, tz: "America/Chicago" },
    );
    assert.equal(out.work.tone, "warn");
    assert.match(out.work.text, /^Out of fuel · back Tue 3:00 pm/);
    const day = roomLines(
      { ...base, mindNow: mind({ quota: { used_percent: 104, exhausted: true, resets_at: null, period: "day" } }) },
      { now: NOW, tz: "America/Chicago" },
    );
    assert.equal(day.work.text, "Out of fuel for today");
  });
  it("reads House from the health check without crying wolf over a self-repair", () => {
    assert.deepEqual(roomLines(base, { now: NOW }).house, { tone: "ok", text: "Everything healthy" });
    const fixed = roomLines(
      { ...base, house: { overall: "repaired", problems: [], repairs: ["graph-worker restarted (exit 1) at 04:10"] } },
      { now: NOW },
    ).house;
    assert.deepEqual(fixed, { tone: "info", text: "Fixed itself · graph-worker restarted at 04:10" }, "no exit codes on Ellis's side");
    const bad = roomLines({ ...base, house: { overall: "problem", problems: ["Postgres is down"], repairs: [] } }, { now: NOW }).house;
    assert.deepEqual(bad, { tone: "bad", text: "Needs you · Postgres is down" });
  });
  it("is neutral and honest when a source can't be read", () => {
    const l = roomLines(
      { mindNow: null, intentions: null, memory: null, heartbeat: null, workersRunning: null, wishesOpen: null, house: null },
      { now: NOW },
    );
    for (const k of ["mind", "memory", "attention", "work", "house"] as const) {
      assert.equal(l[k].tone, "neutral", k);
      assert.ok(l[k].text.length > 0, k);
    }
    assert.equal(l.memory.text, "Can't count memories right now");
  });
});
