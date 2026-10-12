"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUp, Mic, Type, X } from "lucide-react";

import { useVoiceRecorder } from "@/hooks/useVoiceRecorder";
import { transcribeAudio } from "@/lib/api";
import { cn } from "@/lib/utils";

interface VoiceMemoButtonProps {
  disabled?: boolean;
  /** Post the clip as a voice memo (attachment + server-side transcript). */
  onMemo: (file: File) => void;
  /** Drop a transcript into the text box for editing. */
  onTranscript: (text: string) => void;
  /** Lets the parent hide the text box while recording. */
  onRecordingChange?: (recording: boolean) => void;
}

function formatElapsed(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// 44px hit, 36px face — the composer's round-button recipe.
const HIT =
  "group/btn relative inline-flex h-11 flex-none items-center justify-center rounded-full touch-manipulation outline-none " +
  "disabled:pointer-events-none disabled:opacity-40 focus-visible:[&>span]:ring-2 focus-visible:[&>span]:ring-terminal/60";
const FACE =
  "inline-flex h-9 items-center justify-center gap-1.5 rounded-full transition-[transform,background-color,color] duration-press " +
  "ease-out group-active/btn:scale-[.94] [&_svg]:h-4 [&_svg]:w-4";

/**
 * The mic button in the chat input.
 *
 * Tap to start recording. While recording: "Send" posts the clip as a
 * memo (Edward gets the transcript, you keep the audio), "Text" puts the
 * transcript in the box so you can edit before sending, X discards.
 */
export function VoiceMemoButton({ disabled, onMemo, onTranscript, onRecordingChange }: VoiceMemoButtonProps) {
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { state, start, stop, cancel } = useVoiceRecorder({
    onClip: async (file, intent) => {
      if (intent === "send") {
        onMemo(file);
        return;
      }
      setTranscribing(true);
      try {
        const result = await transcribeAudio(file, "en", file.name);
        if (result.text.trim()) {
          onTranscript(result.text.trim());
        } else {
          setError("I didn't catch any words in that.");
        }
      } catch {
        setError("I couldn't turn that into text.");
      } finally {
        setTranscribing(false);
      }
    },
  });

  // Tell the parent only once recording has really started (a mic that
  // fails to open must not hide the text box), and again when it ends.
  const onRecordingChangeRef = useRef(onRecordingChange);
  useEffect(() => {
    onRecordingChangeRef.current = onRecordingChange;
  }, [onRecordingChange]);
  useEffect(() => {
    onRecordingChangeRef.current?.(state.phase === "recording" || state.phase === "finishing");
  }, [state.phase]);

  // A note fades on its own after a few seconds.
  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 4000);
    return () => clearTimeout(t);
  }, [error]);

  const handleStart = useCallback(async () => {
    setError(null);
    await start();
  }, [start]);

  const message = error || state.error;

  if (state.phase === "recording" || state.phase === "finishing") {
    const finishing = state.phase === "finishing";
    return (
      <div className="flex min-h-[44px] min-w-0 flex-1 items-center gap-1 pl-1.5" role="group" aria-label="Recording a voice memo">
        <span className="flex min-w-0 items-center gap-2 text-footnote text-text-secondary">
          <span className="relative flex h-2.5 w-2.5 flex-none items-center justify-center" aria-hidden>
            <span className="absolute inset-0 rounded-full bg-text-primary/25 motion-safe:animate-ping" />
            <span className="h-2 w-2 rounded-full bg-text-primary" />
          </span>
          <span className="tabular-nums text-text-primary">{formatElapsed(state.elapsedMs)}</span>
          <span className="hidden truncate sm:inline">{finishing ? "Finishing…" : "Recording"}</span>
        </span>
        <span className="flex-1" />
        <button type="button" onClick={cancel} disabled={finishing} className={cn(HIT, "w-11")} aria-label="Discard recording" title="Discard">
          <span className={cn(FACE, "w-9 text-text-muted group-hover/btn:bg-surface-3 group-hover/btn:text-text-primary")}>
            <X aria-hidden />
          </span>
        </button>
        <button type="button" onClick={() => stop("text")} disabled={finishing} className={HIT} title="Turn it into text to edit">
          <span className={cn(FACE, "border border-border bg-surface-3 px-3 text-footnote font-medium text-text-primary group-hover/btn:bg-surface-elevated")}>
            <Type aria-hidden /> Text
          </span>
        </button>
        <button type="button" onClick={() => stop("send")} disabled={finishing} className={HIT} title="Send as a voice memo">
          <span className={cn(FACE, "bg-text-primary px-3 text-footnote font-semibold text-primary-bg shadow-e1")}>
            <ArrowUp aria-hidden strokeWidth={2.5} /> Send
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className="relative flex-none">
      <button
        type="button"
        onClick={handleStart}
        disabled={disabled || transcribing}
        className={cn(HIT, "w-11")}
        aria-label={transcribing ? "Turning your memo into text" : "Record a voice memo"}
        title="Record a voice memo"
        aria-busy={transcribing || undefined}
      >
        <span className={cn(FACE, "w-9 text-text-muted group-hover/btn:bg-surface-3 group-hover/btn:text-text-primary")}>
          {transcribing ? <span className="h-2 w-2 rounded-full bg-terminal/80" aria-hidden /> : <Mic aria-hidden />}
        </span>
      </button>
      {message && (
        <span
          role="status"
          className="absolute bottom-full right-0 mb-2 w-max max-w-[16rem] animate-fade-rise rounded-md border border-border bg-surface-elevated px-2.5 py-1.5 text-caption text-text-secondary shadow-e2"
        >
          {message}
        </span>
      )}
    </div>
  );
}
