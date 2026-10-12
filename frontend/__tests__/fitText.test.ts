// Set type that fits its box (2026-09-28): Edward's words step down until
// they no longer overflow — never below 12 px, never more than 14 steps.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { fitText } from "../app/(display)/display/hooks/useFitText.ts";

function fixture(height: number, linesAt: (px: number) => number) {
  const style = document.createElement("style");
  style.textContent = ".fit-t { font-size: 100px; }";
  document.head.appendChild(style);
  const box = document.createElement("div");
  const text = document.createElement("div");
  text.className = "fit-t";
  box.appendChild(text);
  document.body.appendChild(box);
  const px = () => parseFloat(text.style.fontSize || getComputedStyle(text).fontSize);
  Object.defineProperty(box, "clientHeight", { get: () => height });
  Object.defineProperty(box, "clientWidth", { get: () => 800 });
  Object.defineProperty(box, "scrollWidth", { get: () => 800 });
  Object.defineProperty(box, "scrollHeight", { get: () => linesAt(px()) * px() });
  return { box, text, px, done: () => { box.remove(); style.remove(); } };
}

describe("fitText", () => {
  it("steps the words down until they fit", () => {
    const f = fixture(150, (px) => (px > 60 ? 2 : 1) + 1); // two lines and a caption at size, fewer when smaller
    fitText(f.box, f.text);
    assert.ok(f.box.scrollHeight <= 150, `still ${f.box.scrollHeight}`);
    assert.ok(f.px() >= 40, `shrank too far: ${f.px()}`);
    f.done();
  });

  it("leaves words that fit alone", () => {
    const f = fixture(300, () => 2);
    fitText(f.box, f.text);
    assert.equal(f.text.style.fontSize, "");
    f.done();
  });

  it("never below 12 px, however long", () => {
    const f = fixture(10, () => 50);
    fitText(f.box, f.text);
    assert.ok(f.px() >= 12);
    f.done();
  });

  it("an unlaid-out box is left alone", () => {
    const f = fixture(0, () => 5);
    fitText(f.box, f.text);
    assert.equal(f.text.style.fontSize, "");
    f.done();
  });
});
