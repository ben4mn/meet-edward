"use client";

import { useEffect, useRef } from "react";

/**
 * Tell Edward this screen exists and whether someone is using it.
 *
 * Why: nothing on the backend knew whether the owner was at a keyboard, so
 * triage acted on texts they were already answering. A beacon every 30s
 * (and immediately on visibility change) is enough for the settle window
 * to stretch when he is clearly here, and for an interrupt to land on the
 * screen he is looking at.
 *
 * Pure decisions (state + idle from raw inputs) live in presenceBeacon.ts
 * and are unit-tested; this hook only wires timers and listeners.
 */
import { beaconState, detectKind, stableDeviceId, type BeaconKind } from "./presenceBeacon";

const INTERVAL_MS = 30_000;

export function usePresenceBeacon(opts: { kind?: BeaconKind; alwaysActive?: boolean } = {}) {
  const lastInput = useRef<number>(Date.now());

  useEffect(() => {
    if (typeof window === "undefined") return;
    const kind = opts.kind ?? detectKind(navigator.userAgent, (navigator as { standalone?: boolean }).standalone);
    const deviceId = stableDeviceId(kind, localStorage);
    const deviceName = `${kind} · ${navigator.platform || "browser"}`;

    const bump = () => {
      lastInput.current = Date.now();
    };
    const send = () => {
      const { state, idle_seconds } = beaconState({
        hidden: document.visibilityState === "hidden",
        msSinceInput: Date.now() - lastInput.current,
        alwaysActive: !!opts.alwaysActive,
      });
      const body = JSON.stringify({ device_id: deviceId, device_name: deviceName, kind, state, idle_seconds });
      // keepalive lets the "hidden" beacon out even as the tab is backgrounded.
      fetch("/api/presence/beacon", {
        method: "POST",
        credentials: "include",
        keepalive: true,
        headers: { "Content-Type": "application/json" },
        body,
      }).catch(() => {});
    };

    window.addEventListener("pointermove", bump, { passive: true });
    window.addEventListener("keydown", bump);
    window.addEventListener("pointerdown", bump, { passive: true });
    document.addEventListener("visibilitychange", send);
    send();
    const timer = window.setInterval(send, INTERVAL_MS);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("pointermove", bump);
      window.removeEventListener("keydown", bump);
      window.removeEventListener("pointerdown", bump);
      document.removeEventListener("visibilitychange", send);
    };
  }, [opts.kind, opts.alwaysActive]);
}
