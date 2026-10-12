/** Pure helpers behind usePresenceBeacon — unit-tested in __tests__/presenceBeacon.test.ts. */

export type BeaconKind = "mac-web" | "iphone-pwa" | "display" | "pi-hub";
export type BeaconState = "active" | "idle" | "hidden";

/** Past this, a visible tab with no input counts as idle (mirrors the backend's ACTIVE_IDLE_MAX_SECONDS). */
export const IDLE_AFTER_MS = 120_000;

export function detectKind(userAgent: string, standalone?: boolean): BeaconKind {
  const ua = userAgent || "";
  if (/iPhone|iPad|iPod/i.test(ua) || standalone) return "iphone-pwa";
  return "mac-web";
}

export function beaconState(input: { hidden: boolean; msSinceInput: number; alwaysActive: boolean }): {
  state: BeaconState;
  idle_seconds: number;
} {
  const idle_seconds = Math.max(0, Math.floor(input.msSinceInput / 1000));
  if (input.alwaysActive) return { state: "active", idle_seconds: 0 };
  if (input.hidden) return { state: "hidden", idle_seconds };
  return { state: input.msSinceInput >= IDLE_AFTER_MS ? "idle" : "active", idle_seconds };
}

/** One id per browser profile so a reload does not look like a new device. */
export function stableDeviceId(kind: BeaconKind, storage: Pick<Storage, "getItem" | "setItem">): string {
  const key = "edward.presence.device_id";
  try {
    const existing = storage.getItem(key);
    if (existing) return existing;
    const fresh = `${kind}-${Math.random().toString(36).slice(2, 8)}`;
    storage.setItem(key, fresh);
    return fresh;
  } catch {
    return `${kind}-ephemeral`;
  }
}
