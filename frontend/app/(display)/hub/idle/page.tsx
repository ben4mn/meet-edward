"use client";

// Idle screensaver: a photo album from ~/edward-photos, with an adaptive
// clock and Edward pottering about the frame. Any touch returns to the hub.
//
// The orbit/ambient view this replaced lives on at /display — that one is
// built for the always-on monitor of the Mac Edward runs on.

import "../hub.css";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { formatClock } from "@/lib/time";

import { useWakeLock } from "../../display/hooks/useWakeLock";
import { HubCanvas } from "../components/HubCanvas";
import { PhotoCompanion, pushDirection } from "../components/PhotoCompanion";
import { fetchHubSettings, fetchPhotos, photoUrl } from "../lib/hubApi";
import { inkForPhoto } from "../lib/sampleInk";
import {
  advance,
  initSlideshow,
  queueUpcoming,
  visiblePhoto,
  type SlideshowState,
} from "../lib/slideshow";
import type { HubSettings, PhotosResponse } from "../lib/types";

// Must match the .hub-slide opacity transition in hub.css.
const FADE_MS = 1600;

// Re-read the folder and settings occasionally so changes land without a
// reload — the hub can sit idle for days.
const RELIST_MS = 10 * 60_000;

const DEFAULT_SETTINGS: HubSettings = {
  slide_seconds: 30,
  transition: "kenburns",
  edward_presents: true,
};

export default function HubIdlePage() {
  useWakeLock(true);
  const router = useRouter();

  const [data, setData] = useState<PhotosResponse | null>(null);
  const [settings, setSettings] = useState<HubSettings>(DEFAULT_SETTINGS);
  const [show, setShow] = useState<SlideshowState>(() => initSlideshow([]));
  const [now, setNow] = useState(() => new Date());
  const [ink, setInk] = useState<"light" | "dark">("light");
  // Rises just before a slide changes so Edward can push it across.
  const [presenting, setPresenting] = useState(false);

  const seedRef = useRef(Math.floor(Math.random() * 100000) + 1);
  const photosRef = useRef<string[]>([]);

  // ---- wake on any interaction ----
  useEffect(() => {
    router.prefetch("/hub");
    const wake = () => router.replace("/hub");
    const opts = { passive: true } as const;
    window.addEventListener("pointerdown", wake, opts);
    window.addEventListener("keydown", wake);
    window.addEventListener("touchstart", wake, opts);
    return () => {
      window.removeEventListener("pointerdown", wake);
      window.removeEventListener("keydown", wake);
      window.removeEventListener("touchstart", wake);
    };
  }, [router]);

  // ---- album + settings ----
  const load = useCallback(async () => {
    try {
      const [photos, cfg] = await Promise.all([
        fetchPhotos(seedRef.current),
        fetchHubSettings().catch(() => DEFAULT_SETTINGS),
      ]);
      setSettings(cfg);
      setData((prev) => {
        const changed =
          !prev || prev.photos.join("|") !== photos.photos.join("|");
        if (changed) {
          photosRef.current = photos.photos;
          setShow(initSlideshow(photos.photos));
        }
        return photos;
      });
    } catch {
      /* keep what's on screen; a screensaver must never show an error */
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, RELIST_MS);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 10_000);
    return () => clearInterval(id);
  }, []);

  const photos = useMemo(() => data?.photos ?? [], [data]);
  photosRef.current = photos;

  // ---- the slide cycle ----
  useEffect(() => {
    if (photos.length < 2) return;
    const period = Math.max(5, settings.slide_seconds) * 1000;

    const id = setInterval(() => {
      const album = photosRef.current;

      // Edward gets a beat to wind up and shove before the slide moves.
      if (settings.edward_presents) {
        setPresenting(true);
        window.setTimeout(() => setPresenting(false), 1400);
      }

      window.setTimeout(
        () => {
          setShow((s) => advance(s, album));
          // Only load the upcoming photo once the fade has finished — doing
          // it during the fade is exactly what used to glitch.
          window.setTimeout(
            () => setShow((s) => queueUpcoming(s, photosRef.current)),
            FADE_MS + 200,
          );
        },
        settings.edward_presents ? 700 : 0,
      );
    }, period);

    return () => clearInterval(id);
  }, [photos.length, settings.slide_seconds, settings.edward_presents]);

  const current = visiblePhoto(show);

  // Pick the clock's ink from the patch of THIS photo behind it.
  useEffect(() => {
    if (!current) return;
    let stale = false;
    inkForPhoto(photoUrl(current))
      .then((v) => {
        if (!stale) setInk(v);
      })
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, [current]);

  const hasPhotos = photos.length > 0 && current !== null;

  return (
    <div className="hub-screensaver" data-transition={settings.transition}>
      {hasPhotos && (
        <>
          {([0, 1] as const).map((slot) => {
            const name = show.slots[slot];
            if (!name) return null;
            return (
              <div
                key={slot}
                className="hub-slide"
                data-visible={show.active === slot}
                // Exiting slides leave to the left; a slot merely parked
                // with the upcoming photo waits off-screen to the right.
                data-exiting={show.exiting === slot || undefined}
                // Restarts the Ken Burns drift on each new photo rather
                // than letting it continue mid-zoom from the last one.
                data-photo={name}
              >
                <div
                  className="hub-slide-blur"
                  style={{ backgroundImage: `url(${photoUrl(name)})` }}
                />
                <div
                  className="hub-slide-photo"
                  style={{ backgroundImage: `url(${photoUrl(name)})` }}
                />
              </div>
            );
          })}
          <div className="hub-slide-scrim" />
        </>
      )}

      {settings.edward_presents && hasPhotos && (
        <PhotoCompanion presenting={presenting} pushToward={pushDirection(settings.transition)} />
      )}
      {!settings.edward_presents && hasPhotos && <PhotoCompanion />}

      <div className="hub-screensaver-clock" data-ink={ink}>
        <div className="hub-screensaver-time">
          {formatClock(now)}
        </div>
        <div className="hub-screensaver-date">
          {now.toLocaleDateString([], {
            weekday: "long",
            month: "long",
            day: "numeric",
          })}
        </div>
      </div>

      {data && photos.length === 0 && (
        <div className="hub-screensaver-empty">
          <div className="hub-screensaver-empty-title">No photos yet</div>
          <div className="hub-screensaver-empty-body">
            Drop images into <code>{data.dir}</code>
            {data.skipped_unsupported > 0 && (
              <>
                <br />
                {data.skipped_unsupported} HEIC/RAW file
                {data.skipped_unsupported > 1 ? "s" : ""} skipped — browsers
                can&apos;t draw those. Convert with{" "}
                <code>sips -s format jpeg *.heic --out .</code>
              </>
            )}
          </div>
        </div>
      )}

      {data && photos.length > 0 && data.skipped_unsupported > 0 && (
        <div className="hub-screensaver-note">
          {data.skipped_unsupported} HEIC/RAW skipped
        </div>
      )}
      <HubCanvas />
    </div>
  );
}
