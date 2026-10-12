// The folded pack in the chat (2026-09-25): one line that says what the
// pack was, a real button that toggles the raw text (keyboard included),
// and a bubble for anything that is not a pack.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { PackCard } from "../components/chat/PackCard.tsx";

const PACK = `[WAKE: messages] fri 9:12am. Ellis is away.\nSince your last wake (1h): 3 new messages.\n\nWho you are and what you are doing\n${"raw pack line\n".repeat(80)}`;

// Never hand a DOM node to assert: on failure node:assert tries to inspect
// a jsdom element and the run dies of it. Compare booleans instead.
const absent = (el: Element | null) => el === null;
const present = (el: Element | null) => el !== null;

afterEach(cleanup);

describe("PackCard", () => {
  it("starts folded with a one-line summary and no raw text", () => {
    render(<PackCard text={PACK} />);
    // 2026-09-26: in Edward's words, not "Wake pack" (no internal vocabulary on Ellis's screens).
    const toggle = screen.getByRole("button", { name: /what i woke up to/i });
    assert.equal(present(screen.queryByText(/wake pack/i)), false);
    assert.equal(present(screen.queryByText("messages · fri 9:12am · " + (PACK.length / 1000).toFixed(1) + "K chars")), true);
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    assert.equal(absent(screen.queryByText(/raw pack line/)), true);
    assert.equal(absent(screen.queryByText("Since your last wake (1h): 3 new messages.")), false, "a preview line shows while folded");
  });

  it("expands to the raw text and folds back", () => {
    render(<PackCard text={PACK} />);
    const toggle = screen.getByRole("button", { name: /what i woke up to/i });
    fireEvent.click(toggle);
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    const raw = screen.getByTestId("pack-raw");
    assert.equal(raw.tagName, "PRE");
    assert.ok(raw.textContent?.includes("raw pack line"));
    assert.equal(toggle.getAttribute("aria-controls"), raw.id);
    fireEvent.click(toggle);
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    assert.equal(absent(screen.queryByTestId("pack-raw")), true);
  });
});

describe("PackCard kinds", () => {
  it("names a pulse by what it was for", () => {
    const pulse = `[EXPRESSION PULSE] thu 9:00pm. Nothing moved.\n${"x\n".repeat(700)}`;
    render(<PackCard text={pulse} />);
    assert.equal(present(screen.queryByRole("button", { name: /what my screens showed me/i })), true);
  });
});
