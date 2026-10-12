"use client";

import { useEffect, useState } from "react";
import { shouldHoldWakeLock } from "./wakeLockPolicy";

// Web Wake Lock — keeps the screen awake while it matters: the host Mac's
// monitor on /screen, and the iPhone while Edward is working on a chat
// (pass `enabled = isLoading`). Browsers auto-release the lock when the tab hides
// (or you switch desktops on macOS), so we re-acquire on every
// visibilitychange that brings the tab back to the foreground.
//
// `navigator.wakeLock` is gated by the browser to a secure context with
// an active foreground document — Chrome/Safari on a logged-in edward.example.com
// page satisfies both. If unavailable, this hook is a no-op.

export type WakeLockState = "active" | "released" | "unsupported" | "error";

interface MaybeWakeLock {
  request?: (type: "screen") => Promise<WakeLockSentinel>;
}

export function useWakeLock(enabled = true): WakeLockState {
  const [state, setState] = useState<WakeLockState>("released");

  useEffect(() => {
    if (!enabled) return;
    if (typeof navigator === "undefined") return;

    const wl = (navigator as Navigator & { wakeLock?: MaybeWakeLock }).wakeLock;
    if (!wl || typeof wl.request !== "function") {
      setState("unsupported");
      return;
    }

    let sentinel: WakeLockSentinel | null = null;
    let cancelled = false;

    const acquire = async () => {
      if (cancelled) return;
      if (!shouldHoldWakeLock({ working: enabled, visible: document.visibilityState === "visible" })) return;
      try {
        sentinel = await wl.request!("screen");
        setState("active");
        sentinel.addEventListener("release", () => {
          // The browser released it (tab hidden, system policy, etc.).
          // We'll re-acquire on the next visibilitychange.
          if (!cancelled) setState("released");
        });
      } catch (e) {
        // Common: NotAllowedError when the page hasn't had a user gesture
        // yet. We'll retry on the next visibility-visible event, which
        // typically fires right after the user clicks anywhere.
        console.warn("[wake-lock] request failed:", e);
        setState("error");
      }
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") acquire();
    };

    acquire();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      cancelled = true;
      setState("released");
      document.removeEventListener("visibilitychange", onVisibility);
      if (sentinel) {
        sentinel.release().catch(() => {});
      }
    };
  }, [enabled]);

  return state;
}
