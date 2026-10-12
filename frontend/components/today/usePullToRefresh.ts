"use client";

// Pull to refresh on touch (UX pass 2026-09-26): only from the very top of
// the scroll container, only for a downward drag. The numbers are pure in
// pullToRefresh.ts; this binds them to touch events on the container.

import { useEffect, useRef, useState, type RefObject } from "react";
import { pullOffset, releaseRefreshes } from "./pullToRefresh";

export function usePullToRefresh(ref: RefObject<HTMLElement>, onRefresh: () => Promise<unknown> | void) {
  const [offset, setOffset] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const start = useRef<{ y: number; x: number } | null>(null);
  const offsetRef = useRef(0);
  const refreshRef = useRef(onRefresh);
  refreshRef.current = onRefresh;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onStart = (e: TouchEvent) => {
      if (el.scrollTop > 0 || e.touches.length !== 1) {
        start.current = null;
        return;
      }
      start.current = { y: e.touches[0].clientY, x: e.touches[0].clientX };
    };
    const onMove = (e: TouchEvent) => {
      if (!start.current) return;
      const dy = e.touches[0].clientY - start.current.y;
      const dx = Math.abs(e.touches[0].clientX - start.current.x);
      if (dy <= 0 || el.scrollTop > 0 || dx > dy) {
        if (offsetRef.current) {
          offsetRef.current = 0;
          setOffset(0);
          setDragging(false);
        }
        return;
      }
      const next = pullOffset(dy);
      offsetRef.current = next;
      setDragging(true);
      setOffset(next);
    };
    const onEnd = () => {
      if (!start.current) return;
      start.current = null;
      const fire = releaseRefreshes(offsetRef.current);
      offsetRef.current = 0;
      setDragging(false);
      setOffset(0);
      if (fire) {
        setRefreshing(true);
        Promise.resolve(refreshRef.current()).finally(() => setRefreshing(false));
      }
    };
    el.addEventListener("touchstart", onStart, { passive: true });
    el.addEventListener("touchmove", onMove, { passive: true });
    el.addEventListener("touchend", onEnd);
    el.addEventListener("touchcancel", onEnd);
    return () => {
      el.removeEventListener("touchstart", onStart);
      el.removeEventListener("touchmove", onMove);
      el.removeEventListener("touchend", onEnd);
      el.removeEventListener("touchcancel", onEnd);
    };
  }, [ref]);

  return { offset, dragging, refreshing };
}
