"use client";

// The chat view's one screen-reader voice for a turn (review 2026-09-26,
// aria-live-turn-chatter): each coarse phase once, debounced so fast flips
// collapse, then how the turn ended. Mounted in ChatWindow, outside the
// phone bar (display:none on md+) and the composing fold. Words are pure in
// lib/turnAnnouncer.ts.

import React, { useEffect, useRef, useState } from "react";
import type { Message, Turn } from "@/lib/ChatContext";
import { coarsePhase, endAnnouncement } from "@/lib/turnAnnouncer";

export const PHASE_DEBOUNCE_MS = 1500;

export function TurnAnnouncer({
  turn,
  messages,
  debounceMs = PHASE_DEBOUNCE_MS,
}: {
  turn: Turn | null;
  messages: Message[];
  debounceMs?: number;
}) {
  const [said, setSaid] = useState("");
  // A turn that had already ended when the view opened is history.
  const endedSeen = useRef<string | null>(turn && turn.endedAt != null ? turn.id : null);
  const lastPhase = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const phase = coarsePhase(turn);
  const ended = turn && turn.endedAt != null ? turn : null;
  const finalText = ended ? messages.find((m) => m.id === ended.id)?.content ?? "" : "";
  const end = ended && endedSeen.current !== ended.id ? endAnnouncement(ended, finalText) : null;

  useEffect(() => {
    if (end && ended) {
      endedSeen.current = ended.id;
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
      lastPhase.current = null;
      setSaid(end);
      return;
    }
    if (!phase || phase === lastPhase.current) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      lastPhase.current = phase;
      setSaid(`Edward · ${phase}`);
    }, debounceMs);
  }, [phase, end, ended, debounceMs]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  return (
    <div className="sr-only" aria-live="polite" aria-atomic="true">
      {said}
    </div>
  );
}
