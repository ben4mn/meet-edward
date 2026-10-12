"use client";

import "./hub.css";
import { HubRoot } from "./components/HubRoot";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { lazyComponent } from "@/lib/lazyComponent";
import { formatClock } from "@/lib/time";

import { usePoll } from "../display/hooks/usePoll";
import { useWakeLock } from "../display/hooks/useWakeLock";
import { useIdleRedirect } from "./lib/useIdleRedirect";
import { WhoToggle } from "./components/WhoToggle";
import { fetchBoard } from "./lib/hubApi";
import { useWho } from "./lib/useWho";
import type { CareWindow, HubBoard } from "./lib/types";

// Edward in the greeting, with his presence, is his own chunk (2026-09-26
// perf pass: the Pi parses slowly, and his mood arrives over the network
// anyway). Fetched as this module runs, so he is almost always here on the
// first render; if not, his 200 px square is held empty so nothing moves.
function HubEdwardSpace() {
  return <span aria-hidden style={{ display: "inline-flex", width: 200, height: 200 }} />;
}
const HubEdward = lazyComponent(
  () => import("./components/HubEdward").then((mod) => mod.HubEdward),
  HubEdwardSpace,
);
if (typeof window !== "undefined") {
  HubEdward.preload().catch(() => {
    /* retried when the greeting mounts */
  });
}

// Slower than the pet screen: the home view is glanced at, not worked.
const POLL_MS = 15_000;

const SPECIES_ICON: Record<string, string> = { dog: "🐕", cat: "🐈" };

interface Tile {
  id: string;
  label: string;
  icon: string;
  href?: string;
  hint?: string;
}

const TILES: Tile[] = [
  { id: "pets", label: "Pets", icon: "🐾", href: "/hub/pets", hint: "meals & meds" },
  { id: "today", label: "Today", icon: "📅", href: "/hub/today", hint: "calendar" },
  { id: "lists", label: "Lists", icon: "🛒", href: "/hub/lists", hint: "shopping" },
  { id: "ask", label: "Ask Edward", icon: "🎙", href: "/hub/ask", hint: "voice" },
  { id: "timers", label: "Timers", icon: "⏱", href: "/hub/timers", hint: "kitchen" },
  { id: "health", label: "Health", icon: "🩺", href: "/hub/health", hint: "edward" },
  { id: "photos", label: "Photos", icon: "🖼", href: "/hub/idle", hint: "screensaver" },
  { id: "settings", label: "Settings", icon: "⚙", href: "/hub/settings", hint: "this screen" },
];

function greetingFor(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 10_000);
    return () => clearInterval(id);
  }, []);
  return now;
}

export default function HubHomePage() {
  useWakeLock(true);
  useIdleRedirect();

  const clock = useClock();
  // Edward waves whenever someone new taps in; his presence is the
  // household's (components/HubEdward.tsx).
  const { who, setWho } = useWho();
  const [waveKey, setWaveKey] = useState(0);
  const pickWho = useCallback(
    (person: string) => {
      if (person !== who) setWaveKey((k) => k + 1);
      setWho(person);
    },
    [who, setWho],
  );
  const { data: board } = usePoll<HubBoard>(fetchBoard, POLL_MS);

  const currentWindow: CareWindow = board?.window ?? "am";

  // One honest sentence about the household, not a dashboard.
  const summary = useMemo(() => {
    if (!board) return "…";

    const bowls = board.pets.map((p) => p.feedings[currentWindow]).filter((f) => f.item_id);
    const fed = bowls.filter((f) => f.done).length;

    const outstandingMeds = board.pets.flatMap((p) =>
      p.meds.filter(
        (m) => m.window === currentWindow && (m.status === "due" || m.status === "overdue"),
      ),
    );

    const when = currentWindow === "am" ? "morning" : "evening";
    const parts = [`${fed} of ${bowls.length} ${when} meals logged`];
    if (outstandingMeds.length) {
      parts.push(`${outstandingMeds.length} dose${outstandingMeds.length > 1 ? "s" : ""} still due`);
    }
    return parts.join(" · ");
  }, [board, currentWindow]);

  const petsBadge = useMemo(() => {
    if (!board) return 0;
    let n = 0;
    for (const pet of board.pets) {
      const bowl = pet.feedings[currentWindow];
      if (bowl.item_id && !bowl.done) n += 1;
      n += pet.meds.filter(
        (m) => m.window === currentWindow && (m.status === "due" || m.status === "overdue"),
      ).length;
    }
    return n;
  }, [board, currentWindow]);

  return (
    <HubRoot>
      <header className="hub-header">
        <div>
          <div className="hub-clock">
            {formatClock(clock)}
          </div>
          <div className="hub-date">
            {clock.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}
          </div>
        </div>

        <div className="hub-alerts">
          {board?.alerts.map((a) => (
            <div key={`${a.pet}-${a.item}`} className="hub-alert">
              {a.pet} · {a.item} · {a.minutes_late}m late
            </div>
          ))}
        </div>

        <WhoToggle who={who} onPick={pickWho} />
      </header>

      <main className="hub-home">
        <section className="hub-greet">
          <HubEdward waveKey={waveKey} />
          <div className="hub-greet-text">
            {greetingFor(clock.getHours())}
            {who ? `, ${who}` : ""}
          </div>
          <div className="hub-greet-sub">{summary}</div>
        </section>

        <section className="hub-tiles">
          {TILES.map((tile) => {
            const badge = tile.id === "pets" ? petsBadge : 0;
            const body = (
              <>
                {badge > 0 && <span className="hub-tile-badge">{badge}</span>}
                <span className="hub-tile-icon">{tile.icon}</span>
                <span className="hub-tile-label">{tile.label}</span>
                <span className="hub-tile-hint">{tile.hint ?? "soon"}</span>
              </>
            );

            // Unbuilt tiles render as visibly inert rather than as buttons
            // that do nothing — a dead tap is worse than an honest label.
            return tile.href ? (
              <Link key={tile.id} href={tile.href} className="hub-tile" data-live="true">
                {body}
              </Link>
            ) : (
              <div key={tile.id} className="hub-tile" data-live="false" aria-disabled="true">
                {body}
              </div>
            );
          })}
        </section>
      </main>

      {/* The household's most-asked question, answered without a tap. */}
      <Link href="/hub/pets" className="hub-strip">
        {board?.pets.map((pet) => {
          const am = pet.feedings.am;
          const pm = pet.feedings.pm;
          return (
            <span key={pet.id} className="hub-strip-pet">
              <span className="hub-strip-icon">{SPECIES_ICON[pet.species ?? ""] ?? "🐾"}</span>
              <span className="hub-strip-name">{pet.name}</span>
              <span className="hub-strip-dot" data-done={am.done} />
              <span className="hub-strip-dot" data-done={pm.done} />
              {pm.marker && <span className="hub-strip-marker">{pm.marker}</span>}
            </span>
          );
        })}
      </Link>
    </HubRoot>
  );
}
