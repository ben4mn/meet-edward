// R/G red for DisplayAvatar swapping to Pane internals while keeping
// the state-driven prop contract used by StageEdward and friends.
//
// Contract: passing `state` translates via paneActivity into the right
// activity class on .pane-avatar.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render } from "@testing-library/react";

import { DisplayAvatar } from "../app/(display)/display/components/DisplayAvatar.tsx";

function activityClass(container: HTMLElement): string | null {
  const pane = container.querySelector(".pane-avatar") as HTMLElement | null;
  if (!pane) return null;
  return Array.from(pane.classList).find((c) => c.startsWith("act-")) ?? null;
}

describe("DisplayAvatar (Pane edition)", () => {
  it("default state idle → act-stand", () => {
    const { container } = render(<DisplayAvatar />);
    assert.equal(activityClass(container as any), "act-stand");
  });

  it("thinking → act-think", () => {
    const { container } = render(<DisplayAvatar state="thinking" />);
    assert.equal(activityClass(container as any), "act-think");
  });

  it("speaking → act-speak", () => {
    const { container } = render(<DisplayAvatar state="speaking" />);
    assert.equal(activityClass(container as any), "act-speak");
  });

  it("listening → act-listen", () => {
    const { container } = render(<DisplayAvatar state="listening" />);
    assert.equal(activityClass(container as any), "act-listen");
  });

  it("looking-left → act-look", () => {
    const { container } = render(<DisplayAvatar state="looking-left" />);
    assert.equal(activityClass(container as any), "act-look");
  });

  it("respects size prop", () => {
    const { container } = render(<DisplayAvatar size={420} />);
    const pane = container.querySelector(".pane-avatar") as HTMLElement;
    assert.equal(pane.style.width, "420px");
  });
});

// ---------------------------------------------------------------------------
// States route through Edward's moods (lib/edwardMood.ts)
// ---------------------------------------------------------------------------

function pane(container: HTMLElement): HTMLElement {
  return container.querySelector(".pane-avatar") as HTMLElement;
}

describe("DisplayAvatar moods", () => {
  it("maps each state onto a mood", () => {
    for (const [state, mood] of [
      ["idle", "idle"],
      ["thinking", "think"],
      ["speaking", "speak"],
      ["listening", "listen"],
      ["looking-left", "idle"],
    ] as const) {
      const { container } = render(<DisplayAvatar state={state} />);
      assert.equal(pane(container).dataset.mood, mood, state);
    }
  });

  it("looking-* is a gaze now, not a mirrored body", () => {
    for (const [state, gaze] of [
      ["looking-left", "left"],
      ["looking-right", "right"],
      ["looking-up", "up"],
    ] as const) {
      const el = pane(render(<DisplayAvatar state={state} />).container);
      assert.equal(el.dataset.gaze, gaze, state);
      assert.ok(!el.classList.contains("face-left"), `${state} mirrored`);
    }
    assert.equal(pane(render(<DisplayAvatar state="idle" />).container).dataset.gaze, undefined);
  });

  it("is the live Edward on /screen: he breathes", () => {
    assert.ok(pane(render(<DisplayAvatar />).container).classList.contains("is-live"));
  });

  it("can play a beat (e.g. a nod when poked)", () => {
    const el = pane(render(<DisplayAvatar beat="nod" />).container);
    assert.equal(el.dataset.beat, "nod");
  });

  it("passes the accent through as his light", () => {
    const el = pane(render(<DisplayAvatar accent="#3b82f6" />).container);
    assert.equal(el.style.getPropertyValue("--pane-accent"), "#3b82f6");
  });
});

// ---------------------------------------------------------------------------
// The screen passes his real mood and label (2026-09-26): the legacy
// `state` only knew idle/thinking/speaking/listening, so waiting on Ellis,
// resting on a spent quota or offline could not be drawn at all.
// ---------------------------------------------------------------------------

describe("DisplayAvatar with a mood from real signals", () => {
  it("a mood wins over the legacy state", () => {
    const el = pane(render(<DisplayAvatar state="idle" mood="attention" />).container);
    assert.equal(el.dataset.mood, "attention");
  });

  it("every mood can be drawn", () => {
    for (const mood of ["work", "resting", "sleepy", "offline"] as const) {
      assert.equal(pane(render(<DisplayAvatar mood={mood} />).container).dataset.mood, mood);
    }
  });

  it("his status is his accessible name", () => {
    const el = pane(render(<DisplayAvatar mood="think" ariaLabel="Edward · Thinking · morning wake" onClick={() => {}} />).container);
    assert.equal(el.getAttribute("aria-label"), "Edward · Thinking · morning wake");
  });

  it("hands out the life handle so the screen can make him glance", () => {
    const lifeRef = { current: null } as React.MutableRefObject<import("../lib/useEdwardLife.ts").EdwardLife | null>;
    const el = pane(render(<DisplayAvatar mood="idle" lifeRef={lifeRef} />).container);
    assert.ok(lifeRef.current, "expected a life handle");
    lifeRef.current!.glance("up", 500);
    assert.equal(el.dataset.glance, "up");
  });
});
