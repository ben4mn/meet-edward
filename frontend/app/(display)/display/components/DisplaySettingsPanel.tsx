"use client";

import React, { useEffect, useRef, useState } from "react";
import type { WakeLockState } from "../hooks/useWakeLock";
import {
  ACCENT_HEX,
  type AccentName,
  type AvatarSize,
  type Density,
  type DisplaySettings,
  type TimeOfDay,
} from "../lib/types";

interface Props {
  settings: DisplaySettings;
  update: (patch: Partial<DisplaySettings>) => void;
  wakeLock?: WakeLockState;
}

function isFullscreen(): boolean {
  if (typeof document === "undefined") return false;
  return Boolean(document.fullscreenElement);
}

async function toggleFullscreen(): Promise<void> {
  if (typeof document === "undefined") return;
  try {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else {
      await document.documentElement.requestFullscreen();
    }
  } catch (e) {
    console.warn("[fullscreen] toggle failed:", e);
  }
}

const WAKE_LABELS: Record<WakeLockState, string> = {
  active: "screen will stay awake",
  released: "released — focus the tab to renew",
  unsupported: "wake lock unsupported in this browser",
  error: "wake lock unavailable (try clicking the page first)",
};

const WAKE_DOTS: Record<WakeLockState, string> = {
  active: "var(--ed-accent)",
  released: "var(--ed-text-faint)",
  unsupported: "var(--ed-text-faint)",
  error: "var(--ed-warn)",
};

const ACCENT_OPTIONS: AccentName[] = ["green", "blue", "amber", "violet"];
/** The stage has two canvases: ink, and night (dimmer, drifting). "auto"
 *  follows the hour; an older morning/dusk choice reads as day. */
const TOD_OPTIONS: TimeOfDay[] = ["auto", "day", "night"];
const todShown = (t: TimeOfDay): TimeOfDay => (t === "morning" || t === "dusk" ? "day" : t);
const DENSITY_OPTIONS: Density[] = ["loose", "normal", "dense"];
const SIZE_OPTIONS: AvatarSize[] = ["sm", "md", "lg", "xl"];
const ON_OFF = ["on", "off"] as const;

export function DisplaySettingsPanel({ settings, update, wakeLock }: Props) {
  const [visible, setVisible] = useState(false); // gear visibility
  const [open, setOpen] = useState(false);
  const [fs, setFs] = useState(false);
  const lastMoveRef = useRef<number>(Date.now());

  // Track browser fullscreen so the toggle label stays in sync if the user
  // hits Esc to exit (which fires fullscreenchange but not a click on us).
  useEffect(() => {
    const onChange = () => setFs(isFullscreen());
    onChange();
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  useEffect(() => {
    const onMove = () => {
      lastMoveRef.current = Date.now();
      setVisible(true);
    };
    window.addEventListener("mousemove", onMove);
    return () => window.removeEventListener("mousemove", onMove);
  }, []);

  useEffect(() => {
    if (open) return; // keep gear visible while drawer is open
    const id = setInterval(() => {
      if (Date.now() - lastMoveRef.current > 6000) setVisible(false);
    }, 500);
    return () => clearInterval(id);
  }, [open]);

  return (
    <>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label="Display settings"
        style={{
          position: "fixed",
          top: 14,
          right: 14,
          zIndex: 1000,
          width: 32,
          height: 32,
          borderRadius: 8,
          border: "1px solid var(--ed-border-strong)",
          background: "rgba(0,0,0,0.45)",
          color: "var(--ed-text-dim)",
          cursor: "pointer",
          opacity: visible || open ? 1 : 0,
          transition: "opacity 250ms ease",
          fontSize: 16,
          lineHeight: 1,
        }}
      >
        ⚙
      </button>

      {open && (
        <div
          onClick={(e) => e.stopPropagation()}
          className="mc-card"
          style={{
            position: "fixed",
            top: 56,
            right: 14,
            zIndex: 1000,
            width: 280,
            maxHeight: "calc(100vh - 80px)",
            overflowY: "auto",
            padding: 14,
            background: "linear-gradient(180deg, var(--ed-surface), var(--ed-bg-2))",
            border: "1px solid var(--ed-border-strong)",
            borderRadius: 12,
            color: "var(--ed-text)",
            fontFamily: "var(--ed-mono)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
            <span style={{ fontSize: 11, letterSpacing: "0.16em", textTransform: "uppercase" }}>
              tweaks
            </span>
            <button
              onClick={() => setOpen(false)}
              style={{
                appearance: "none",
                background: "transparent",
                border: 0,
                color: "var(--ed-text-dim)",
                cursor: "pointer",
                fontSize: 12,
              }}
            >
              ✕
            </button>
          </div>

          <Section label="display">
            <button
              onClick={toggleFullscreen}
              style={{
                appearance: "none",
                width: "100%",
                padding: "8px 10px",
                fontSize: 11,
                fontFamily: "inherit",
                textTransform: "uppercase",
                letterSpacing: "0.1em",
                cursor: "pointer",
                borderRadius: 6,
                border: "1px solid var(--ed-border-strong)",
                background: "rgba(0,0,0,0.3)",
                color: "var(--ed-text)",
              }}
            >
              {fs ? "exit fullscreen" : "go fullscreen"}
            </button>
            {wakeLock && (
              <div
                style={{
                  marginTop: 8,
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: 10,
                  color: "var(--ed-text-faint)",
                }}
              >
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: WAKE_DOTS[wakeLock],
                  }}
                />
                {WAKE_LABELS[wakeLock]}
              </div>
            )}
          </Section>

          <Section label="avatar size">
            <Segmented
              options={SIZE_OPTIONS}
              value={settings.avatar_size}
              onChange={(v) => update({ avatar_size: v as AvatarSize })}
            />
          </Section>

          <Section label="density">
            <Segmented
              options={DENSITY_OPTIONS}
              value={settings.density}
              onChange={(v) => update({ density: v as Density })}
            />
          </Section>

          <Section label="accent">
            <div style={{ display: "flex", gap: 8 }}>
              {ACCENT_OPTIONS.map((a) => (
                <button
                  key={a}
                  onClick={() => update({ accent: a })}
                  aria-label={a}
                  style={{
                    width: 28,
                    height: 28,
                    borderRadius: 8,
                    cursor: "pointer",
                    border:
                      settings.accent === a
                        ? "2px solid var(--ed-text)"
                        : "1px solid var(--ed-border-strong)",
                    background: ACCENT_HEX[a],
                  }}
                />
              ))}
            </div>
          </Section>

          <Section label="time of day">
            <Segmented
              label="time of day"
              options={TOD_OPTIONS}
              value={todShown(settings.time_of_day)}
              onChange={(v) => update({ time_of_day: v as TimeOfDay })}
            />
          </Section>

          <Section label="thinking on screen">
            <Segmented
              label="thinking on screen"
              options={ON_OFF}
              value={settings.show_thinking === false ? "off" : "on"}
              onChange={(v) => update({ show_thinking: v === "on" })}
            />
          </Section>

          <Section label="speak aloud">
            <Segmented
              label="speak aloud"
              options={ON_OFF}
              value={settings.speak_aloud === false ? "off" : "on"}
              onChange={(v) => update({ speak_aloud: v === "on" })}
            />
          </Section>

          <Section label="the stage">
            <div style={{ fontSize: 11, color: "var(--ed-text-dim)", lineHeight: 1.5 }}>
              Edward sets the stage and decides what goes on it. Ask him:
              &ldquo;put a pasta timer up&rdquo;, &ldquo;show a Twitch stream on your screen&rdquo;,
              &ldquo;back to your usual scene&rdquo;.
            </div>
          </Section>
        </div>
      )}
    </>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div
        style={{
          fontSize: 9,
          letterSpacing: "0.16em",
          textTransform: "uppercase",
          color: "var(--ed-text-faint)",
          marginBottom: 6,
        }}
      >
        {label}
      </div>
      {children}
    </div>
  );
}

function Segmented({
  label,
  options,
  value,
  onChange,
}: {
  label?: string;
  options: readonly string[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      style={{
        display: "flex",
        background: "rgba(0,0,0,0.3)",
        borderRadius: 6,
        padding: 2,
        border: "1px solid var(--ed-border)",
      }}
    >
      {options.map((o) => (
        <button
          key={o}
          data-value={o}
          aria-pressed={value === o}
          onClick={() => onChange(o)}
          style={{
            flex: 1,
            padding: "5px 6px",
            fontSize: 10,
            fontFamily: "inherit",
            textTransform: "uppercase",
            letterSpacing: "0.1em",
            cursor: "pointer",
            border: 0,
            borderRadius: 4,
            background: value === o ? "var(--ed-accent-soft)" : "transparent",
            color: value === o ? "var(--ed-accent)" : "var(--ed-text-dim)",
          }}
        >
          {o}
        </button>
      ))}
    </div>
  );
}
