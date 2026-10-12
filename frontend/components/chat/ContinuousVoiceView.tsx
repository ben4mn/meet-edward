"use client";

// Live voice as a presence orb (2026-09-26 chat chrome; design system §4
// "voice view"). Edward, large, is the screen's hero: his mood follows the
// voice phase (lib/voice/voicePresence.ts — listen / think / work / speak),
// a wince when something breaks, an "acted" beat when a handed-off job
// finishes. One accent sonar ring follows the level — the mic while he
// listens, his own voice while he speaks — and his mouth follows his voice
// (--mouth). Those three CSS variables are the only per-frame writes, from
// one rAF loop that pauses while the tab is hidden and never runs under
// reduced motion. Below him: his status line in his voice, then the last
// few exchanges in Inter. Mode and mic sensitivity live in a small sheet;
// Stop and End are big and plain.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Hand, Mic, Radio, RotateCcw, SlidersHorizontal, X } from "lucide-react";

import { EdwardAvatar } from "@/components/EdwardAvatar";
// Leaf imports, not the components/ui barrel (which carries PanelShell,
// Switch and the settings rooms onto the hub).
import { Button } from "@/components/ui/Button";
import { Segment } from "@/components/ui/primitives";
import { Sheet } from "@/components/ui/Sheet";
import { Notice } from "@/components/ui/Feedback";
import { usePrefersReducedMotion } from "@/components/ui/motion";
import { useVoiceSession, type VoiceMode } from "@/hooks/useVoiceSession";
import { useWakeLock } from "@/app/(display)/display/hooks/useWakeLock";
import { usePresenceHero } from "@/lib/presenceHero";
import { cn } from "@/lib/utils";
import type { Beat } from "@/lib/edwardMood";
import { isAudioUnlocked } from "@/lib/voice/audioContext";
import { SENSITIVITIES, SENSITIVITY_LABEL, parseSensitivity, type Sensitivity } from "@/lib/voice/noiseGate";
import type { VoiceExchange, VoicePhase, VoiceViewState } from "@/lib/voice/voiceMachine";
import { smoothLevel, visiblePhase, voiceBeat, voiceMood, voiceStatus } from "@/lib/voice/voicePresence";
import type { VoiceSurface } from "@/lib/voice/voiceProtocol";

interface ContinuousVoiceViewProps {
  conversationId?: string;
  onExitVoiceMode: () => void;
  onConversationCreated?: (id: string) => void;
  /** Reports the voice machine's phase to the chat (presence reads it); the
   *  chat passes its `setVoicePhase`. Absent on the hub, which has no chat —
   *  so the view never imports ChatContext (2026-09-26 perf pass). */
  onPhase?: (phase: VoicePhase | null) => void;
  /** Who is speaking: "app" (the default) is the owner; the household hub
   *  passes "hub", whose turns the backend runs as `hub_voice`, not as the owner. */
  surface?: VoiceSurface;
}

const MODE_STORAGE_KEY = "edward-voice-mode";
const SENSITIVITY_STORAGE_KEY = "edward-voice-sensitivity";

const SENSITIVITY_HINT: Record<Sensitivity, string> = {
  high: "A quiet room: I hear everything the mic does.",
  normal: "I ignore steady background noise.",
  low: "A noisy room: only clear, close speech gets through, and it takes three words to interrupt me.",
};

function loadSensitivity(): Sensitivity {
  try {
    return parseSensitivity(localStorage.getItem(SENSITIVITY_STORAGE_KEY));
  } catch {
    return "normal";
  }
}

function loadMode(): VoiceMode {
  try {
    return localStorage.getItem(MODE_STORAGE_KEY) === "ptt" ? "ptt" : "auto";
  } catch {
    return "auto";
  }
}

function store(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode */
  }
}

// ---- the rolling transcript ---------------------------------------------

export function VoiceTranscript({ exchanges, liveCaption }: { exchanges: readonly VoiceExchange[]; liveCaption?: string }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [exchanges, liveCaption]);
  if (exchanges.length === 0 && !liveCaption) return null;
  return (
    <div
      ref={scrollRef}
      className="max-h-[30dvh] w-full overflow-y-auto overscroll-contain px-1 [mask-image:linear-gradient(to_bottom,transparent,black_28px)]"
      aria-live="polite"
      aria-label="What we said"
    >
      <div className="space-y-3 pt-6">
        {exchanges.map((e, i) => (
          <div key={i} className="space-y-1.5">
            {e.you ? (
              <p className="ml-auto w-fit max-w-[85%] rounded-lg rounded-br-xs bg-surface-3 px-3 py-1.5 text-footnote text-text-primary">
                {e.you}
              </p>
            ) : null}
            {e.edward.trim() ? (
              <p className="max-w-[92%] text-body text-text-secondary">
                {e.edward.trim()}
                {e.cut ? <span className="text-text-muted"> — cut off</span> : null}
              </p>
            ) : null}
          </div>
        ))}
        {liveCaption ? (
          <p className="ml-auto w-fit max-w-[85%] rounded-lg rounded-br-xs border border-dashed border-text-muted/40 px-3 py-1.5 text-footnote text-text-secondary">
            {liveCaption}
            <span className="ml-0.5 inline-block h-3.5 w-px translate-y-0.5 bg-text-secondary motion-safe:animate-pulse" aria-hidden />
          </p>
        ) : null}
      </div>
    </div>
  );
}

// ---- the settings sheet ---------------------------------------------------

export function VoiceSettingsSheet({
  open,
  onClose,
  mode,
  sensitivity,
  onMode,
  onSensitivity,
}: {
  open: boolean;
  onClose: () => void;
  mode: VoiceMode;
  sensitivity: Sensitivity;
  onMode: (m: VoiceMode) => void;
  onSensitivity: (s: Sensitivity) => void;
}) {
  return (
    <Sheet open={open} onClose={onClose} title="Voice settings" className="md:max-w-md">
      <div className="space-y-5 pb-2">
        <div className="space-y-2">
          <p className="text-overline text-text-muted">How you talk</p>
          <Segment<VoiceMode>
            label="How you talk"
            value={mode}
            onChange={onMode}
            options={[
              { value: "auto", label: <span className="inline-flex items-center gap-1.5"><Radio className="h-3.5 w-3.5" aria-hidden />Hands-free</span> },
              { value: "ptt", label: <span className="inline-flex items-center gap-1.5"><Hand className="h-3.5 w-3.5" aria-hidden />Hold to talk</span> },
            ]}
          />
          <p className="text-footnote text-text-muted">
            {mode === "auto" ? "I listen the whole time and answer when you pause." : "I only hear you while you hold the button."}
          </p>
        </div>
        {mode === "auto" ? (
          <div className="space-y-2">
            <p className="text-overline text-text-muted">Mic sensitivity</p>
            <Segment<Sensitivity>
              label="Mic sensitivity"
              value={sensitivity}
              onChange={onSensitivity}
              options={SENSITIVITIES.map((s) => ({ value: s, label: SENSITIVITY_LABEL[s] }))}
            />
            <p className="text-footnote text-text-muted">{SENSITIVITY_HINT[sensitivity]}</p>
          </div>
        ) : null}
      </div>
    </Sheet>
  );
}

// ---- controls -------------------------------------------------------------

function RoundControl({
  label,
  ariaLabel,
  onClick,
  className,
  children,
  size = 56,
}: {
  label: string;
  ariaLabel?: string;
  onClick?: () => void;
  className?: string;
  children: React.ReactNode;
  size?: number;
}) {
  return (
    <div className="flex w-20 flex-col items-center gap-1.5">
      <button
        type="button"
        onClick={onClick}
        aria-label={ariaLabel ?? label}
        className={cn(
          "inline-flex items-center justify-center rounded-full outline-none transition-[transform,background-color,color] duration-press ease-out active:scale-[.94]",
          "focus-visible:ring-2 focus-visible:ring-terminal/60 focus-visible:ring-offset-2 focus-visible:ring-offset-primary-bg",
          className,
        )}
        style={{ width: size, height: size }}
      >
        {children}
      </button>
      <span className="text-caption text-text-muted" aria-hidden>
        {label}
      </span>
    </div>
  );
}

// ---- the view -------------------------------------------------------------

export function ContinuousVoiceView({
  conversationId,
  onExitVoiceMode,
  onConversationCreated,
  onPhase,
  surface = "app",
}: ContinuousVoiceViewProps) {
  const { state, connect, disconnect, interrupt, pttPress, pttRelease, getMicLevel, getOutputLevel, unlock } =
    useVoiceSession(surface);
  // A phone that dims mid-sentence ends the mic and the socket; keep the
  // screen on for as long as this view is up.
  useWakeLock(true);
  const reduced = usePrefersReducedMotion();
  const [mode, setMode] = useState<VoiceMode>("auto");
  const [sensitivity, setSensitivity] = useState<Sensitivity>("normal");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [audioBlocked, setAudioBlocked] = useState(false);
  const hasNotifiedRef = useRef(false);
  const modeRef = useRef<VoiceMode>("auto");
  const sensitivityRef = useRef<Sensitivity>("normal");
  const orbRef = useRef<HTMLDivElement>(null);
  // The screen's hero Edward: the presence bar's Edward defers while he is up.
  const heroLive = usePresenceHero(orbRef);

  // Connect on mount with the remembered mode + mic sensitivity, tear down on unmount.
  useEffect(() => {
    const initial = loadMode();
    const initialSensitivity = loadSensitivity();
    modeRef.current = initial;
    sensitivityRef.current = initialSensitivity;
    setMode(initial);
    setSensitivity(initialSensitivity);
    connect(conversationId, initial, initialSensitivity);
    return () => {
      disconnect();
    };
    // conversationId is captured at mount time on purpose.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (state.conversationId && onConversationCreated && !hasNotifiedRef.current) {
      hasNotifiedRef.current = true;
      onConversationCreated(state.conversationId);
    }
  }, [state.conversationId, onConversationCreated]);

  // Presence: what live voice is doing, for the shell's Edward; cleared on the way out.
  const phase = visiblePhase(state);
  useEffect(() => {
    onPhase?.(phase);
  }, [phase, onPhase]);
  useEffect(() => () => onPhase?.(null), [onPhase]);

  // Beats: the one that matters from each change of state.
  const prevRef = useRef<VoiceViewState>(state);
  const [beat, setBeat] = useState<{ beat: Beat; key: number } | null>(null);
  useEffect(() => {
    const b = voiceBeat(prevRef.current, state);
    prevRef.current = state;
    if (b) setBeat((old) => ({ beat: b, key: (old?.key ?? 0) + 1 }));
  }, [state]);

  // iOS: output stays silent until a tap resumes the AudioContext.
  useEffect(() => {
    if (state.phase === "listening" || state.phase === "speaking") setAudioBlocked(!isAudioUnlocked());
  }, [state.phase]);

  const handleUnlock = useCallback(async () => {
    const ok = await unlock();
    setAudioBlocked(!ok);
  }, [unlock]);

  const reconnect = useCallback(
    (nextMode: VoiceMode, nextSensitivity: Sensitivity) => {
      // Keep the conversation and the rolling transcript on screen.
      disconnect({ keep: true });
      // Let the socket close before opening the next one.
      window.setTimeout(() => connect(state.conversationId || conversationId, nextMode, nextSensitivity), 50);
    },
    [connect, disconnect, state.conversationId, conversationId],
  );

  const switchMode = useCallback(
    (next: VoiceMode) => {
      if (next === modeRef.current) return;
      modeRef.current = next;
      setMode(next);
      store(MODE_STORAGE_KEY, next);
      reconnect(next, sensitivityRef.current);
    },
    [reconnect],
  );

  // Scribe's VAD tuning is fixed per session, so a new level means a new socket.
  const switchSensitivity = useCallback(
    (next: Sensitivity) => {
      if (next === sensitivityRef.current) return;
      sensitivityRef.current = next;
      setSensitivity(next);
      store(SENSITIVITY_STORAGE_KEY, next);
      reconnect(modeRef.current, next);
    },
    [reconnect],
  );

  const handleExit = () => {
    disconnect();
    onExitVoiceMode();
  };

  const handleRetry = () => {
    connect(state.conversationId || conversationId || undefined, modeRef.current, sensitivityRef.current);
  };

  // Esc ends the session (the settings sheet takes Esc first while open).
  const exitRef = useRef(handleExit);
  exitRef.current = handleExit;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !settingsOpen && !e.defaultPrevented) exitRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [settingsOpen]);

  const micLive = state.phase === "listening" && (mode === "auto" || state.pttHeld) && !state.awaitingPlaybackEnd;
  const speaking = phase === "speaking";
  const mood = voiceMood(state);

  // The per-frame loop: three CSS variables on the orb, nothing else.
  const levelsRef = useRef({ mic: 0, out: 0 });
  const liveRef = useRef({ micLive, speaking });
  liveRef.current = { micLive, speaking };
  useEffect(() => {
    const el = orbRef.current;
    if (!el) return;
    if (reduced) {
      el.style.setProperty("--level", "0");
      el.style.setProperty("--ring", "0");
      el.style.setProperty("--mouth", "0.5");
      return;
    }
    let raf = 0;
    const tick = () => {
      const { micLive: mLive, speaking: sp } = liveRef.current;
      const lv = levelsRef.current;
      lv.mic = smoothLevel(lv.mic, mLive ? getMicLevel() * 1.6 : 0);
      lv.out = smoothLevel(lv.out, sp ? getOutputLevel() : 0);
      el.style.setProperty("--level", lv.mic.toFixed(3));
      el.style.setProperty("--mouth", lv.out.toFixed(3));
      el.style.setProperty("--ring", (sp ? lv.out : lv.mic).toFixed(3));
      raf = requestAnimationFrame(tick);
    };
    const start = () => {
      cancelAnimationFrame(raf);
      if (document.visibilityState === "visible") raf = requestAnimationFrame(tick);
    };
    const onVis = () => (document.visibilityState === "visible" ? start() : cancelAnimationFrame(raf));
    start();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [getMicLevel, getOutputLevel, reduced]);

  const active = phase === "listening" || phase === "thinking" || phase === "speaking";
  const status = voiceStatus(state);
  const ptt = mode === "ptt";
  const canInterrupt = phase === "speaking" || phase === "thinking";

  const orbStyle = useMemo(
    () => ({ "--level": 0, "--ring": 0, "--mouth": 0.5 }) as React.CSSProperties,
    [],
  );

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-primary-bg"
      role="dialog"
      aria-modal="true"
      aria-label="Talking with Edward"
      style={{
        paddingTop: "max(env(safe-area-inset-top), 12px)",
        paddingBottom: "max(env(safe-area-inset-bottom), 16px)",
      }}
    >
      {/* a quiet emerald wash behind him while the session is alive */}
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute inset-x-0 top-[18%] mx-auto h-[420px] w-[420px] max-w-full rounded-full transition-opacity duration-slow",
          "bg-[radial-gradient(closest-side,rgb(var(--terminal-rgb)/0.13),transparent)]",
          active ? "opacity-100" : "opacity-40",
        )}
      />

      {/* top: how you are talking (tap to change) */}
      <div className="relative flex h-11 items-center justify-center px-3">
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          aria-label={`${ptt ? "Hold to talk" : "Hands-free"} · change voice settings`}
          className="inline-flex h-9 items-center gap-1.5 rounded-full border border-border bg-surface/70 px-3 text-caption text-text-secondary outline-none transition-[background-color,color,transform] duration-press hover:text-text-primary active:scale-[.97] focus-visible:ring-2 focus-visible:ring-terminal/60"
        >
          {ptt ? <Hand className="h-3.5 w-3.5" aria-hidden /> : <Radio className="h-3.5 w-3.5" aria-hidden />}
          {ptt ? "Hold to talk" : "Hands-free"}
        </button>
      </div>

      {/* Edward, his status, the conversation */}
      <div className="relative mx-auto flex min-h-0 w-full max-w-md flex-1 flex-col items-center px-6">
        <div className="flex flex-1 flex-col items-center justify-center gap-9 pt-4">
          <div ref={orbRef} className="relative flex items-center justify-center" style={orbStyle}>
            {/* one accent sonar ring, driven by the level */}
            <span
              aria-hidden
              className="absolute left-1/2 top-1/2 h-[228px] w-[228px] rounded-full border-2 border-terminal"
              style={{
                transform: "translate(-50%, -50%) scale(calc(0.9 + var(--ring) * 0.24))",
                opacity: active ? "calc(0.12 + var(--ring) * 0.55)" : 0,
              }}
            />
            <span
              aria-hidden
              className={cn(
                "absolute left-1/2 top-1/2 h-[188px] w-[188px] -translate-x-1/2 -translate-y-1/2 rounded-full border border-border bg-surface/50 transition-shadow duration-slow",
                active && "shadow-glow",
              )}
            />
            <EdwardAvatar
              size={156}
              mood={mood}
              beat={beat?.beat ?? null}
              beatKey={beat?.key}
              live={heroLive}
              // The provider sends the app's one perk as a beat, but this hero
              // reads no presence beats: it perks on return itself.
              perkOnReturn={heroLive}
              lipsync={speaking}
              aria-label={`Edward · ${status}`}
              className="relative"
            />
          </div>

          <div className="flex min-h-[52px] flex-col items-center gap-1 text-center">
            <p className="text-title3 text-text-primary" aria-live="polite">
              {status}
            </p>
            {state.bridge && phase === "thinking" ? (
              <p className="text-footnote text-text-secondary">{state.bridge}</p>
            ) : state.backgroundWork ? (
              <p className="inline-flex items-center gap-1.5 text-footnote text-text-secondary">
                <span className="h-1.5 w-1.5 rounded-full bg-terminal" aria-hidden />
                Working in the background · {state.backgroundWork}
              </p>
            ) : null}
          </div>
        </div>

        <div className="w-full pb-4">
          {state.phase === "error" ? (
            <Notice tone="bad">
              {state.lostConnection
                ? "The link to the Mac dropped. Try again and I'll pick up where we were."
                : state.errorMessage || "The voice link failed. Try again in a moment."}
            </Notice>
          ) : (
            <VoiceTranscript exchanges={state.exchanges} liveCaption={state.phase === "listening" ? state.liveCaption : ""} />
          )}
        </div>
      </div>

      {/* sound unlock (iOS keeps output muted until a tap) */}
      {audioBlocked && state.phase !== "error" ? (
        <div className="relative mb-3 flex justify-center">
          <Button variant="primary" size="sm" onClick={handleUnlock}>
            Tap to turn on sound
          </Button>
        </div>
      ) : null}

      {/* controls */}
      <div className="relative mx-auto flex w-full max-w-md items-end justify-between px-6">
        <RoundControl
          label="Settings"
          ariaLabel="Voice settings"
          onClick={() => setSettingsOpen(true)}
          className="border border-border bg-surface-3 text-text-secondary hover:text-text-primary"
        >
          <SlidersHorizontal className="h-5 w-5" aria-hidden />
        </RoundControl>

        <div className="flex w-28 flex-col items-center gap-1.5">
          {state.phase === "error" ? (
            <>
              <button
                type="button"
                onClick={handleRetry}
                aria-label="Try again"
                className="inline-flex h-[72px] w-[72px] items-center justify-center rounded-full bg-text-primary text-primary-bg shadow-e1 outline-none transition-transform duration-press active:scale-[.94] focus-visible:ring-2 focus-visible:ring-terminal/60 focus-visible:ring-offset-2 focus-visible:ring-offset-primary-bg"
              >
                <RotateCcw className="h-7 w-7" aria-hidden />
              </button>
              <span className="text-caption text-text-muted" aria-hidden>
                Try again
              </span>
            </>
          ) : ptt && !canInterrupt ? (
            <>
              <button
                type="button"
                onPointerDown={(e) => {
                  e.preventDefault();
                  (e.currentTarget as HTMLButtonElement).setPointerCapture?.(e.pointerId);
                  void handleUnlock();
                  pttPress();
                }}
                onPointerUp={() => pttRelease()}
                onPointerCancel={() => pttRelease()}
                onContextMenu={(e) => e.preventDefault()}
                disabled={state.phase !== "listening"}
                aria-label="Hold to talk"
                aria-pressed={state.pttHeld}
                className={cn(
                  "inline-flex h-[84px] w-[84px] select-none items-center justify-center rounded-full outline-none touch-manipulation",
                  "focus-visible:ring-2 focus-visible:ring-terminal/60 focus-visible:ring-offset-2 focus-visible:ring-offset-primary-bg",
                  "transition-[transform,background-color,color,box-shadow] duration-fast ease-out disabled:opacity-50",
                  state.pttHeld
                    ? "scale-[1.04] bg-text-primary text-primary-bg shadow-glow"
                    : "border border-border bg-surface-3 text-text-primary",
                )}
              >
                <Mic className="h-8 w-8" aria-hidden />
              </button>
              <span className="text-caption text-text-muted" aria-hidden>
                {state.pttHeld ? "Release to send" : "Hold to talk"}
              </span>
            </>
          ) : canInterrupt ? (
            <>
              <button
                type="button"
                onClick={interrupt}
                aria-label="Stop Edward"
                className="inline-flex h-[72px] w-[72px] items-center justify-center rounded-full text-text-primary outline-none transition-transform duration-press active:scale-[.94] focus-visible:ring-2 focus-visible:ring-terminal/60"
              >
                <svg viewBox="0 0 72 72" className="h-[72px] w-[72px]" aria-hidden>
                  <circle cx="36" cy="36" r="34" className="fill-surface-3" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.5" />
                  <rect x="27" y="27" width="18" height="18" rx="3.5" fill="currentColor" />
                </svg>
              </button>
              <span className="text-caption text-text-muted" aria-hidden>
                Stop
              </span>
            </>
          ) : (
            <span className="h-[72px]" aria-hidden />
          )}
        </div>

        <RoundControl
          label="End"
          ariaLabel="End voice"
          onClick={handleExit}
          className={
            state.phase === "error"
              ? "border border-border bg-surface-3 text-text-primary"
              : "bg-text-primary text-primary-bg shadow-e1"
          }
        >
          <X className="h-6 w-6" aria-hidden />
        </RoundControl>
      </div>

      <VoiceSettingsSheet
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        mode={mode}
        sensitivity={sensitivity}
        onMode={switchMode}
        onSensitivity={switchSensitivity}
      />
    </div>
  );
}
