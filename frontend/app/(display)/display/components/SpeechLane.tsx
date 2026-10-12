"use client";

// What he said, beside him (say_on_screen, 2026-09-28). An emerald overline
// ("Said aloud · 5:52 pm" only when its audio really played, else "Said ·
// 5:52 pm"; the clock keeps its lower-case pm), the line before it muted, the new line large with
// a still emerald caret — his words are his, so they carry his light. It
// enters once and sits; after three minutes it goes (lib/stageModel.ts::speechView).

import React, { useRef, type CSSProperties } from "react";
import { Volume2 } from "lucide-react";

import { formatClock } from "@/lib/time";

import { useFitText } from "../hooks/useFitText";
import type { SpeechView } from "../lib/stageModel";

interface Props {
  speech: SpeechView;
  /** The current line's audio really played (useSpeakAloud's `played`). */
  playedAloud?: boolean;
  tz?: string;
  side: "left" | "right";
  style: CSSProperties;
}

export function SpeechLane({ speech, playedAloud = false, tz, side, style }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const current = speech.current;
  useFitText(boxRef, textRef, `${current?.id ?? ""}|${speech.previous?.id ?? ""}`);
  if (!current) return null;
  return (
    <div ref={boxRef} className="st-lane st-speech" data-lane="speech" data-side={side} style={style} aria-live="polite">
      <div className="st-speech-inner" key={current.id}>
        <div className="st-overline st-overline-edward st-speech-over">
          {playedAloud && <Volume2 size={14} aria-hidden />}
          <span>
            {playedAloud ? "Said aloud" : "Said"} · <span className="st-overline-clock">{formatClock(current.at, { tz })}</span>
          </span>
        </div>
        {speech.previous && <div className="st-speech-previous">{speech.previous.text}</div>}
        <div ref={textRef} className="st-speech-current">
          {current.text}
          <span className="st-caret" aria-hidden />
        </div>
      </div>
    </div>
  );
}
