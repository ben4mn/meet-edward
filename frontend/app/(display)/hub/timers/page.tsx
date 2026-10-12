"use client";

import "../hub.css";
import { HubRoot } from "../components/HubRoot";

import { useCallback, useEffect, useRef, useState } from "react";

import { useWakeLock } from "../../display/hooks/useWakeLock";
import { HubHeader } from "../components/HubHeader";

// Timers are pure client state, stored as absolute end timestamps rather
// than remaining seconds — so a reload, a navigation, or the screensaver
// kicking in can't quietly lose the pasta.
const STORE_KEY = "hub.timers";

interface Timer {
  id: string;
  label: string;
  endsAt: number;
}

const PRESETS = [
  { label: "3 min", seconds: 180 },
  { label: "5 min", seconds: 300 },
  { label: "10 min", seconds: 600 },
  { label: "20 min", seconds: 1200 },
  { label: "30 min", seconds: 1800 },
  { label: "1 hour", seconds: 3600 },
];

function fmt(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function HubTimersPage() {
  useWakeLock(true);

  const [timers, setTimers] = useState<Timer[]>([]);
  const [, forceTick] = useState(0);
  const chimed = useRef<Set<string>>(new Set());

  useEffect(() => {
    const raw = window.localStorage.getItem(STORE_KEY);
    if (raw) {
      try {
        setTimers(JSON.parse(raw) as Timer[]);
      } catch {
        /* corrupt store — start clean rather than crash the panel */
      }
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(timers));
  }, [timers]);

  // One ticker for the whole page.
  useEffect(() => {
    const id = setInterval(() => forceTick((n) => n + 1), 500);
    return () => clearInterval(id);
  }, []);

  const chime = useCallback((id: string) => {
    if (chimed.current.has(id)) return;
    chimed.current.add(id);
    try {
      const Ctx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.3, ctx.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 1.1);
      osc.start();
      osc.stop(ctx.currentTime + 1.2);
    } catch {
      /* audio blocked before any user gesture — the visual state still fires */
    }
  }, []);

  const add = (label: string, seconds: number) =>
    setTimers((t) => [
      ...t,
      { id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, label, endsAt: Date.now() + seconds * 1000 },
    ]);

  const remove = (id: string) => {
    chimed.current.delete(id);
    setTimers((t) => t.filter((x) => x.id !== id));
  };

  const now = Date.now();

  return (
    <HubRoot>
      <HubHeader title="Timers" />

      <main className="hub-two-col">
        <section className="hub-card">
          <h2 className="hub-card-title">Start one</h2>
          <div className="hub-presets">
            {PRESETS.map((p) => (
              <button key={p.label} className="hub-preset" onClick={() => add(p.label, p.seconds)}>
                {p.label}
              </button>
            ))}
          </div>
        </section>

        <section className="hub-card">
          <h2 className="hub-card-title">Running</h2>
          <div className="hub-rows hub-rows-scroll">
            {timers.length ? (
              timers.map((t) => {
                const left = t.endsAt - now;
                const done = left <= 0;
                if (done) chime(t.id);
                return (
                  <div className="hub-timer" data-done={done} key={t.id}>
                    <span className="hub-timer-left">{done ? "DONE" : fmt(left)}</span>
                    <span className="hub-timer-label">{t.label}</span>
                    <button className="hub-x" onClick={() => remove(t.id)} aria-label="Clear">
                      ✕
                    </button>
                  </div>
                );
              })
            ) : (
              <div className="hub-empty-lg">No timers running</div>
            )}
          </div>
        </section>
      </main>
    </HubRoot>
  );
}
