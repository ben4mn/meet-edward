"use client";

import { usePresenceBeacon } from "@/lib/usePresenceBeacon";
import { HubCanvas } from "./HubCanvas";
import { useWho } from "../lib/useWho";
import { themeFor } from "../lib/whoTheme";

// ADAPT: the hub is laid out for one device, a 1280x720 touchscreen kiosk at the pet station.
//   How it works here: a Raspberry Pi with a 7" display runs Chromium in kiosk mode against /hub
//   (a thin client; over plain-HTTP LAN it needs --unsafely-treat-insecure-origin-as-secure for the mic and Wake Lock).
//   To adapt: any browser in kiosk mode on /hub works; for another screen size, ask Claude to rework hub.css.

/**
 * The hub's outer shell, tinted for whoever is currently logging.
 *
 * Every screen goes through this so the accent follows the active person
 * consistently — a per-page `data-who` would drift the moment someone
 * added a screen and forgot.
 */
export function HubRoot({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  const { who, people } = useWho();
  usePresenceBeacon({ kind: "pi-hub" });

  return (
    <div
      className={className ? `hub-root ${className}` : "hub-root"}
      data-who={themeFor(who, people)}
    >
      {children}
      <HubCanvas />
    </div>
  );
}
