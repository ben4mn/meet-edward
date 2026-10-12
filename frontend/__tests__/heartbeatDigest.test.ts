// R/G red for the heartbeat digest.
//
// The heartbeat panel scrolled every text of the day with snake_case
// labels. What Ellis wants to see is what Edward *decided*: the handful of
// noted / watching / escalated / handled-by-you events, each with a reason
// in one sentence — and everything dismissed folded to a per-person count
// he can open to audit. Pure so it can be pinned.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { buildDigest, humanStatus, reasonFor, toneFor } from "../components/settings/heartbeatDigestModel.ts";

const ev = (id: string, p: Partial<Record<string, unknown>>) => ({
  id,
  source: "imessage",
  event_type: "message_received",
  sender: "+1",
  contact_name: "Rowan",
  chat_identifier: "+1",
  chat_name: null,
  summary: "hi",
  raw_data: null,
  is_from_user: false,
  created_at: "2026-09-06T01:00:00Z",
  triage_status: "dismissed",
  briefed: false,
  ...p,
});

describe("humanStatus / toneFor", () => {
  it("never shows snake_case", () => {
    assert.equal(humanStatus("handled_by_owner"), "Handled by you");
    assert.equal(humanStatus("watching"), "Watching");
    assert.equal(humanStatus("noted"), "Noted");
    assert.equal(humanStatus("escalated"), "Told you");
    assert.equal(humanStatus("mention"), "Mentioned Edward");
    assert.equal(humanStatus("follow_up"), "Following up");
    assert.equal(humanStatus("calendar_urgent"), "Calendar, urgent");
    assert.equal(humanStatus("dismissed"), "Set aside");
    assert.equal(humanStatus("pending"), "Waiting for triage");
  });
  it("maps to status tones", () => {
    assert.equal(toneFor("escalated"), "bad");
    assert.equal(toneFor("watching"), "warn");
    assert.equal(toneFor("noted"), "info");
    // Ellis's own reply is never painted in Edward's emerald (design §1.3).
    assert.equal(toneFor("handled_by_owner"), "ok");
    assert.equal(toneFor("acted"), "edward");
    assert.equal(toneFor("dismissed"), "neutral");
  });
});

describe("reasonFor", () => {
  it("explains each decision in one sentence", () => {
    assert.equal(reasonFor(ev("a", { triage_status: "handled_by_owner" })), "You replied yourself. Nothing for me to do.");
    assert.equal(reasonFor(ev("a", { triage_status: "watching" })), "Still forming. I'll surface it if it firms up.");
    assert.equal(reasonFor(ev("a", { triage_status: "noted" })), "Saved as a note and an open loop.");
    assert.equal(reasonFor(ev("a", { triage_status: "escalated" })), "Looked urgent, so I told you.");
  });
});

describe("buildDigest", () => {
  const events = [
    ev("1", { triage_status: "dismissed", contact_name: "Rowan", created_at: "2026-09-06T01:00:00Z" }),
    ev("2", { triage_status: "dismissed", contact_name: "Rowan", created_at: "2026-09-06T01:01:00Z" }),
    ev("3", { triage_status: "dismissed", contact_name: "Ellis", is_from_user: true, created_at: "2026-09-06T01:02:00Z" }),
    ev("4", { triage_status: "noted", contact_name: "Quinn", created_at: "2026-09-06T00:30:00Z" }),
    ev("5", { triage_status: "handled_by_owner", contact_name: "Sasha", created_at: "2026-09-06T00:50:00Z" }),
    ev("6", { triage_status: "escalated", contact_name: "Jordan", created_at: "2026-09-06T00:10:00Z" }),
    ev("7", { triage_status: "pending", contact_name: "New", created_at: "2026-09-06T01:05:00Z" }),
    ev("8", { triage_status: "acted", contact_name: "Ellis", is_from_user: true, created_at: "2026-09-06T01:06:00Z" }),
  ];

  it("never lists Ellis's own outbound texts as decisions", () => {
    const d = buildDigest(events);
    assert.ok(!d.decisions.some((x) => x.id === "8"));
    const you = d.setAside.find((p) => p.who === "You");
    assert.equal(you?.count, 2);
  });

  it("lists decisions newest first and leaves dismissals out", () => {
    const d = buildDigest(events);
    assert.deepEqual(d.decisions.map((x) => x.id), ["5", "4", "6"]);
  });

  it("folds dismissals into per-person counts, biggest first, naming you as You", () => {
    const d = buildDigest(events);
    assert.deepEqual(d.setAside.map((p) => [p.who, p.count]), [["Rowan", 2], ["You", 2]]);
    assert.equal(d.setAsideTotal, 4);
  });

  it("counts every status", () => {
    const d = buildDigest(events);
    assert.equal(d.counts.dismissed, 3);
    assert.equal(d.counts.noted, 1);
    assert.equal(d.counts.pending, 1);
    assert.equal(d.waiting, 1);
  });
});

// 2026-09-26 (Jarvis polish): with the mind on (`mind_enabled` on
// /api/heartbeat/status), "escalated" no longer means "I pushed you" — it
// means the message was handed to Edward's mind for its next wake. The
// digest must say so, in Edward's colour, and never "Told you" in red.
import { feedbackOptions, heartbeatStatusLine, heartbeatView, startOfDayIn, todaysEvents, whoOf } from "../components/settings/heartbeatDigestModel.ts";

describe("escalated with the mind on", () => {
  const mind = { mindEnabled: true };
  it("is 'Handed to his mind', in Edward's tone, with a first-person reason", () => {
    assert.equal(humanStatus("escalated", mind), "Handed to his mind");
    assert.equal(toneFor("escalated", mind), "edward");
    assert.equal(reasonFor(ev("a", { triage_status: "escalated" }), mind), "I looked at this on my next wake.");
  });
  it("never says 'Told you' or looks bad", () => {
    assert.notEqual(humanStatus("escalated", mind), "Told you");
    assert.notEqual(toneFor("escalated", mind), "bad");
  });
  it("leaves the old wording alone with the mind off", () => {
    assert.equal(humanStatus("escalated", { mindEnabled: false }), "Told you");
    assert.equal(toneFor("escalated", { mindEnabled: false }), "bad");
    assert.equal(reasonFor(ev("a", { triage_status: "escalated" }), { mindEnabled: false }), "Looked urgent, so I told you.");
  });
  it("offers a right-call pair that fits a hand-off", () => {
    assert.deepEqual(
      feedbackOptions("escalated", mind).map((o) => o.verdict),
      ["right_call", "not_worth_noting"],
    );
  });
});

describe("whoOf", () => {
  it("names a calendar event by its title, never 'Unknown'", () => {
    const cal = ev("c", { source: "calendar", sender: null, contact_name: null, chat_identifier: null,
      raw_data: { title: "Call with the contractor" } });
    assert.equal(whoOf(cal as never), "Call with the contractor");
    assert.equal(whoOf(ev("d", { source: "calendar", sender: null, contact_name: null, raw_data: null }) as never), "Calendar");
  });
  it("prefers the contact, then the chat, then the sender", () => {
    assert.equal(whoOf(ev("e", { contact_name: "Mom" }) as never), "Mom");
    assert.equal(whoOf(ev("f", { contact_name: null, chat_name: "Family" }) as never), "Family");
    assert.equal(whoOf(ev("g", { is_from_user: true }) as never), "You");
  });
});

describe("todaysEvents", () => {
  it("keeps only what arrived since midnight in Ellis's zone", () => {
    const now = new Date("2026-09-06T15:00:00Z"); // 10:00 in Springfield
    const start = startOfDayIn("America/Chicago", now);
    assert.equal(start.toISOString(), "2026-09-06T05:00:00.000Z");
    const kept = todaysEvents(
      [ev("1", { created_at: "2026-09-06T05:30:00Z" }), ev("2", { created_at: "2026-09-06T04:59:00Z" })] as never[],
      "America/Chicago",
      now,
    );
    assert.deepEqual(kept.map((e: { id: string }) => e.id), ["1"]);
  });
});

describe("heartbeatStatusLine", () => {
  const now = new Date("2026-09-06T15:00:00Z");
  const base = {
    running: true, enabled: true, pending_count: 1,
    last_triage_at: "2026-09-06T14:57:00Z", next_triage_at: "2026-09-06T15:01:00Z",
    tracks: {
      imessage: { enabled: true, status: "running" },
      calendar: { enabled: true, status: "running" },
      email: { enabled: true, status: "running" },
      gmail: { enabled: false, status: "disabled" },
    },
  };
  it("is one sentence: what I listen to, when I last looked, what is waiting", () => {
    assert.equal(
      heartbeatStatusLine(base as never, now),
      "I'm listening to messages, calendar and email — last looked 3m ago, 1 waiting.",
    );
  });
  it("names a track that is not answering", () => {
    const s = { ...base, pending_count: 0, tracks: { ...base.tracks, calendar: { enabled: true, status: "error" } } };
    assert.equal(
      heartbeatStatusLine(s as never, now),
      "I'm listening to messages and email (calendar isn't answering) — last looked 3m ago.",
    );
  });
  it("says plainly when I am paused or off", () => {
    assert.equal(heartbeatStatusLine({ ...base, running: false } as never, now), "Paused — I'm not listening right now.");
    assert.equal(heartbeatStatusLine({ ...base, running: false, enabled: false } as never, now), "I'm not watching your messages right now.");
    assert.equal(heartbeatStatusLine(null, now), "");
  });
});

describe("heartbeatView", () => {
  it("reads ?view=log|config and defaults to the digest", () => {
    assert.equal(heartbeatView(new URLSearchParams("view=log")), "log");
    assert.equal(heartbeatView(new URLSearchParams("view=config")), "config");
    assert.equal(heartbeatView(new URLSearchParams("view=nope")), "digest");
    assert.equal(heartbeatView(null), "digest");
  });
});

// A stored interval the presets do not list (the mini runs triage every
// 120 s and email every 600 s) still shows as the selected option.
import { secondsLabel, withCurrentOption } from "../components/settings/heartbeatDigestModel.ts";

describe("withCurrentOption", () => {
  const presets = [{ label: "1m", value: "60" }, { label: "5m", value: "300" }];
  it("adds the stored value, in order, when no preset matches", () => {
    assert.deepEqual(withCurrentOption(presets, 120), [
      { label: "1m", value: "60" }, { label: "2m", value: "120" }, { label: "5m", value: "300" },
    ]);
  });
  it("leaves the presets alone when one matches", () => {
    assert.equal(withCurrentOption(presets, 300), presets);
  });
  it("reads seconds in words", () => {
    assert.equal(secondsLabel(10), "10s");
    assert.equal(secondsLabel(600), "10m");
    assert.equal(secondsLabel(90), "90s");
    assert.equal(secondsLabel(3600), "1h");
    assert.equal(secondsLabel(5400), "90m");
  });
});

describe("senderSubline", () => {
  it("names what a blocked sender is without raw ids", async () => {
    const { senderSubline } = await import("../components/settings/heartbeatDigestModel.ts");
    assert.equal(senderSubline("chat874412", "Family"), "Group chat");
    assert.equal(senderSubline("+15555550142", "Parker"), "(555) 555-0142");
    assert.equal(senderSubline("5555550142", "Parker"), "(555) 555-0142");
    assert.equal(senderSubline("+447700900123", "Sam"), "+447700900123");
    assert.equal(senderSubline("parker@example.com", "Parker"), "parker@example.com");
    assert.equal(senderSubline("Parker", "Parker"), null);
  });
});

describe("pings (2026-10-11)", () => {
  // A ping (someone asked Edward directly: an @mention, a reply to his
  // message, Ellis's own chat with him) never enters triage; the listener
  // stores it `ping` and the reply path answers it in the chat. It must not
  // vanish from Listening.
  it("is a decision with a human label and a reason", () => {
    const d = buildDigest([ev("p1", { triage_status: "ping", summary: "@edward which set is first?" })]);
    assert.deepEqual(d.decisions.map((e) => e.id), ["p1"]);
    assert.equal(humanStatus("ping"), "Asked me");
    assert.ok(!/reply path|triage|ping/i.test(humanStatus("ping")));
    const reason = reasonFor(ev("p1", { triage_status: "ping" }) as never);
    assert.match(reason, /asked me/i);
    assert.ok(!/reply path|triage|heartbeat/i.test(reason));
    assert.equal(toneFor("ping"), "info");
  });
});
