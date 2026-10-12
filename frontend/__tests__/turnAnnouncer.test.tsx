// One announcer per chat turn (review 2026-09-26: aria-live-turn-chatter —
// one turn fired 26 polite announcements in paired duplicates, and the
// answer itself was never announced). The work rail's status is visual
// only; the chat view owns a single sr-only polite region that says each
// coarse phase once (Thinking / Working / Writing, debounced), then how the
// turn ended: "Edward replied: <first sentence>" or "Edward stopped: …".

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, render } from "@testing-library/react";

import { coarsePhase, endAnnouncement, firstSentence } from "../lib/turnAnnouncer.ts";
import { TurnAnnouncer } from "../components/chat/TurnAnnouncer.tsx";
import { WorkRail } from "../components/chat/WorkRail.tsx";
import { endTurn, reduceTurn, startTurn, type Turn } from "../lib/turnModel.ts";
import type { Message } from "../lib/ChatContext.tsx";

afterEach(cleanup);

const T0 = 1_000;
const tick = (ms: number) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));

describe("turnAnnouncer (pure)", () => {
  it("names the coarse phase only, never a tool or a count", () => {
    let t = startTurn("a1", "c1", T0);
    assert.equal(coarsePhase(t), "Thinking");
    t = reduceTurn(t, { type: "tool_start", tool_name: "web_search" }, T0 + 1);
    assert.equal(coarsePhase(t), "Working");
    t = reduceTurn(t, { type: "tool_end", tool_name: "web_search", result: "ok" }, T0 + 2);
    t = reduceTurn(t, { type: "content", content: "Found it." }, T0 + 3);
    assert.equal(coarsePhase(t), "Writing");
    assert.equal(coarsePhase(endTurn(t, "done", T0 + 4)), null);
    assert.equal(coarsePhase(null), null);
  });

  it("the first sentence, plain and at most 140 characters", () => {
    assert.equal(firstSentence("**Book the 1–4pm slot.** It's the calmest window."), "Book the 1–4pm slot.");
    assert.equal(firstSentence("See [the forecast](https://x.test) — calm all day"), "See the forecast — calm all day");
    const long = "word ".repeat(60);
    assert.equal(firstSentence(long).length <= 140, true);
    assert.equal(firstSentence(""), "");
  });

  it("how the turn ended, in plain words", () => {
    const t = startTurn("a1", "c1", T0);
    assert.equal(endAnnouncement(endTurn(t, "done", T0 + 5), "Done. The rest is below."), "Edward replied: Done.");
    assert.equal(endAnnouncement(endTurn(t, "done", T0 + 5), ""), "Edward replied.");
    assert.equal(endAnnouncement(endTurn(t, "stopped", T0 + 5), "half"), "Edward stopped: you stopped me.");
    assert.equal(endAnnouncement(endTurn(t, "error", T0 + 5), ""), "Edward stopped: I couldn't finish that.");
    assert.equal(endAnnouncement(t, ""), null, "a live turn has not ended");
  });
});

describe("<TurnAnnouncer>", () => {
  const live = (el: HTMLElement) => el.querySelector('[aria-live="polite"]') as HTMLElement;

  it("says each coarse phase once (debounced), then the reply", async () => {
    let turn: Turn = startTurn("a1", "c1", T0);
    const msgs = (content: string): Message[] => [{ id: "a1", role: "assistant", content }];
    const { container, rerender } = render(<TurnAnnouncer turn={turn} messages={msgs("")} debounceMs={30} />);
    const said: string[] = [];
    const observer = new window.MutationObserver(() => said.push(live(container).textContent ?? ""));
    observer.observe(live(container), { childList: true, characterData: true, subtree: true });

    // Fast flips collapse: tool start / end / start within the debounce.
    turn = reduceTurn(turn, { type: "tool_start", tool_name: "web_search" }, T0 + 1);
    rerender(<TurnAnnouncer turn={turn} messages={msgs("")} debounceMs={30} />);
    turn = reduceTurn(turn, { type: "tool_end", tool_name: "web_search", result: "ok" }, T0 + 2);
    rerender(<TurnAnnouncer turn={turn} messages={msgs("")} debounceMs={30} />);
    turn = reduceTurn(turn, { type: "tool_start", tool_name: "browser_open" }, T0 + 3);
    rerender(<TurnAnnouncer turn={turn} messages={msgs("")} debounceMs={30} />);
    await tick(60);
    turn = reduceTurn(turn, { type: "tool_end", tool_name: "browser_open", result: "ok" }, T0 + 4);
    turn = reduceTurn(turn, { type: "content", content: "Book it." }, T0 + 5);
    rerender(<TurnAnnouncer turn={turn} messages={msgs("Book it.")} debounceMs={30} />);
    await tick(60);
    turn = endTurn(turn, "done", T0 + 6);
    rerender(<TurnAnnouncer turn={turn} messages={msgs("Book it. The 1–4pm window is calm.")} debounceMs={30} />);
    await tick(10);
    observer.disconnect();
    const distinct = said.filter((s, i) => s && s !== said[i - 1]);
    assert.deepEqual(distinct, ["Edward · Working", "Edward · Writing", "Edward replied: Book it."]);
  });

  it("a turn that had already ended when the view opened is not announced", async () => {
    const turn = endTurn(startTurn("a0", "c1", T0), "done", T0 + 9);
    const { container } = render(
      <TurnAnnouncer turn={turn} messages={[{ id: "a0", role: "assistant", content: "Old answer." }]} debounceMs={10} />,
    );
    await tick(30);
    assert.equal(live(container).textContent, "");
  });
});

describe("WorkRail is visual only", () => {
  it("its live status is not a live region", () => {
    let turn = startTurn("a1", "c1", Date.now() - 3000);
    turn = reduceTurn(turn, { type: "tool_start", tool_name: "web_search" }, Date.now());
    const msg: Message = { id: "a1", role: "assistant", content: "", toolActivities: [{ id: "t1", tool_name: "web_search", status: "running", timestamp: 1 }], trace: [{ kind: "tool", id: "t1" }] };
    const { container } = render(<WorkRail message={msg} live turn={turn} />);
    assert.equal(container.querySelectorAll("[aria-live]").length, 0);
  });

  it("is not a landmark per message (a region rotor full of 'What Edward did')", () => {
    const msg: Message = { id: "a1", role: "assistant", content: "hi", toolActivities: [{ id: "t1", tool_name: "web_search", status: "completed", timestamp: 1, result: "ok" }], trace: [{ kind: "tool", id: "t1" }] };
    const { container } = render(<WorkRail message={msg} live={false} turn={null} />);
    assert.equal(container.querySelectorAll("section").length, 0);
  });
});
