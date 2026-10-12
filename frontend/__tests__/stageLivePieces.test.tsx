// The live pieces on the stage (2026-09-28): the calendar and Listening get
// stage faces — sentence-case titles, Inter, no emerald (it means Edward),
// status colours only for status, no counter ticking for show — and every
// live piece draws the title Edward gave it, since his own view of the
// screen names it (screen_service.piece_phrase).

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";

import { StagePiece } from "../app/(display)/display/components/StagePiece.tsx";
import { calendarStageView, listeningRows, listeningWord } from "../app/(display)/display/lib/stageLive.ts";
import type { StagePiece as Piece } from "../app/(display)/display/lib/stageModel.ts";
import type { CalendarEvent } from "../app/(display)/display/lib/types.ts";

const tz = "America/Chicago";
const NOW = new Date("2026-09-28T19:00:00Z"); // 2:00 pm in Springfield
const at = (min: number) => new Date(NOW.getTime() + min * 60_000).toISOString();

const EVENTS: CalendarEvent[] = [
  { id: "a", title: "Standup", start: at(-120), end: at(-105), where: "", kind: "work" },
  { id: "b", title: "1:1 with Priya", start: at(18), end: at(48), where: "Zoom", kind: "focus" },
  { id: "c", title: "Dinner with Rowan", start: at(300), end: at(390), where: "", kind: "personal" },
];

const originalFetch = globalThis.fetch;
const g = globalThis as { React?: typeof React };
const hadReact = g.React;

beforeEach(() => {
  g.React = React;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } });
    if (url.includes("/api/display/calendar")) return json({ source: "apple", events: EVENTS });
    if (url.includes("/api/heartbeat/events")) {
      return json([
        { id: "e1", source: "imessage", event_type: "message", sender: "+1555", contact_name: "Parker", summary: "Saturday?", is_from_user: false, created_at: "2026-09-28T20:28:00Z", triage_status: "escalated" },
        { id: "e2", source: "gmail", event_type: "email", sender: "news@x.com", contact_name: null, summary: "Weekly digest", is_from_user: false, created_at: "2026-09-28T20:10:00Z", triage_status: "dismissed" },
      ]);
    }
    if (url.includes("/api/heartbeat/status")) return json({ running: true, enabled: true, pending_count: 0, listener_status: "running", tracks: {} });
    return json({});
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
  if (hadReact) g.React = hadReact;
  else delete g.React;
});

const piece = (kind: Piece["kind"], title: string | null = null): Piece => ({
  id: kind, kind, size: "m", enter: "fade", place: "center", rect: { x: 1, y: 1, w: 8, h: 6 }, title,
});
const draw = (p: Piece) => render(<StagePiece piece={p} stage={{ cols: 24, rows: 12 }} dimmed={false} nowMs={Date.now()} tz={tz} signed={null} />);

/** Nothing inside may paint in Edward's emerald. */
function assertNoEmerald(root: HTMLElement) {
  for (const el of Array.from(root.querySelectorAll<HTMLElement>("*"))) {
    const style = el.getAttribute("style") ?? "";
    assert.doesNotMatch(style, /--ed-accent|--st-edward/, `${el.tagName}.${el.className}: ${style}`);
  }
  assert.equal(root.querySelector(".mc-pill-ok"), null, "no status pill in emerald");
}

describe("calendarStageView", () => {
  it("the next meeting in words, rows with plain times, past ones marked, no category colours", () => {
    const v = calendarStageView(EVENTS, NOW, tz);
    assert.equal(v.next, "1:1 with Priya · in 18 min");
    assert.deepEqual(v.rows.map((r) => [r.label, r.detail, r.past]), [
      ["Standup", "12:00 pm", true],
      ["1:1 with Priya", "2:18 pm · Zoom", false],
      ["Dinner with Rowan", "7:00 pm", false],
    ]);
  });
  it("hours read as hours", () => {
    assert.equal(calendarStageView([EVENTS[2]], NOW, tz).next, "Dinner with Rowan · in 5 hr");
  });
});

describe("Listening, in Ellis's words", () => {
  it("never a raw triage word", () => {
    for (const s of ["pending", "noted", "acted", "escalated", "dismissed", "mention", "calendar_urgent", "", "something_new"]) {
      const w = listeningWord(s);
      assert.doesNotMatch(w.word, /_|escalat|triage|dismiss|pending/i, `${s} → ${w.word}`);
    }
    assert.equal(listeningWord("escalated").tone, "warn");
    assert.equal(listeningWord("dismissed").tone, "mute");
  });
  it("rows: who, what, when in the one clock format", () => {
    const [parker] = listeningRows(
      [{ id: "e1", source: "imessage", event_type: "message", sender: "+1555", contact_name: "Parker", summary: "Saturday?", is_from_user: false, created_at: "2026-09-28T20:28:00Z", triage_status: "escalated" }],
      tz,
    );
    assert.deepEqual([parker.who, parker.what, parker.when, parker.word], ["Parker", "Saturday?", "3:28 pm", "needs you"]);
  });
});

describe("live pieces on the stage", () => {
  it("the calendar: a sentence-case title, the next meeting in words, no emerald, no count pill, no uppercase kinds", async () => {
    const { container, findByText } = draw(piece("calendar"));
    await findByText("Standup");
    const root = container.querySelector('[data-piece="calendar"]') as HTMLElement;
    assert.equal(root.querySelector(".st-card-title")?.textContent, "Today");
    assert.doesNotMatch(root.textContent ?? "", /CALENDAR|EVENTS|NEXT UP|FOCUS|PERSONAL|\bWORK\b/);
    assert.equal(root.querySelector(".mc-mono"), null, "Inter, not mono");
    assertNoEmerald(root);
  });

  it("the Listening piece: its name is Listening, statuses in Ellis's words, times in the one format, nothing ticking", async () => {
    const { container, findByText } = draw(piece("heartbeat"));
    await findByText("Parker");
    const root = container.querySelector('[data-piece="heartbeat"]') as HTMLElement;
    const text = root.textContent ?? "";
    assert.equal(root.querySelector(".st-card-title")?.textContent, "Listening");
    assert.doesNotMatch(text, /heartbeat|triage|escalated|dismissed|cycle/i);
    assert.match(text, /needs you/);
    assert.match(text, /3:28 pm/);
    await new Promise((r) => setTimeout(r, 1_200));
    assert.equal(root.textContent, text, "nothing counts for show");
    assertNoEmerald(root);
  });

  it("every live piece draws the title Edward gave it", async () => {
    for (const kind of ["calendar", "heartbeat", "pets", "clock", "alerts", "recents", "workers", "vitals", "memory_ticker"] as const) {
      const { container, unmount } = draw(piece(kind, "Tonight"));
      await waitFor(() => assert.match(container.textContent ?? "", /Tonight/, kind));
      unmount();
    }
  });
});
