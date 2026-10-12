"use client";

// A line Edward says with `aloud` plays once through the host Mac's speakers
// (2026-09-28). The backend already keeps quiet hours (a line said between
// 22:00 and 07:00 is never aloud); this side plays each line id once per tab
// session, only while the tab is visible, only while it is under a minute old,
// and never when the owner turned "speak aloud" off. Failures are silent — the
// line is still on the screen. `speaking` is true only while the audio
// really plays, so "Speaking" under him is never a guess; `played` holds the
// line ids whose audio really started (this tab's session), so "Said aloud"
// beside him is never a guess either — a line asked for aloud that the owner's
// switch, a hidden tab, a failed synthesis or Chrome's autoplay rule kept
// silent reads "Said".

import { useEffect, useRef, useState } from "react";

import { synthesizeSpeech } from "@/lib/api";

import { shouldSpeak, type SpokenLine } from "../lib/stageModel";

export const SPOKEN_KEY = "edward.screen.spoken";
export const PLAYED_KEY = "edward.screen.played";
const KEEP = 20;

function readIds(key: string): Set<string> {
  try {
    const raw = sessionStorage.getItem(key);
    const ids = raw ? (JSON.parse(raw) as unknown) : [];
    return new Set(Array.isArray(ids) ? ids.filter((x): x is string => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

function markId(key: string, id: string): void {
  try {
    const ids = Array.from(readIds(key)).concat(id).slice(-KEEP);
    sessionStorage.setItem(key, JSON.stringify(ids));
  } catch {
    /* storage blocked: at worst a reload says it again (or calls it "Said") */
  }
}

const readSpoken = () => readIds(SPOKEN_KEY);

export function useSpeakAloud(
  line: SpokenLine | null,
  enabled: boolean,
): { speaking: boolean; played: ReadonlySet<string> } {
  const [speaking, setSpeaking] = useState(false);
  const [played, setPlayed] = useState<ReadonlySet<string>>(() => readIds(PLAYED_KEY));
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    const visible = typeof document === "undefined" || document.visibilityState !== "hidden";
    if (!line || !shouldSpeak(line, { enabled, nowMs: Date.now(), visible, spoken: readSpoken() })) return;
    markId(SPOKEN_KEY, line.id);
    const id = line.id;
    const ac = new AbortController();
    let url: string | null = null;
    synthesizeSpeech(line.text, ac.signal)
      .then((buf) => {
        if (ac.signal.aborted || typeof URL.createObjectURL !== "function" || typeof Audio === "undefined") return;
        url = URL.createObjectURL(new Blob([buf], { type: "audio/mpeg" }));
        const audio = new Audio(url);
        audioRef.current = audio;
        const done = () => {
          setSpeaking(false);
          if (url) URL.revokeObjectURL(url);
          url = null;
        };
        audio.onplaying = () => {
          setSpeaking(true);
          markId(PLAYED_KEY, id);
          setPlayed((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
        };
        audio.onended = done;
        audio.onerror = done;
        // Refused (no gesture yet on this tab): keep quiet; the next line's
        // cleanup frees the audio, so the element never loads a revoked URL.
        audio.play().catch(() => setSpeaking(false));
      })
      .catch(() => {
        /* silent: the words are on the screen */
      });
    return () => {
      ac.abort();
      audioRef.current?.pause();
      audioRef.current = null;
      setSpeaking(false);
      if (url) URL.revokeObjectURL(url);
    };
  }, [line?.id, enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  return { speaking, played };
}
