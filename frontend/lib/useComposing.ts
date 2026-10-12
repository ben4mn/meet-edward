"use client";

import { useEffect, useState } from "react";

const EVENT = "edward:composing";
let composing = false;
let blurTimer: ReturnType<typeof setTimeout> | null = null;

/** Called by the composer on focus/blur. Broadcasts to the shell. Blur is
 *  debounced so tapping Send (which blurs the textarea for a moment) does
 *  not flash the header and dock back in. */
export function setComposing(v: boolean) {
  if (blurTimer) {
    clearTimeout(blurTimer);
    blurTimer = null;
  }
  const apply = () => {
    if (composing === v) return;
    composing = v;
    if (typeof window !== "undefined") window.dispatchEvent(new window.CustomEvent(EVENT, { detail: v }));
  };
  if (v) apply();
  else blurTimer = setTimeout(apply, 250);
}

export function useComposing(): boolean {
  const [v, setV] = useState(composing);
  useEffect(() => {
    const on = (e: Event) => setV(Boolean((e as CustomEvent).detail));
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  return v;
}
