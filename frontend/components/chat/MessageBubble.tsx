"use client";

// One message of the transcript (2026-09-26).
//
//   Owner   — a neutral, high-contrast bubble on the right (emerald means
//             Edward, so the owner's words never wear it); body 15/22.
//   Edward  — no bubble, no avatar: the work rail (what he did), then the
//             answer in the reading column, then Copy · Retry · Read aloud
//             once it is finished (low emphasis on touch, on hover with a
//             mouse; always shown under the last answer).
//
// Memoised: while a turn streams only the live message re-renders.
// `live` comes from the turn model (ChatContext streamingMessageId), never
// from the content being empty.

import React, { memo, useCallback, useEffect, useRef, useState } from "react";
import { Check, Clock, Copy, Download, FileText, Minus, Pause, Play, RotateCcw, Square, Volume2, X, Activity } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Message, MessageAttachment } from "@/lib/ChatContext";
import type { Turn } from "@/lib/turnModel";
import { isPackMessage } from "@/lib/packMessage";
import { describeTrigger, speakableText } from "@/lib/answerText";
import { withoutUnfinishedTable } from "@/lib/partialMarkdown";
import { synthesizeSpeech } from "@/lib/api";
import { Notice } from "@/components/ui/Feedback";
import { MarkdownContent } from "./MarkdownContent";
import { WorkRail } from "./WorkRail";
import { PackCard } from "./PackCard";

interface MessageBubbleProps {
  message: Message;
  /** The turn streams into this message. */
  live?: boolean;
  /** ChatContext's `turn` when it belongs to this message, else null. */
  turn?: Turn | null;
  /** The question to send again (the last answer only). */
  retryText?: string;
  onRetry?: (text: string) => void;
  /** Voice is set up: offer Read aloud. */
  canReadAloud?: boolean;
  /** The newest answer: its actions stay visible with a mouse too. */
  isLast?: boolean;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function attachmentSrc(a: MessageAttachment): string | undefined {
  return a.preview_url || (a.file_id ? `/api/files/${a.file_id}/download` : undefined);
}

function AttachmentImage({ attachment }: { attachment: MessageAttachment }) {
  const [fullscreen, setFullscreen] = useState(false);
  const src = attachmentSrc(attachment);
  if (!src) return null;
  return (
    <>
      <button
        type="button"
        onClick={() => setFullscreen(true)}
        className="overflow-hidden rounded-md border border-border transition-transform duration-press active:scale-[.97] focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
        aria-label={`Open ${attachment.filename}`}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={src} alt={attachment.filename} className="max-h-[200px] max-w-[200px] object-cover" />
      </button>
      {fullscreen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4"
          onClick={() => setFullscreen(false)}
          role="dialog"
          aria-label={attachment.filename}
        >
          <button
            type="button"
            onClick={() => setFullscreen(false)}
            className="absolute right-3 top-[max(env(safe-area-inset-top),12px)] flex h-11 w-11 items-center justify-center rounded-full text-white/80 hover:text-white"
            aria-label="Close"
          >
            <X className="h-6 w-6" />
          </button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt={attachment.filename}
            className="max-h-[90vh] max-w-[92vw] rounded-lg object-contain"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </>
  );
}

function AttachmentBadge({ attachment }: { attachment: MessageAttachment }) {
  const downloadUrl = attachment.file_id ? `/api/files/${attachment.file_id}/download` : undefined;
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-sm border border-border bg-surface px-2 py-1 text-caption font-normal">
      <FileText className="h-3.5 w-3.5 flex-none text-text-muted" aria-hidden />
      <span className="truncate text-text-secondary">{attachment.filename}</span>
      <span className="flex-none tabular-nums text-text-muted">{formatFileSize(attachment.size)}</span>
      {downloadUrl && (
        <a
          href={downloadUrl}
          download={attachment.filename}
          className="relative ml-0.5 text-text-secondary after:absolute after:-inset-2.5 after:content-[''] hover:text-text-primary"
          aria-label={`Download ${attachment.filename}`}
          onClick={(e) => e.stopPropagation()}
        >
          <Download className="h-3.5 w-3.5" />
        </a>
      )}
    </span>
  );
}

function clock(seconds: number): string {
  const t = Math.max(0, Math.floor(seconds));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

/**
 * A voice memo: play / pause, progress and the length in tabular numbers,
 * driving a hidden <audio> — never the browser's own grey player, which
 * sat off-theme in the bubble (2026-09-26 review). The owner's colours (neutral),
 * since the memo is theirs.
 */
function AttachmentAudio({ attachment }: { attachment: MessageAttachment }) {
  const src = attachmentSrc(attachment);
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState<number | null>(null);
  const [current, setCurrent] = useState(0);
  if (!src) return null;

  const toggle = () => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) {
      setPlaying(true);
      void Promise.resolve(a.play()).catch(() => setPlaying(false));
    } else {
      a.pause();
      setPlaying(false);
    }
  };
  const readDuration = () => {
    const d = audioRef.current?.duration;
    if (typeof d === "number" && Number.isFinite(d) && d > 0) setDuration(d);
  };
  const pct = duration ? Math.min(100, (current / duration) * 100) : 0;
  const time = playing || current > 0 ? clock(current) : duration ? clock(duration) : "Voice memo";

  return (
    <div className="flex w-[220px] max-w-full items-center gap-2.5 py-0.5">
      <button
        type="button"
        onClick={toggle}
        aria-label={playing ? "Pause voice memo" : "Play voice memo"}
        className="relative inline-flex h-9 w-9 flex-none items-center justify-center rounded-full bg-text-primary text-primary-bg shadow-e1 transition-transform duration-press after:absolute after:-inset-1 after:content-[''] active:scale-[.94] focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
      >
        {playing ? <Pause className="h-4 w-4 fill-current" aria-hidden /> : <Play className="h-4 w-4 translate-x-px fill-current" aria-hidden />}
      </button>
      <div
        role="progressbar"
        aria-label="Voice memo progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-text-muted/25"
      >
        <div className="h-full rounded-full bg-text-secondary" style={{ width: `${pct}%` }} />
      </div>
      <span className="flex-none text-caption tabular-nums text-text-muted">{time}</span>
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio
        ref={audioRef}
        src={src}
        preload="metadata"
        className="hidden"
        onLoadedMetadata={readDuration}
        onDurationChange={readDuration}
        onTimeUpdate={() => setCurrent(audioRef.current?.currentTime ?? 0)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false);
          setCurrent(0);
          readDuration();
        }}
      />
    </div>
  );
}

function Attachments({ attachments, alone = false }: { attachments: MessageAttachment[]; alone?: boolean }) {
  const images = attachments.filter((a) => a.mime_type.startsWith("image/"));
  const audio = attachments.filter((a) => a.mime_type.startsWith("audio/"));
  const files = attachments.filter((a) => !a.mime_type.startsWith("image/") && !a.mime_type.startsWith("audio/"));
  return (
    <div className={cn("space-y-1.5", !alone && "mb-2")}>
      {images.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {images.map((att) => (
            <AttachmentImage key={att.id} attachment={att} />
          ))}
        </div>
      )}
      {audio.map((att) => (
        <AttachmentAudio key={att.id} attachment={att} />
      ))}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {files.map((att) => (
            <AttachmentBadge key={att.id} attachment={att} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- triggers
function TriggerCard({ message }: { message: Message }) {
  const t = describeTrigger(message.content, message.triggerType);
  const Icon = t.kind === "heartbeat" ? Activity : Clock;
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3 shadow-e1">
      <p className="mb-1.5 flex items-center gap-2 text-overline text-text-muted">
        <Icon className="h-3.5 w-3.5" aria-hidden />
        {t.label}
      </p>
      {t.body ? <p className="text-footnote text-text-secondary [overflow-wrap:anywhere]">{t.body}</p> : null}
      {t.fields.length > 0 ? (
        <dl className={cn("space-y-1", t.body && "mt-1.5")}>
          {t.fields.map(([key, val], i) => (
            <div key={i} className="flex min-w-0 gap-2 text-footnote">
              <dt className="flex-none text-text-muted">{key}</dt>
              <dd className="min-w-0 text-text-secondary [overflow-wrap:anywhere]">{val}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- actions
let playing: HTMLAudioElement | null = null;

type ReadState = "idle" | "loading" | "playing" | "error";

// A cancel (the second tap, or leaving the thread) aborts the request that
// is still making the voice and invalidates it: a clip that arrives late is
// never played, and a deliberate cancel never reads as an error
// (2026-09-26 review — it used to start reading 2–4 s after the owner said
// no, over whatever thread they had moved to, with no Stop on screen).
function useReadAloud(text: string) {
  const [state, setState] = useState<ReadState>("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const reqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const cleanup = useCallback(() => {
    reqRef.current++;
    abortRef.current?.abort();
    abortRef.current = null;
    audioRef.current?.pause();
    if (playing === audioRef.current) playing = null;
    audioRef.current = null;
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = null;
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const toggle = useCallback(async () => {
    if (state === "playing" || state === "loading") {
      cleanup();
      setState("idle");
      return;
    }
    const my = ++reqRef.current;
    const ac = new AbortController();
    abortRef.current = ac;
    setState("loading");
    try {
      const buf = await synthesizeSpeech(speakableText(text), ac.signal);
      if (my !== reqRef.current) return;
      abortRef.current = null;
      const url = URL.createObjectURL(new Blob([buf], { type: "audio/mpeg" }));
      const audio = new Audio(url);
      playing?.pause();
      playing = audio;
      audioRef.current = audio;
      urlRef.current = url;
      audio.onended = () => {
        cleanup();
        setState("idle");
      };
      await audio.play();
      if (my !== reqRef.current) return;
      setState("playing");
    } catch {
      if (my !== reqRef.current || ac.signal.aborted) return;
      cleanup();
      setState("error");
      setTimeout(() => setState((s) => (s === "error" ? "idle" : s)), 2400);
    }
  }, [cleanup, state, text]);

  return { state, toggle };
}

function ActionButton({
  label,
  onClick,
  children,
  active,
  tone,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  active?: boolean;
  tone?: "bad";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={cn(
        "relative flex h-8 w-8 items-center justify-center rounded-sm transition-[color,background-color,transform] duration-press",
        "after:absolute after:-inset-1.5 after:content-[''] active:scale-[.97]",
        "hover:bg-surface-3 hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60",
        tone === "bad" ? "text-bad" : active ? "text-terminal-strong" : "text-text-muted",
        "[&_svg]:h-4 [&_svg]:w-4",
      )}
    >
      {children}
    </button>
  );
}

function AnswerActions({
  content,
  retryText,
  onRetry,
  canReadAloud,
  isLast,
}: {
  content: string;
  retryText?: string;
  onRetry?: (text: string) => void;
  canReadAloud?: boolean;
  isLast?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const read = useReadAloud(content);
  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable */
    }
  }, [content]);

  const readLabel =
    read.state === "playing" ? "Stop reading" : read.state === "loading" ? "Getting my voice ready" : read.state === "error" ? "I couldn't read that aloud" : "Read aloud";

  return (
    <div
      className={cn(
        "-ml-2 mt-1.5 flex items-center gap-1 transition-opacity duration-fast",
        !isLast && "[@media(hover:hover)]:opacity-0 [@media(hover:hover)]:focus-within:opacity-100 [@media(hover:hover)]:group-hover:opacity-100",
      )}
    >
      <ActionButton label={copied ? "Copied" : "Copy answer"} onClick={copy} active={copied}>
        {copied ? <Check className="motion-safe:animate-scale-in" /> : <Copy />}
      </ActionButton>
      {retryText && onRetry ? (
        <ActionButton label="Retry" onClick={() => onRetry(retryText)}>
          <RotateCcw />
        </ActionButton>
      ) : null}
      {canReadAloud ? (
        <ActionButton label={readLabel} onClick={read.toggle} active={read.state === "playing"} tone={read.state === "error" ? "bad" : undefined}>
          {read.state === "playing" ? (
            <Square className="!h-3.5 !w-3.5 fill-current" />
          ) : read.state === "loading" ? (
            <span className="h-1.5 w-1.5 rounded-full bg-terminal" />
          ) : read.state === "error" ? (
            <X />
          ) : (
            <Volume2 />
          )}
        </ActionButton>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- the message
function MessageBubbleImpl({ message, live = false, turn = null, retryText, onRetry, canReadAloud, isLast }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const hasAttachments = Boolean(message.attachments && message.attachments.length > 0);

  if (message.isTrigger) return <TriggerCard message={message} />;

  // A mind/pulse signal pack is what Edward was shown, not something the
  // owner said: one folded line instead of a 15K-char bubble (lib/packMessage.ts).
  if (isUser && !hasAttachments && isPackMessage(message.content)) return <PackCard text={message.content} />;

  if (isUser) {
    return (
      <div className="flex justify-end pl-10">
        <div
          data-testid="user-bubble"
          className="min-w-0 max-w-[85%] rounded-lg rounded-br-xs border border-border bg-surface-3 px-3.5 py-2.5 text-body text-text-primary shadow-e1 md:max-w-[75%]"
        >
          {hasAttachments && <Attachments attachments={message.attachments!} alone={!message.content} />}
          {message.content && <p className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{message.content}</p>}
        </div>
      </div>
    );
  }

  const finished = !live && Boolean(message.content.trim());
  return (
    <div className="group min-w-0">
      <WorkRail message={message} live={live} turn={turn} />

      {/* Debug — per-memory match list (gated server-side by settings.debug_streaming). */}
      {message.memoryMatches && message.memoryMatches.length > 0 && (
        <div className="mb-3 rounded-sm border-l-2 border-warn/50 bg-surface px-3 py-2 text-caption font-normal text-text-muted" data-testid="memory-matches">
          <p className="mb-1 text-overline">Memory matches ({message.memoryMatches.length})</p>
          {message.memoryMatches.map((mm, i) => (
            <p key={mm.id ?? i} className="[overflow-wrap:anywhere]">
              <span className="opacity-70">
                {mm.type ?? "?"}
                {mm.score != null ? ` · ${mm.score.toFixed(2)}` : ""}
              </span>{" "}
              — {mm.preview}
            </p>
          ))}
        </div>
      )}

      {hasAttachments && <Attachments attachments={message.attachments!} />}

      {message.content && (
        <div className="text-body text-text-primary">
          {/* A table still being written renders as raw pipes until its separator row lands: held back while live, dropped once stopped. */}
          <MarkdownContent content={live || message.wasInterrupted ? withoutUnfinishedTable(message.content) : message.content} />
        </div>
      )}

      {message.wasCutOff && !live ? (
        <Notice
          tone="warn"
          className="mt-3"
          action={
            retryText && onRetry ? (
              <button
                type="button"
                onClick={() => onRetry(retryText)}
                className="relative inline-flex min-h-[32px] items-center gap-1 rounded-xs px-1 font-medium transition-opacity duration-fast after:absolute after:-inset-1.5 after:content-[''] hover:opacity-80 focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
              >
                <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                Try again
              </button>
            ) : null
          }
        >
          {message.content.trim() ? "I got cut off before finishing." : "I couldn't finish that — try again?"}
        </Notice>
      ) : null}

      {message.wasInterrupted && (
        <p className="mt-3 flex items-center gap-2 text-footnote text-text-muted">
          <Minus className="h-3.5 w-3.5" aria-hidden />
          You stopped me here.
          {/* Stopped before any answer: no actions row below, so the retry lives here. */}
          {!finished && retryText && onRetry ? (
            <button
              type="button"
              onClick={() => onRetry(retryText)}
              className="relative ml-1 inline-flex items-center gap-1 rounded-xs font-medium text-text-secondary transition-colors duration-fast after:absolute after:-inset-2.5 after:content-[''] hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden />
              Try again
            </button>
          ) : null}
        </p>
      )}

      {finished ? (
        <AnswerActions content={message.content} retryText={retryText} onRetry={onRetry} canReadAloud={canReadAloud} isLast={isLast} />
      ) : null}
    </div>
  );
}

export const MessageBubble = memo(MessageBubbleImpl);
