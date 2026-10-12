"use client";

// His thinking beside him while a mind or surfaces turn really runs
// (GET /api/screen/live, 2026-09-28): a still emerald pip, "Thinking · reading
// your messages", since when, then the stream — his reasoning summaries as
// short lines that fade with age and the receipts of what he did, tool names
// in his words, never arguments or results. The newest line is large with a
// still caret. When the turn ends it folds into one "Earlier · …" line.
// Otherwise (round 2) his mind: one recent thought at a time, "On my mind ·
// 3:10 pm" — its own time, no pip, no caret, quieter than live — changing
// with a single cross-fade. Nothing here loops; each line enters once.

import React, { useEffect, useRef, useState, type CSSProperties } from "react";
import { Check, ChevronRight, Circle, X } from "lucide-react";

import type { ThoughtView } from "../lib/thinkingModel";

interface Props {
  view: ThoughtView;
  side: "left" | "right";
  style: CSSProperties;
}

function ReceiptIcon({ state }: { state: "running" | "ok" | "error" }) {
  if (state === "ok") return <Check size={12} aria-hidden className="st-receipt-icon" />;
  if (state === "error") return <X size={12} aria-hidden className="st-receipt-icon" />;
  return <Circle size={12} aria-hidden className="st-receipt-icon" />;
}

export function ThoughtLane({ view, side, style }: Props) {
  if (!view.active) return null;
  return (
    <div className="st-lane st-thought" data-lane="thinking" data-side={side} style={style}>
      <div className="st-thought-head">
        <div className="st-thought-title">
          <span className="st-pip" aria-hidden />
          <span>{view.about ? `Thinking · ${view.about}` : "Thinking"}</span>
        </div>
        {view.since && <span className="st-thought-since">since {view.since}</span>}
      </div>
      <div className="st-thought-stream">
        {view.stream.map((item) =>
          item.type === "line" ? (
            <div key={item.key} className="st-thought-line" data-newest={item.newest ? "true" : undefined} style={{ opacity: item.opacity }}>
              {item.text}
              {item.newest && <span className="st-caret" aria-hidden />}
            </div>
          ) : (
            <div key={item.key} className="st-receipt" data-state={item.state} style={{ opacity: Math.max(item.opacity, 0.6) }}>
              <ReceiptIcon state={item.state} />
              <span>{item.label}</span>
            </div>
          ),
        )}
      </div>
    </div>
  );
}

/** The last finished turn, one line on the lane's floor. */
export function FoldLine({ text, style }: { text: string; style: CSSProperties }) {
  return (
    <div className="st-lane st-fold" data-lane="fold" style={style}>
      <div className="st-fold-line">
        <ChevronRight size={12} aria-hidden />
        <span>{text}</span>
      </div>
    </div>
  );
}

export interface MindItem {
  key: string;
  /** "On my mind", "Daydreaming", "Dreaming". */
  label: string;
  /** The thought's own time, "3:10 pm". */
  time: string;
  text: string;
}

/** How long the old thought takes to fade out under the new one (display.css .st-mind-item[data-leaving]). */
const CROSS_FADE_MS = 900;

function MindBody({ item, leaving }: { item: MindItem; leaving?: boolean }) {
  return (
    <div className="st-mind-item" data-leaving={leaving ? "true" : undefined} aria-hidden={leaving || undefined}>
      <div className="st-overline st-mind-over">
        {item.label} · <span className="st-overline-clock">{item.time}</span>
      </div>
      <div className="st-mind-text">{item.text}</div>
    </div>
  );
}

/** A recent thought on his mind, labelled as past; a new one cross-fades over it once. */
export function MindLane({ item, side, style }: { item: MindItem; side: "left" | "right"; style: CSSProperties }) {
  const last = useRef<MindItem>(item);
  const now = useRef<MindItem>(item);
  now.current = item;
  const [leaving, setLeaving] = useState<MindItem | null>(null);
  useEffect(() => {
    const prev = last.current;
    last.current = now.current;
    if (prev.key === item.key) return;
    setLeaving(prev);
    const id = window.setTimeout(() => setLeaving(null), CROSS_FADE_MS);
    return () => window.clearTimeout(id);
  }, [item.key]);
  return (
    <div className="st-lane st-mind" data-lane="mind" data-side={side} style={style}>
      <div className="st-mind-stack">
        {leaving && leaving.key !== item.key && <MindBody key={`out|${leaving.key}`} item={leaving} leaving />}
        <MindBody key={item.key} item={item} />
      </div>
    </div>
  );
}
