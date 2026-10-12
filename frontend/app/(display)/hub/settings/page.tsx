"use client";

// Controls for the panel, not for Edward.
//
// The kiosk has no chrome — no address bar, no tabs, no way out of
// Chromium — which is the right call for a screen by the pet station but
// leaves you with nothing to do when it wedges, and no way to shut it up
// at night. This screen is that missing set of buttons.
//
// Edward's own settings (model, temperature, screensaver pacing) stay on
// /hub/health; skills and anything destructive stay off the hub entirely
// until it has a PIN gate. The note at the bottom says so out loud rather
// than leaving people hunting for a toggle that was never here.

import "../hub.css";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { useBuildReloadHold } from "@/lib/buildReload";
import { useWakeLock } from "../../display/hooks/useWakeLock";
import { HubHeader } from "../components/HubHeader";
import { HubRoot } from "../components/HubRoot";
import {
  DEVICE_ACTIONS,
  NOTHING_ARMED,
  expire,
  isArmed,
  tap,
  type ConfirmState,
  type DeviceAction,
} from "../lib/deviceActions";
import { useIdleRedirect } from "../lib/useIdleRedirect";
import { useWho } from "../lib/useWho";

export default function HubSettingsPage() {
  const router = useRouter();
  const { who, people } = useWho();

  // Blacking the screen means giving up the wake lock — holding it would
  // keep the backlight on behind a black page, which is the one thing
  // "Screen off" is for.
  const [screenOff, setScreenOff] = useState(false);
  useWakeLock(!screenOff);

  // While the screen is off, the idle redirect would bounce us to the
  // bright photo screensaver in two minutes — exactly backwards.
  useIdleRedirect(screenOff ? 24 * 60 * 60_000 : 120_000);

  // And a new deploy's reload waits (lib/buildReload.ts): nobody touches a
  // blacked-out screen, so it would come back as this bright page with the
  // wake lock taken again, then the screensaver — lit all night
  // (2026-09-27 review).
  useBuildReloadHold(screenOff);

  // Waking goes home, not back here. Whoever blacked the screen is rarely
  // whoever wakes it, and greeting them with a big "Exit hub" button is a
  // poor way to say good morning. Prefetched so the wake feels instant.
  useEffect(() => {
    if (screenOff) router.prefetch("/hub");
  }, [screenOff, router]);

  const [confirm, setConfirm] = useState<ConfirmState>(NOTHING_ARMED);
  const [now, setNow] = useState(() => Date.now());

  // Ticks only while something is armed, so the "tap again" wording drops
  // back on its own when nobody follows through and the button never sits
  // armed in front of the next person. Expiring the state (rather than
  // just letting isArmed answer false) is what stops this timer: leave the
  // record set and the kiosk re-renders twice a second until it's reloaded.
  useEffect(() => {
    if (!confirm.armed) return;
    const id = setInterval(() => {
      const t = Date.now();
      setNow(t);
      setConfirm((c) => expire(c, t));
    }, 500);
    return () => clearInterval(id);
  }, [confirm.armed]);

  const run = useCallback(
    (action: DeviceAction) => {
      switch (action.kind) {
        case "sleep":
          router.replace("/hub/idle");
          break;
        case "screen-off":
          setScreenOff(true);
          break;
        case "reload":
          window.location.reload();
          break;
        case "leave":
          // Kiosk: this quits Chromium and drops to the desktop, which is
          // the only route to a shutdown from the panel itself. systemd's
          // Restart=on-failure reads the clean exit as deliberate and
          // leaves it down; the Edward Hub desktop icon brings it back.
          window.close();
          // A normal browser refuses to close a window it did not open, so
          // fall back to the full app when close was ignored.
          setTimeout(() => {
            window.location.href = "/chat";
          }, 1200);
          break;
      }
    },
    [router],
  );

  const onTap = (action: DeviceAction) => {
    const t = Date.now();
    setNow(t);
    const next = tap(confirm, action.id, action.confirm, t);
    setConfirm(next.state);
    if (next.fire) run(action);
  };

  if (screenOff) {
    return (
      <div
        className="hub-blackout"
        role="button"
        tabIndex={0}
        aria-label="Screen off — tap to wake"
        onPointerDown={() => router.replace("/hub")}
        onKeyDown={() => router.replace("/hub")}
      />
    );
  }

  return (
    <HubRoot>
      <HubHeader title="This screen" />

      <main className="hub-two-col">
        <section className="hub-card">
          <h2 className="hub-card-title">Panel</h2>

          <div className="hub-device-actions">
            {DEVICE_ACTIONS.map((action) => {
              const armed = isArmed(confirm, action.id, now);
              return (
                <button
                  key={action.id}
                  className="hub-device-action"
                  data-armed={armed || undefined}
                  onClick={() => onTap(action)}
                >
                  <span className="hub-device-icon">{action.icon}</span>
                  <span className="hub-device-label">
                    {armed ? action.confirmLabel : action.label}
                  </span>
                  <span className="hub-device-hint">{action.hint}</span>
                </button>
              );
            })}
          </div>
        </section>

        <section className="hub-card">
          <h2 className="hub-card-title">Where the rest lives</h2>

          <div className="hub-setting-note">
            Edward&apos;s model, temperature and the screensaver&apos;s pacing are on{" "}
            <strong>Health</strong>. Photos are added from the full settings page
            on your phone — Settings → Hub Photos.
          </div>

          <div className="hub-setting-note">
            Restarting Edward himself isn&apos;t a button here. It would need this
            panel to be able to run commands on the Mac Edward runs on, and the hub shares
            one password with every other screen — that&apos;s the same reason
            skills and integrations aren&apos;t here either. <strong>Reload hub</strong>{" "}
            above restarts this screen, which is what a stuck kiosk actually needs.
          </div>

          {who ? (
            <div className="hub-setting-hint">
              Logging as {who}
              {people.length > 1 ? " · tap a name up top to switch" : ""}
            </div>
          ) : null}
        </section>
      </main>
    </HubRoot>
  );
}
