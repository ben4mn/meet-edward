"use client";

// The sticky-sidebar rule for Today's right pane on the desktop (UX pass
// 2026-09-26): a pane that fits sticks under a small gap; a taller one
// sticks by its bottom, so nothing in it is ever out of reach. Pure
// `stickyTop` is pinned in __tests__/todaySticky.test.ts.

import { useEffect, useState, type RefObject } from "react";

export const STICKY_GAP_PX = 32;

export function stickyTop(containerHeight: number, paneHeight: number, gap = STICKY_GAP_PX): number {
  if (!(containerHeight > 0) || !(paneHeight > 0)) return gap;
  return Math.min(gap, containerHeight - paneHeight - gap);
}

/** `top` (px) for a sticky pane inside a scroll container. */
export function useStickyTop(container: RefObject<HTMLElement>, pane: RefObject<HTMLElement>): number {
  const [top, setTop] = useState(STICKY_GAP_PX);
  useEffect(() => {
    const c = container.current;
    const p = pane.current;
    if (!c || !p || typeof ResizeObserver === "undefined") return;
    const measure = () => setTop(stickyTop(c.clientHeight, p.offsetHeight));
    const ro = new ResizeObserver(measure);
    ro.observe(c);
    ro.observe(p);
    measure();
    return () => ro.disconnect();
  }, [container, pane]);
  return top;
}
