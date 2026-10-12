// The bar above a thought entry (2026-09-25): "Edward acted · <reason> ·
// <when>", a "reached out" marker when he sent something, and "Right
// call?" that posts the verdict at once and rolls back if the post fails.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ThoughtEntryHeader } from "../components/chat/ThoughtEntryHeader.tsx";
import type { ThoughtMeta } from "../lib/thoughtEntry.ts";

const T: ThoughtMeta = {
  kind: "mind_action",
  ledger_id: 42,
  reason: "messages",
  acted_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
  outward: true,
  reached: true,
  verdict: null,
};

// Never hand a DOM node to assert (node:assert inspecting a jsdom element
// on failure kills the run); compare booleans.
const present = (el: Element | null) => el !== null;

afterEach(cleanup);

describe("ThoughtEntryHeader", () => {
  it("says he acted, why and when, and marks an outward action", () => {
    render(<ThoughtEntryHeader thought={T} post={async () => ({})} />);
    assert.equal(present(screen.queryByText("Edward acted · messages wake · 2h ago")), true);
    assert.equal(present(screen.queryByText("reached out")), true);
    render(<ThoughtEntryHeader thought={{ ...T, ledger_id: 7, outward: false, reached: false }} post={async () => ({})} />);
    assert.equal(screen.getAllByText("reached out").length, 1, "only the one that reached someone is marked");
  });

  // L3 (2026-09-27 follow-ups): a note Ellis will see on Today, or a send
  // held for his approval, is outward but reached nobody. The chip reads
  // thought.reached; an entry written before it existed says null: unknown,
  // no chip, never a guess from outward (integration review, 2026-09-27).
  it("marks only an action that reached someone (thought.reached), not a note or a held send", () => {
    render(<ThoughtEntryHeader thought={{ ...T, ledger_id: 8, outward: true, reached: false }} post={async () => ({})} />);
    assert.equal(present(screen.queryByText("reached out")), false, "a note is not reaching out");
    cleanup();
    render(<ThoughtEntryHeader thought={{ ...T, ledger_id: 9, outward: true, reached: true }} post={async () => ({})} />);
    assert.equal(present(screen.queryByText("reached out")), true);
    cleanup();
    render(<ThoughtEntryHeader thought={{ ...T, ledger_id: 10, outward: true, reached: null }} post={async () => ({})} />);
    assert.equal(present(screen.queryByText("reached out")), false, "an older entry is unknown, not outward");
    cleanup();
    const { reached: _drop, ...older } = T;
    render(<ThoughtEntryHeader thought={{ ...older, ledger_id: 11, outward: true }} post={async () => ({})} />);
    assert.equal(present(screen.queryByText("reached out")), false, "no field at all is unknown too");
  });

  it("posts the verdict with the ledger id and reads it back", async () => {
    const calls: [number, string][] = [];
    render(<ThoughtEntryHeader thought={T} post={async (id, v) => { calls.push([id, v]); return {}; }} />);
    fireEvent.click(screen.getByRole("button", { name: /yes, right call/i }));
    // Optimistic: read back at once, saving, no "Change" until the post answers.
    assert.equal(present(screen.queryByText("You said: right call")), true);
    assert.equal(present(screen.queryByLabelText("Saving")), true);
    assert.equal(present(screen.queryByRole("button", { name: /change your verdict/i })), false);
    assert.deepEqual(calls, [[42, "right_call"]]);
    // Settled: wait for "Change" itself — the label is already there while
    // saving, so waiting on it and then getBy-ing "Change" races the post.
    const change = await screen.findByRole("button", { name: /change your verdict/i });
    assert.equal(present(screen.queryByLabelText("Saving")), false);
    assert.equal(present(screen.queryByText("You said: right call")), true);
    assert.equal(present(screen.queryByRole("button", { name: /yes, right call/i })), false);
    fireEvent.click(change);
    assert.equal(present(screen.queryByRole("button", { name: /no, wrong call/i })), true);
  });

  it("rolls back and says so when the post fails", async () => {
    render(<ThoughtEntryHeader thought={T} post={async () => { throw new Error("Failed to record the verdict"); }} />);
    fireEvent.click(screen.getByRole("button", { name: /no, wrong call/i }));
    assert.equal(present(await screen.findByRole("alert")), true);
    assert.equal(present(screen.queryByText("You said: wrong call")), false);
    assert.equal(present(screen.queryByRole("button", { name: /no, wrong call/i })), true);
  });

  it("shows a verdict the backend already has", () => {
    render(<ThoughtEntryHeader thought={{ ...T, verdict: "wrong_call" }} post={async () => ({})} />);
    assert.equal(present(screen.queryByText("You said: wrong call")), true);
  });
});

// 2026-09-26 review: "No" recorded a bare wrong_call — his next wake had to
// guess what he did wrong. After a No, one optional line asks what he
// should have done; the note goes with the verdict (POST /api/mind/verdict
// takes `note`).
describe("ThoughtEntryHeader: why it was the wrong call", () => {
  it("after No, asks what he should have done and sends the note with the verdict", async () => {
    const calls: unknown[][] = [];
    render(<ThoughtEntryHeader thought={T} post={async (...args: unknown[]) => { calls.push(args); return {}; }} />);
    fireEvent.click(screen.getByRole("button", { name: /no, wrong call/i }));
    const field = (await screen.findByRole("textbox", { name: /what should i have done/i })) as HTMLInputElement;
    fireEvent.change(field, { target: { value: "Leave Parker's thread to me" } });
    // A one-field form: Enter submits it in a browser (jsdom does not synthesise that), as does Send.
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(/I.ll read that next time I wake/i);
    assert.deepEqual(calls[calls.length - 1], [42, "wrong_call", "Leave Parker's thread to me"]);
    assert.equal(present(screen.queryByRole("textbox", { name: /what should i have done/i })), false);
  });

  it("is optional: it can be dismissed, and a Yes never asks", async () => {
    render(<ThoughtEntryHeader thought={T} post={async () => ({})} />);
    fireEvent.click(screen.getByRole("button", { name: /no, wrong call/i }));
    await screen.findByRole("textbox", { name: /what should i have done/i });
    fireEvent.click(screen.getByRole("button", { name: /skip/i }));
    assert.equal(present(screen.queryByRole("textbox", { name: /what should i have done/i })), false);
    cleanup();
    render(<ThoughtEntryHeader thought={{ ...T, ledger_id: 9 }} post={async () => ({})} />);
    fireEvent.click(screen.getByRole("button", { name: /yes, right call/i }));
    await screen.findByRole("button", { name: /change your verdict/i });
    assert.equal(present(screen.queryByRole("textbox", { name: /what should i have done/i })), false);
  });
});
