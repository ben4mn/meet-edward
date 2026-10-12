"use client";

// Set type that fits its box (the stage, 2026-09-28). Edward writes words of
// any length into a box of any size; the stylesheet gives the size he asked
// for, and this steps the text down (8 % at a time, at most 14 steps) until
// its box no longer overflows — once per text and per resize, never a loop.

import { useLayoutEffect, type RefObject } from "react";

const STEP = 0.92;
const MAX_STEPS = 14;
const MIN_PX = 12;

function overflows(box: HTMLElement): boolean {
  // Content pushed out at the top (a bottom-aligned lane) is not in
  // scrollHeight; the first child's own height is.
  const inner = box.firstElementChild as HTMLElement | null;
  return (
    box.scrollHeight > box.clientHeight + 1 ||
    box.scrollWidth > box.clientWidth + 1 ||
    (!!inner && inner.offsetHeight > box.clientHeight + 1)
  );
}

export function fitText(box: HTMLElement, text: HTMLElement): void {
  text.style.fontSize = "";
  if (!(box.clientHeight > 0)) return; // not laid out (tests, hidden tab)
  let px = parseFloat(getComputedStyle(text).fontSize);
  for (let i = 0; i < MAX_STEPS && overflows(box) && px > MIN_PX; i++) {
    px = Math.max(MIN_PX, px * STEP);
    text.style.fontSize = `${px.toFixed(1)}px`;
  }
}

export function useFitText(
  boxRef: RefObject<HTMLElement | null>,
  textRef: RefObject<HTMLElement | null>,
  key: string,
): void {
  useLayoutEffect(() => {
    const box = boxRef.current;
    const text = textRef.current;
    if (!box || !text) return;
    fitText(box, text);
    if (typeof ResizeObserver === "undefined") return;
    let last = `${box.clientWidth}x${box.clientHeight}`;
    const ro = new ResizeObserver(() => {
      const now = `${box.clientWidth}x${box.clientHeight}`;
      if (now === last) return;
      last = now;
      fitText(box, text);
    });
    ro.observe(box);
    return () => ro.disconnect();
  }, [boxRef, textRef, key]);
}
