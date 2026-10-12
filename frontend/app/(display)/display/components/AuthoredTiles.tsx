"use client";

// The pieces Edward writes himself on the stage (compose_screen, 2026-09-28):
// words in big set type, a note (his written card), a list, a stat, a
// countdown, a timer, a picture, a video, an HTML scene. Calm cards or set
// type — nothing loops; a timer's digits change once a second, which is the
// time, not an animation. Live panels have their own components.
//
// Only his own words carry his emerald (a note's overline); everything else
// is ink. An HTML scene runs sandboxed (`allow-scripts`, no same origin); a
// video embeds the way the show layer does (ScreenOverlay).

import React, { useEffect, useRef, useState } from "react";

import { formatClock } from "@/lib/time";

import { useFitText } from "../hooks/useFitText";
import { countdownLabel, listItems } from "../lib/screenModel";
import { timerView, type StagePiece } from "../lib/stageModel";

interface PieceProps {
  piece: StagePiece;
}

/** A clock that ticks while mounted, starting from the stage's own now. */
function useTick(startMs: number, everyMs: number): number {
  const [now, setNow] = useState(startMs);
  useEffect(() => setNow(startMs), [startMs]);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), everyMs);
    return () => window.clearInterval(id);
  }, [everyMs]);
  return now;
}

export function WordsPiece({ piece }: PieceProps) {
  const boxRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  useFitText(boxRef, textRef, `${piece.size}|${piece.text}|${piece.title ?? ""}`);
  return (
    <div ref={boxRef} className="st-words" data-size={piece.size}>
      <div ref={textRef} className="st-words-text">{piece.text}</div>
      {piece.title && <div className="st-words-caption">{piece.title}</div>}
    </div>
  );
}

export function NotePiece({ piece, signed }: PieceProps & { signed: string | null }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  useFitText(boxRef, textRef, `${piece.size}|${piece.body}|${piece.title ?? ""}`);
  return (
    <div ref={boxRef} className="st-card st-note">
      {piece.title && <div className="st-overline st-overline-edward">{piece.title}</div>}
      <div ref={textRef} className="st-note-body">{piece.body}</div>
      {signed && <div className="st-meta">{signed}</div>}
    </div>
  );
}

export function ListPiece({ piece }: PieceProps) {
  const items = listItems(piece.items);
  return (
    <div className="st-card st-list">
      {piece.title && <div className="st-card-title">{piece.title}</div>}
      <div className="st-list-rows">
        {items.map((it, i) => (
          <div key={i} className="st-list-row">
            <span className="st-list-label">{it.label}</span>
            {it.detail && <span className="st-list-detail">{it.detail}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

export function StatPiece({ piece }: PieceProps) {
  return (
    <div className="st-stat">
      {piece.title && <div className="st-overline">{piece.title}</div>}
      <div className="st-stat-value">{piece.value}</div>
      {piece.detail && <div className="st-stat-detail">{piece.detail}</div>}
    </div>
  );
}

export function CountdownPiece({ piece, nowMs }: PieceProps & { nowMs: number }) {
  const now = useTick(nowMs, 30_000);
  return (
    <div className="st-stat">
      {piece.title && <div className="st-overline">{piece.title}</div>}
      <div className="st-stat-value">{countdownLabel(piece.target ?? "", now)}</div>
      {piece.detail && <div className="st-stat-detail">{piece.detail}</div>}
    </div>
  );
}

export function TimerPiece({ piece, nowMs, tz }: PieceProps & { nowMs: number; tz?: string }) {
  const now = useTick(nowMs, 1_000);
  const t = timerView(piece, now);
  return (
    <div className="st-timer" data-done={t.done ? "true" : undefined}>
      {piece.title && <div className="st-overline">{piece.title}</div>}
      <div className="st-timer-digits">{t.remaining}</div>
      <div className="st-timer-bar" aria-hidden>
        <span style={{ width: `${Math.round(t.progress * 100)}%` }} />
      </div>
      {piece.ends_at && (
        <div className="st-timer-when">Done at {formatClock(piece.ends_at, { tz })}</div>
      )}
    </div>
  );
}

export function ImagePiece({ piece }: PieceProps) {
  return (
    <figure className="st-media">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img className="st-media-img" src={piece.source ?? ""} alt={piece.title ?? "A picture Edward put up"} />
      {piece.title && <figcaption className="st-meta">{piece.title}</figcaption>}
    </figure>
  );
}

export function VideoPiece({ piece }: PieceProps) {
  return (
    <figure className="st-media">
      {piece.embed_url ? (
        <iframe
          className="st-media-frame"
          src={piece.embed_url}
          title={piece.title ?? "A video Edward put up"}
          allow="autoplay; encrypted-media; fullscreen"
        />
      ) : (
        <video className="st-media-img" src={piece.source ?? ""} autoPlay muted playsInline controls />
      )}
      {piece.title && <figcaption className="st-meta">{piece.title}</figcaption>}
    </figure>
  );
}

export function HtmlPiece({ piece }: PieceProps) {
  return (
    <div className="st-media">
      <iframe className="st-media-frame" sandbox="allow-scripts" srcDoc={piece.body ?? ""} title={piece.title ?? "scene"} />
    </div>
  );
}
