// R/G red for EdwardAvatar — Edward sized for the app.
//
// The old call sites (size keyword/number, animated, className) keep
// working. The character contract (design system §4): small (≤36px) is a
// still bust, big is the full figure and alive, only `live` breathes, and
// mood / beat / gaze / labels pass straight through to the Pane character.

import { describe, it, mock } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render } from "@testing-library/react";

import { EdwardAvatar } from "../components/EdwardAvatar.tsx";

describe("EdwardAvatar (Pane edition)", () => {
  it("renders a Pane-class element when used in size sm", () => {
    const { container } = render(<EdwardAvatar size="sm" />);
    const pane = container.querySelector(".pane-avatar");
    assert.ok(pane, "expected a .pane-avatar element");
    assert.equal((pane as HTMLElement).style.width, "32px");
  });

  it("respects numeric size", () => {
    const { container } = render(<EdwardAvatar size={48} />);
    const pane = container.querySelector(".pane-avatar") as HTMLElement;
    assert.equal(pane.style.width, "48px");
    assert.equal(pane.style.height, "48px");
  });

  it("default activity is act-stand", () => {
    const { container } = render(<EdwardAvatar />);
    const pane = container.querySelector(".pane-avatar") as HTMLElement;
    assert.ok(pane.classList.contains("act-stand"));
  });

  it("forwards className to the outer wrapper", () => {
    const { container } = render(<EdwardAvatar className="my-class" />);
    const wrap = container.firstElementChild as HTMLElement;
    assert.ok(wrap.classList.contains("my-class"));
  });
});

// ---------------------------------------------------------------------------
// The character props (design system §4)
// ---------------------------------------------------------------------------

function pane(container: HTMLElement): HTMLElement {
  return container.querySelector(".pane-avatar") as HTMLElement;
}

describe("EdwardAvatar sizes", () => {
  it("keywords grow: sm < md < lg <= xl (xl used to be smaller than lg)", () => {
    const px = (size: "sm" | "md" | "lg" | "xl") =>
      parseFloat(pane(render(<EdwardAvatar size={size} />).container).style.width);
    assert.ok(px("sm") < px("md"));
    assert.ok(px("md") < px("lg"));
    assert.ok(px("lg") <= px("xl"), `lg ${px("lg")} > xl ${px("xl")}`);
  });
});

describe("EdwardAvatar defaults by size", () => {
  it("a small Edward (≤36px) is a still bust", () => {
    const el = pane(render(<EdwardAvatar size={20} />).container);
    assert.equal(el.dataset.variant, "bust");
    assert.ok(el.classList.contains("pane-static"));
  });

  it("36px is still small", () => {
    const el = pane(render(<EdwardAvatar size={36} />).container);
    assert.equal(el.dataset.variant, "bust");
  });

  it("a big Edward is the full figure and alive (blinks, beats)", () => {
    const el = pane(render(<EdwardAvatar size={72} />).container);
    assert.equal(el.dataset.variant, "full");
    assert.ok(!el.classList.contains("pane-static"));
  });

  it("explicit variant and animated win over the size defaults", () => {
    const small = pane(render(<EdwardAvatar size={28} variant="full" animated />).container);
    assert.equal(small.dataset.variant, "full");
    assert.ok(!small.classList.contains("pane-static"));
    const big = pane(render(<EdwardAvatar size={96} variant="bust" animated={false} />).container);
    assert.equal(big.dataset.variant, "bust");
    assert.ok(big.classList.contains("pane-static"));
  });
});

describe("EdwardAvatar liveness", () => {
  it("only `live` breathes — `animated` alone never makes a second live Edward", () => {
    assert.ok(!pane(render(<EdwardAvatar size={96} animated />).container).classList.contains("is-live"));
    assert.ok(pane(render(<EdwardAvatar size={36} live />).container).classList.contains("is-live"));
  });

  it("a live small Edward is animated even though small ones default still", () => {
    const el = pane(render(<EdwardAvatar size={28} live />).container);
    assert.ok(!el.classList.contains("pane-static"));
  });
});

describe("EdwardAvatar mood, beat and gaze", () => {
  it("passes mood and gaze through", () => {
    const el = pane(render(<EdwardAvatar size={96} mood="work" gaze="down" />).container);
    assert.equal(el.dataset.mood, "work");
    assert.equal(el.dataset.gaze, "down");
    assert.ok(el.classList.contains("act-think"), "legacy class for work");
  });

  it("plays a beat when one is given", () => {
    const el = pane(render(<EdwardAvatar size={96} beat="wave" />).container);
    assert.equal(el.dataset.beat, "wave");
  });

  it("a still Edward ignores beats", () => {
    const el = pane(render(<EdwardAvatar size={20} beat="wave" />).container);
    assert.equal(el.dataset.beat, undefined);
  });

  it("replays the same beat when beatKey changes", () => {
    mock.timers.enable({ apis: ["setTimeout", "Date"], now: 5_000_000 });
    try {
      const { container, rerender } = render(<EdwardAvatar size={96} beat="nod" beatKey={1} />);
      const el = pane(container);
      mock.timers.tick(2000); // the first nod finishes and clears
      assert.equal(el.dataset.beat, undefined);
      rerender(<EdwardAvatar size={96} beat="nod" beatKey={2} />);
      assert.equal(el.dataset.beat, "nod");
    } finally {
      mock.timers.reset();
    }
  });
});

describe("EdwardAvatar labels", () => {
  it("title names him for tooltips and screen readers", () => {
    const el = pane(render(<EdwardAvatar size={36} title="Edward · Resting" />).container);
    assert.equal(el.getAttribute("title"), "Edward · Resting");
    assert.equal(el.getAttribute("aria-label"), "Edward · Resting");
    assert.equal(el.getAttribute("role"), "img");
  });

  it("aria-label alone labels without a tooltip", () => {
    const el = pane(render(<EdwardAvatar size={36} aria-label="Edward · Working · 3 tools" />).container);
    assert.equal(el.getAttribute("aria-label"), "Edward · Working · 3 tools");
    assert.equal(el.getAttribute("title"), null);
  });

  it("is decorative with no label", () => {
    const el = pane(render(<EdwardAvatar size={36} />).container);
    assert.equal(el.getAttribute("aria-hidden"), "true");
  });
});
