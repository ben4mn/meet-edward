// R/G red for PaneSvg — the shared Pane buddy SVG component.
//
// Two contracts we care about:
// 1. The root element carries class "pane-avatar" plus the activity
//    class from paneActivity, so CSS can target it.
// 2. The component sizes itself to the `size` prop and renders the
//    Pane SVG viewBox unchanged ("0 0 36 38" — copied from PaneStreet).

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render } from "@testing-library/react";

import { PaneSvg } from "../components/pane/PaneSvg.tsx";

describe("PaneSvg", () => {
  it("renders with the pane-avatar class and the activity class", () => {
    const { container } = render(<PaneSvg size={64} activity="act-think" />);
    const root = container.firstElementChild as HTMLElement;
    assert.ok(root.classList.contains("pane-avatar"));
    assert.ok(root.classList.contains("act-think"));
  });

  it("renders the Pane SVG with the 36x38 viewBox", () => {
    const { container } = render(<PaneSvg size={64} activity="act-stand" />);
    const svg = container.querySelector("svg");
    assert.equal(svg?.getAttribute("viewBox"), "0 0 36 38");
  });

  it("respects the size prop", () => {
    const { container } = render(<PaneSvg size={128} activity="act-stand" />);
    const root = container.firstElementChild as HTMLElement;
    assert.equal(root.style.width, "128px");
    assert.equal(root.style.height, "128px");
  });

  it("face-left flips horizontally via class, not transform inline", () => {
    const { container } = render(
      <PaneSvg size={64} activity="act-stand" facingLeft />,
    );
    const root = container.firstElementChild as HTMLElement;
    assert.ok(root.classList.contains("face-left"));
  });
});

// ---------------------------------------------------------------------------
// Geometry pin — walkEngine plants his feet from these numbers
// ---------------------------------------------------------------------------
//
// hub/lib/walkEngine.ts derives the hub companion's floor offset from
// PANE_VIEWBOX_HEIGHT (38) and PANE_FOOT_Y (34). Redrawing Edward must not
// move his soles or change the canvas height, or he hovers on the Pi.

function footBottom(root: Element, legClass: string): number {
  const rects = Array.from(root.querySelectorAll(`.${legClass} rect`));
  assert.ok(rects.length > 0, `expected rects inside .${legClass}`);
  return Math.max(
    ...rects.map(
      (r) => Number(r.getAttribute("y")) + Number(r.getAttribute("height")),
    ),
  );
}

describe("PaneSvg geometry", () => {
  it("keeps a 38-unit-tall viewBox", () => {
    const { container } = render(<PaneSvg size={96} activity="act-stand" />);
    const vb = container.querySelector("svg")!.getAttribute("viewBox")!;
    assert.equal(Number(vb.split(/\s+/)[3]), 38);
  });

  it("plants both foot pads at y=34", () => {
    const { container } = render(<PaneSvg size={96} activity="act-stand" />);
    assert.equal(footBottom(container, "robot-leg-left"), 34);
    assert.equal(footBottom(container, "robot-leg-right"), 34);
  });

  it("grows the antenna from the joint every pivot rule uses", () => {
    const { container } = render(<PaneSvg size={96} activity="act-stand" />);
    const path = container.querySelector(".robot-antenna path");
    assert.equal(path?.getAttribute("d"), "M18 6.5 Q18 3 17 1.5");
  });
});

// ---------------------------------------------------------------------------
// The character system: moods, gaze, variants, life
// ---------------------------------------------------------------------------

function root(container: HTMLElement): HTMLElement {
  return container.querySelector(".pane-avatar") as HTMLElement;
}

describe("PaneSvg moods", () => {
  it("carries the mood as data-mood", () => {
    const { container } = render(<PaneSvg size={64} mood="work" />);
    assert.equal(root(container).dataset.mood, "work");
  });

  it("reads a legacy activity as a mood so /screen and the hub keep working", () => {
    for (const [activity, mood] of [
      ["act-stand", "idle"],
      ["act-think", "think"],
      ["act-speak", "speak"],
      ["act-listen", "listen"],
      ["act-look", "idle"],
    ] as const) {
      const { container } = render(<PaneSvg size={64} activity={activity} />);
      assert.equal(root(container).dataset.mood, mood, activity);
      assert.ok(root(container).classList.contains(activity));
    }
  });

  it("keeps an act-* class for a mood given directly", () => {
    const { container } = render(<PaneSvg size={64} mood="listen" />);
    assert.ok(root(container).classList.contains("act-listen"));
  });

  it("defaults to idle", () => {
    const { container } = render(<PaneSvg size={64} />);
    assert.equal(root(container).dataset.mood, "idle");
    assert.ok(root(container).classList.contains("act-stand"));
  });

  it("sets data-gaze only when asked", () => {
    const a = render(<PaneSvg size={64} gaze="left" />);
    assert.equal(root(a.container).dataset.gaze, "left");
    const b = render(<PaneSvg size={64} />);
    assert.equal(root(b.container).dataset.gaze, undefined);
  });

  it("marks lip-sync so the CSS talk loop steps aside", () => {
    const { container } = render(<PaneSvg size={64} mood="speak" lipsync />);
    assert.equal(root(container).dataset.lipsync, "");
  });
});

describe("PaneSvg liveness", () => {
  it("only a live Edward gets .is-live", () => {
    const live = render(<PaneSvg size={64} live />);
    assert.ok(root(live.container).classList.contains("is-live"));
    const still = render(<PaneSvg size={64} />);
    assert.ok(!root(still.container).classList.contains("is-live"));
  });

  it("the hub's legacy pane-animated opt-in still means live", () => {
    const { container } = render(<PaneSvg size={64} activity="act-stand" className="pane-animated" />);
    assert.ok(root(container).classList.contains("is-live"));
  });

  it("animated={false} is fully still", () => {
    const { container } = render(<PaneSvg size={64} animated={false} />);
    assert.ok(root(container).classList.contains("pane-static"));
  });
});

describe("PaneSvg variants", () => {
  it("full is the whole robot on the 36x38 canvas", () => {
    const { container } = render(<PaneSvg size={64} />);
    assert.equal(root(container).dataset.variant, "full");
    assert.ok(container.querySelector(".robot-leg-left"));
    assert.ok(container.querySelector(".robot-shadow"));
  });

  it("bust crops to head and shoulders for small sizes", () => {
    const { container } = render(<PaneSvg size={20} variant="bust" />);
    assert.equal(root(container).dataset.variant, "bust");
    const outer = container.querySelector("svg")!;
    const [x, y, w, h] = outer.getAttribute("viewBox")!.split(/\s+/).map(Number);
    assert.ok(w === h, "bust crop is square");
    assert.ok(w >= 26 && w <= 28, `crop width ${w}`);
    assert.ok(x > 4 && x < 5 && y < 0, `crop origin ${x},${y}`);
    assert.equal(container.querySelector(".robot-leg-left"), null);
    assert.equal(container.querySelector(".robot-shadow"), null);
  });

  it("the bust keeps the rig on its own 0 0 36 38 canvas so pivots don't move", () => {
    const { container } = render(<PaneSvg size={20} variant="bust" />);
    const inner = container.querySelector("svg svg");
    assert.equal(inner?.getAttribute("viewBox"), "0 0 36 38");
    assert.ok(inner?.querySelector(".robot-head"));
  });
});

describe("PaneSvg drawing", () => {
  it("draws every part the CSS drives", () => {
    const { container } = render(<PaneSvg size={96} />);
    for (const cls of [
      "robot-body", "robot-upper", "robot-antenna", "robot-antenna-tip",
      "robot-antenna-glow", "robot-antenna-ring", "robot-head", "robot-visor",
      "robot-scan", "robot-glint", "robot-face", "robot-eyes", "robot-eye-l",
      "robot-eye-r", "robot-eye-glow", "robot-eye-happy", "robot-eye-wince",
      "robot-mouth", "robot-mouth-smile", "robot-mouth-grin", "robot-mouth-flat",
      "robot-mouth-open", "robot-core", "robot-arm-l", "robot-arm-r",
      "robot-leg-left", "robot-leg-right", "robot-shadow",
    ]) {
      assert.ok(container.querySelector(`.${cls}`), `missing .${cls}`);
    }
  });

  it("the antenna rides the head, so a nod or a tilt carries it", () => {
    const { container } = render(<PaneSvg size={96} />);
    assert.ok(container.querySelector(".robot-head .robot-antenna"), "antenna outside the head");
  });

  it("the head turns on a neck group, so a mood pose and a beat compose", () => {
    const { container } = render(<PaneSvg size={96} />);
    assert.ok(container.querySelector(".robot-upper .robot-neck .robot-head"));
  });

  it("eyes open and close inside a group that looks around", () => {
    const { container } = render(<PaneSvg size={96} />);
    // gaze moves .robot-face; mood openness scales .robot-eyes; a blink
    // scales each eye — three transforms that never fight over one element.
    assert.ok(container.querySelector(".robot-face .robot-eyes .robot-eye-l"));
    assert.ok(container.querySelector(".robot-face .robot-eyes .robot-eye-r"));
  });

  it("waves with a raised arm that grows from the shoulder, so the hand never floats off", () => {
    const { container } = render(<PaneSvg size={96} />);
    const arm = container.querySelector(".robot-upper .robot-wave-arm");
    assert.ok(arm, "no raised arm for the wave");
    const stalk = arm!.querySelector("path");
    assert.ok(/^M24\.8 23\.4\b/.test(stalk?.getAttribute("d") ?? ""), "the stalk must start at the shoulder pivot");
  });

  it("gives every SVG id a per-instance name and resolves every url(#…)", () => {
    const { container } = render(
      <div>
        <PaneSvg size={64} />
        <PaneSvg size={64} />
      </div>,
    );
    const ids = Array.from(container.querySelectorAll("[id]")).map((e) => e.id);
    assert.ok(ids.length > 0, "expected defs with ids");
    assert.equal(new Set(ids).size, ids.length, `duplicate ids: ${ids}`);
    for (const el of Array.from(container.querySelectorAll("*"))) {
      for (const attr of Array.from(el.attributes)) {
        const m = attr.value.match(/url\(#([^)]+)\)/);
        if (!m) continue;
        assert.ok(!/:/.test(m[1]), `id ${m[1]} contains a colon`);
        const svg = el.closest(".pane-avatar")!;
        assert.ok(svg.querySelector(`[id="${m[1]}"]`), `url(#${m[1]}) does not resolve`);
      }
    }
  });
});

describe("PaneSvg accessibility", () => {
  it("is decorative without a label", () => {
    const { container } = render(<PaneSvg size={64} />);
    assert.equal(root(container).getAttribute("aria-hidden"), "true");
  });

  it("speaks its label as an image", () => {
    const { container } = render(<PaneSvg size={64} ariaLabel="Edward · Resting" />);
    assert.equal(root(container).getAttribute("role"), "img");
    assert.equal(root(container).getAttribute("aria-label"), "Edward · Resting");
  });

  it("a clickable Edward is a button named Edward by default", () => {
    const { container } = render(<PaneSvg size={64} onClick={() => {}} />);
    assert.equal(root(container).getAttribute("role"), "button");
    assert.equal(root(container).getAttribute("aria-label"), "Edward");
  });
});

describe("PaneSvg beats", () => {
  it("fires a beat prop onto the root as data-beat", () => {
    const { container } = render(<PaneSvg size={64} beat="nod" />);
    assert.equal(root(container).dataset.beat, "nod");
  });

  it("a still Edward never plays beats", () => {
    const { container } = render(<PaneSvg size={64} beat="nod" animated={false} />);
    assert.equal(root(container).dataset.beat, undefined);
  });
});
