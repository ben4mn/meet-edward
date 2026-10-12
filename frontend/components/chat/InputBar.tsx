"use client";

// The chat composer (2026-09-26 chat chrome; design system §3, §5).
//
// One surface: attachments on top, then paperclip · text · memo mic · the
// primary slot. The primary slot follows lib/composerAction.ts: nothing
// typed summons Edward (talk live, his emerald), something typed sends (a
// neutral ArrowUp — the owner's own action), and while Edward works typed text
// queues (chips above the box, "sends next") with Stop, a quiet
// square-in-a-ring, beside it. Enter follows lib/composerAction.ts
// enterKeyIntent (a newline on the phone, send on a desktop keyboard).
// Drafts are per conversation (lib/drafts.ts). The shell owns the home
// indicator; the composer pads by its --shell-inset-bottom
// (composerInsetClass). While Edward streams the surface carries a faint
// emerald edge — presence, not decoration.

import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
} from "react";
import { AnimatePresence, LazyMotion, m } from "framer-motion";
import { MOTION_FEATURES } from "@/components/ui/motionFeatures";
import { ArrowUp, AudioLines, FileText, Mic, Paperclip, X } from "lucide-react";

import { VoiceMemoButton } from "./VoiceMemoButton";
import { cn } from "@/lib/utils";
import { setComposing } from "@/lib/useComposing";
import {
  composerInsetClass,
  enterAction,
  enterKeyHintFor,
  enterKeyIntent,
  primaryAction,
  showsSeparateStop,
  type ComposerState,
} from "@/lib/composerAction";
import { queueChipLabel } from "@/lib/composerQueue";
import { clearDraft, draftTransition, readDraft, writeDraft } from "@/lib/drafts";
import { clearDraftFiles, readDraftFiles, rekeyDraftFiles, subscribeDraftFiles, writeDraftFiles } from "@/lib/draftFiles";
import { useBuildReloadHold } from "@/lib/buildReload";
import { syncObjectUrls } from "@/lib/objectUrls";
import { useChat, useComposerQueue } from "@/lib/ChatContext";
import { DURATION, EASE_OUT, useMediaQuery, usePrefersReducedMotion } from "@/components/ui/motion";

export interface InputBarProps {
  onSend: (message: string, files?: File[]) => void;
  onStop?: () => void;
  onEnterVoiceMode?: () => void;
  isLoading: boolean;
  canStop?: boolean;
  voiceEnabled?: boolean;
  // Text to put in the composer for the owner to send (an idea's "Try it" on
  // Today, 2026-09-24). Never sent by itself. A string is applied each time
  // it changes; {text, nonce} each time the nonce changes, so the same
  // "Try it" twice refills a box the owner cleared (L2, 2026-09-27).
  prefill?: ComposerPrefill;
}

export type ComposerPrefill = string | { text: string; nonce: number | string };

/** What the chat page can do to the composer (the column-wide drop target). */
export interface InputBarHandle {
  addFiles: (files: FileList | File[]) => void;
  focus: () => void;
}

export const ACCEPTED_TYPES =
  "image/png,image/jpeg,image/gif,image/webp,application/pdf,text/plain,text/csv,application/json,audio/*";
const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB
const MAX_TEXTAREA_PX = 6 * 24 + 20; // six lines of 24px + padding

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isImage(f: File): boolean {
  return f.type.startsWith("image/");
}

/** A mouse or trackpad (desktop): focus may move to the box without
 *  raising a phone keyboard. Read at the moment, never from a hydrating hook. */
function pointerIsFine(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(pointer: fine)").matches;
}

/** useChat outside a ChatProvider (a test, a stray mount) is simply absent. */
function useOptionalChat() {
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    return useChat();
  } catch {
    return null;
  }
}

/** One object URL per image, made once, revoked when it leaves. */
function usePreviewUrls(files: File[]): Map<File, string> {
  const ref = useRef<Map<File, string>>(new Map());
  const urls = useMemo(() => {
    const next = syncObjectUrls(
      ref.current,
      files,
      (f) => URL.createObjectURL(f),
      (u) => URL.revokeObjectURL(u),
      isImage,
    );
    ref.current = next;
    return next;
  }, [files]);
  useEffect(
    () => () => {
      ref.current.forEach((u) => URL.revokeObjectURL(u));
      ref.current = new Map();
    },
    [],
  );
  return urls;
}

// ---- the round buttons --------------------------------------------------
// 44px hit area, a 36px drawn circle inside it.

const HIT =
  "group/btn relative inline-flex h-11 w-11 flex-none items-center justify-center rounded-full touch-manipulation " +
  "outline-none disabled:pointer-events-none focus-visible:[&>span]:ring-2 focus-visible:[&>span]:ring-terminal/60 " +
  "focus-visible:[&>span]:ring-offset-2 focus-visible:[&>span]:ring-offset-surface";
const FACE =
  "inline-flex h-9 w-9 items-center justify-center rounded-full transition-[transform,background-color,color,opacity,box-shadow] " +
  "duration-press ease-out group-active/btn:scale-[.92] [&_svg]:h-[18px] [&_svg]:w-[18px]";

function RoundButton({
  label,
  title,
  face,
  onClick,
  disabled,
  children,
}: {
  label: string;
  title?: string;
  face: string;
  onClick?: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} aria-label={label} title={title ?? label} className={HIT}>
      <span className={cn(FACE, face)}>{children}</span>
    </button>
  );
}

const FACE_SEND = "bg-text-primary text-primary-bg shadow-e1 group-hover/btn:bg-text-primary/90";
const FACE_SEND_OFF = "bg-surface-3 text-text-muted";
const FACE_EDWARD = "bg-terminal text-terminal-ink shadow-e1 group-hover/btn:bg-terminal/90";
const FACE_GHOST = "text-text-muted group-hover/btn:bg-surface-3 group-hover/btn:text-text-primary";

/** Stop: a filled square inside a ring — clear, never alarming. */
function StopFace() {
  return (
    <span className={cn(FACE, "text-text-primary group-hover/btn:bg-surface-3")}>
      <svg viewBox="0 0 36 36" className="!h-9 !w-9" aria-hidden>
        <circle cx="18" cy="18" r="16.25" fill="none" stroke="currentColor" strokeOpacity="0.55" strokeWidth="1.5" />
        <rect x="13" y="13" width="10" height="10" rx="2" fill="currentColor" />
      </svg>
    </span>
  );
}

// ---- the composer ---------------------------------------------------------

export const InputBar = forwardRef<InputBarHandle, InputBarProps>(function InputBar(
  { onSend, onStop, onEnterVoiceMode, isLoading, canStop = false, voiceEnabled = false, prefill },
  ref,
) {
  const chat = useOptionalChat();
  const conversationId = chat?.conversationId;
  const streamingConversationId = chat?.streamingConversationId ?? null;
  const isLoadingConversation = chat?.isLoadingConversation ?? false;

  const [message, setMessage] = useState(() => readDraft(conversationId));
  const [files, setFiles] = useState<File[]>(() => readDraftFiles(conversationId));
  const [isRecording, setIsRecording] = useState(false);
  const [focused, setFocused] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const coarse = useMediaQuery("(pointer: coarse)");
  const reduced = usePrefersReducedMotion();
  const previews = usePreviewUrls(files);

  // Queue while running (2026-09-24): what the owner types while Edward works
  // waits in the ChatProvider's queue and goes when the turn ends.
  const { queued, queueMessage, removeQueuedMessage, paused: queuePaused, resumeQueue } = useComposerQueue();

  const focusBox = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.focus({ preventScroll: true });
    const end = el.value.length;
    try {
      el.setSelectionRange(end, end);
    } catch {
      /* not a text control yet */
    }
  }, []);

  // ---- drafts: per conversation, handed over when a new chat gets its id.
  const shownIdRef = useRef(conversationId);
  // The stream's id as this effect last saw it: a rekey is only that id
  // being assigned (null → the new chat's id), never opening a thread that
  // was already streaming. Updated on every run, "none" included.
  const shownStreamRef = useRef(streamingConversationId);
  const messageRef = useRef(message);
  messageRef.current = message;
  const filesRef = useRef(files);
  filesRef.current = files;
  useEffect(() => {
    const prev = shownIdRef.current;
    const streamingBefore = shownStreamRef.current;
    shownStreamRef.current = streamingConversationId;
    const move = draftTransition(prev, conversationId, streamingConversationId, streamingBefore);
    shownIdRef.current = conversationId;
    if (move === "none") return;
    if (move === "rekey") {
      clearDraft(prev);
      writeDraft(conversationId, messageRef.current);
      rekeyDraftFiles(prev, conversationId);
      return;
    }
    writeDraft(prev, messageRef.current);
    setMessage(readDraft(conversationId));
    // Attachments stay with their thread too (L2, 2026-09-27): this used
    // to be setFiles([]), and a photo the owner attached was gone the
    // moment they looked at another thread.
    writeDraftFiles(prev, filesRef.current);
    setFiles(readDraftFiles(conversationId));
    // Desktop: the box is where the owner's hands are.
    if (pointerIsFine()) requestAnimationFrame(focusBox);
  }, [conversationId, streamingConversationId, focusBox]);

  // Desktop: the chat opens ready to type.
  useEffect(() => {
    if (pointerIsFine()) focusBox();
  }, [focusBox]);

  const updateMessage = useCallback(
    (text: string) => {
      setMessage(text);
      writeDraft(shownIdRef.current, text);
    },
    [],
  );

  // A new deploy never reloads the page out from under something typed or
  // attached (H3, 2026-09-27; lib/buildReload.ts), or a memo being
  // recorded: the owner talks for minutes with no tap, so the page reads as idle,
  // and a reload would take the recording with it (2026-09-27 review).
  useBuildReloadHold(message.trim().length > 0 || files.length > 0 || isRecording);

  // What is attached is kept for this thread as it changes, so leaving
  // /chat and coming back finds it (lib/draftFiles.ts).
  useEffect(() => {
    writeDraftFiles(shownIdRef.current, files);
  }, [files]);

  // After a page load, what this thread kept comes back from the tab's
  // stash a moment after the composer mounted (lib/draftFiles.ts).
  useEffect(
    () =>
      subscribeDraftFiles(() => {
        const kept = readDraftFiles(shownIdRef.current);
        if (kept.length && kept.length !== filesRef.current.length) setFiles(kept);
      }),
    [],
  );

  const prefillText = typeof prefill === "string" ? prefill : prefill?.text;
  const prefillNonce = typeof prefill === "string" ? undefined : prefill?.nonce;
  useEffect(() => {
    if (!prefillText) return;
    updateMessage(prefillText);
    focusBox();
  }, [prefillText, prefillNonce, updateMessage, focusBox]);

  const addFiles = useCallback((newFiles: FileList | File[]) => {
    const valid = Array.from(newFiles).filter((f) => f.size <= MAX_FILE_SIZE);
    if (valid.length) setFiles((prev) => [...prev, ...valid]);
  }, []);

  const removeFile = useCallback((index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  useImperativeHandle(ref, () => ({ addFiles, focus: focusBox }), [addFiles, focusBox]);

  // The shell's hotkeys: "/" focuses the box, Esc stops the turn.
  const canStopRef = useRef(canStop);
  canStopRef.current = canStop;
  const onStopRef = useRef(onStop);
  onStopRef.current = onStop;
  useEffect(() => {
    const focus = () => focusBox();
    const stop = () => {
      if (canStopRef.current) onStopRef.current?.();
    };
    window.addEventListener("edward:focus-composer", focus);
    window.addEventListener("edward:stop-turn", stop);
    return () => {
      window.removeEventListener("edward:focus-composer", focus);
      window.removeEventListener("edward:stop-turn", stop);
    };
  }, [focusBox]);

  const composer: ComposerState = {
    message,
    fileCount: files.length,
    isLoading,
    canStop,
    isRecording,
    voiceEnabled: voiceEnabled && Boolean(onEnterVoiceMode),
    canQueue: Boolean(queueMessage),
  };

  const handleSubmit = () => {
    const action = enterAction(composer);
    if (action === "none") return;
    const attached = files.length > 0 ? files : undefined;
    if (action === "queue") queueMessage?.(message, attached);
    else onSend(message, attached);
    setMessage("");
    setFiles([]);
    clearDraft(shownIdRef.current);
    clearDraftFiles(shownIdRef.current);
    if (pointerIsFine()) requestAnimationFrame(focusBox);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    const intent = enterKeyIntent({
      key: e.key,
      shiftKey: e.shiftKey,
      metaKey: e.metaKey,
      ctrlKey: e.ctrlKey,
      isComposing: e.nativeEvent.isComposing,
      keyCode: e.keyCode,
      coarse,
    });
    if (intent === "submit") {
      e.preventDefault();
      handleSubmit();
    }
  };

  // Voice memo: post the clip on its own (or with whatever was typed).
  const handleMemo = useCallback(
    (file: File) => {
      if (isLoading) return;
      onSend(message, [...files, file]);
      setMessage("");
      setFiles([]);
      clearDraft(shownIdRef.current);
      clearDraftFiles(shownIdRef.current);
    },
    [files, isLoading, message, onSend],
  );

  // Transcript into the box, appended to anything already typed.
  const handleTranscript = useCallback(
    (text: string) => {
      const prev = messageRef.current;
      updateMessage(prev.trim() ? `${prev.replace(/\s+$/, "")} ${text}` : text);
      focusBox();
    },
    [updateMessage, focusBox],
  );

  const handlePaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const images: File[] = [];
    for (const item of Array.from(items)) {
      if (item.type.startsWith("image/")) {
        const f = item.getAsFile();
        if (f) images.push(f);
      }
    }
    if (images.length > 0) addFiles(images);
  };

  useEffect(() => () => setComposing(false), []);

  // Grow with the text, up to six lines. The row holds its height while the
  // box is measured: "height: auto" alone collapses the composer for an
  // instant, the transcript above grows taller, and the browser clamps its
  // scroll position a line up — which used to drop the owner's view of the live
  // turn the moment a follow-up wrapped to a second line (2026-09-26 review).
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    const row = el.parentElement;
    const held = row ? row.style.minHeight : "";
    if (row) row.style.minHeight = `${row.offsetHeight}px`;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_PX)}px`;
    if (row) row.style.minHeight = held;
  }, [message, isRecording]);

  const primary = primaryAction(composer);
  const separateStop = showsSeparateStop(composer);
  const streaming = isLoading || canStop;

  const chipMotion = reduced
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 }, transition: { duration: 0.12 } }
    : {
        initial: { opacity: 0, y: 8, scale: 0.96 },
        animate: { opacity: 1, y: 0, scale: 1 },
        exit: { opacity: 0, scale: 0.96, transition: { duration: DURATION.fast * 0.7 } },
        transition: { duration: DURATION.base, ease: EASE_OUT },
      };

  return (
    <LazyMotion features={MOTION_FEATURES} strict>
      <div className={cn("flex-shrink-0 bg-primary-bg px-3 pt-2 sm:px-4", composerInsetClass())} data-composer="">
        <div className="mx-auto w-full max-w-3xl">
          {/* Queued messages: sent in order when the current turn ends. */}
          <div aria-label="Queued messages" aria-live="polite" className={cn(queued.length > 0 && "mb-2")}>
            <AnimatePresence initial={false}>
              {queued.map((item, index) => (
                <m.div
                  key={item.id}
                  {...chipMotion}
                  className="mb-1.5 flex justify-end last:mb-0"
                >
                  <div className="flex min-w-0 max-w-[85%] items-center gap-2 rounded-lg border border-border bg-surface py-1 pl-3 pr-1 shadow-e1">
                    <span className="min-w-0 truncate text-footnote text-text-primary">{queueChipLabel(item)}</span>
                    {/* After Stop the queue waits for the owner: the first chip says so and offers to send. */}
                    {index === 0 && queuePaused && resumeQueue ? (
                      <button
                        type="button"
                        onClick={resumeQueue}
                        className="relative flex-none rounded-xs text-caption font-medium text-text-primary underline decoration-text-muted/50 underline-offset-2 after:absolute after:-inset-2.5 after:content-[''] hover:decoration-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
                        aria-label={`Send queued message now: ${queueChipLabel(item)}`}
                      >
                        {isLoading ? "Send next" : "Send now"}
                      </button>
                    ) : (
                      <span className="flex-none text-caption text-text-muted">
                        {index === 0 ? (isLoadingConversation && !isLoading ? "sends when loaded" : "sends next") : "queued"}
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => removeQueuedMessage?.(item.id)}
                      className="relative inline-flex h-7 w-7 flex-none items-center justify-center rounded-full text-text-muted transition-colors duration-press after:absolute after:-inset-2 after:content-[''] hover:bg-surface-3 hover:text-text-primary active:scale-[.92]"
                      aria-label={`Remove queued message: ${queueChipLabel(item)}`}
                      title="Remove from the queue"
                    >
                      <X className="h-3.5 w-3.5" aria-hidden />
                    </button>
                  </div>
                </m.div>
              ))}
            </AnimatePresence>
          </div>

          <div
            className={cn(
              "rounded-xl border bg-surface shadow-e1 transition-[border-color,box-shadow] duration-base ease-out",
              focused ? "border-text-muted/35" : "border-border",
              streaming &&
                "border-terminal/25 shadow-[0_0_0_1px_rgb(var(--terminal-rgb)/0.10),0_0_28px_-12px_rgb(var(--terminal-rgb)/0.55)]",
            )}
            data-streaming={streaming ? "" : undefined}
          >
            {/* Attachments */}
            {files.length > 0 && (
              <div className="no-scrollbar flex gap-2 overflow-x-auto px-3 pt-3" aria-label="Attachments">
                {files.map((file, index) => {
                  const url = previews.get(file);
                  return (
                    <div key={`${file.name}-${index}`} className="group/att relative flex-none animate-scale-in">
                      {url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={url} alt={file.name} className="h-14 w-14 rounded-md border border-border object-cover" />
                      ) : (
                        <div className="flex h-14 max-w-[200px] items-center gap-2 rounded-md border border-border bg-surface-3 px-3">
                          {file.type.startsWith("audio/") ? (
                            <Mic className="h-4 w-4 flex-none text-text-muted" aria-hidden />
                          ) : (
                            <FileText className="h-4 w-4 flex-none text-text-muted" aria-hidden />
                          )}
                          <span className="min-w-0">
                            <span className="block truncate text-footnote text-text-primary">{file.name}</span>
                            <span className="block text-caption text-text-muted">{formatFileSize(file.size)}</span>
                          </span>
                        </div>
                      )}
                      <button
                        type="button"
                        onClick={() => removeFile(index)}
                        className="absolute -right-1.5 -top-1.5 inline-flex h-6 w-6 items-center justify-center rounded-full border border-border bg-surface-elevated text-text-secondary shadow-e1 transition-transform duration-press after:absolute after:-inset-2.5 after:content-[''] hover:text-text-primary active:scale-[.9]"
                        aria-label={`Remove ${file.name}`}
                      >
                        <X className="h-3 w-3" aria-hidden />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}

            <div className="flex items-end gap-0.5 p-0.5">
              <RoundButton
                label="Attach files"
                face={FACE_GHOST}
                onClick={() => fileInputRef.current?.click()}
                disabled={isRecording}
              >
                <Paperclip aria-hidden />
              </RoundButton>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={ACCEPTED_TYPES}
                onChange={(e) => {
                  if (e.target.files) addFiles(e.target.files);
                  e.target.value = ""; // the same file can be picked again
                }}
                className="hidden"
                tabIndex={-1}
                aria-hidden
              />

              {!isRecording && (
                <textarea
                  ref={textareaRef}
                  rows={1}
                  value={message}
                  onChange={(e) => updateMessage(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  onFocus={() => {
                    setFocused(true);
                    setComposing(true);
                  }}
                  onBlur={() => {
                    setFocused(false);
                    setComposing(false);
                  }}
                  placeholder="Message Edward"
                  aria-label="Message Edward"
                  enterKeyHint={enterKeyHintFor(coarse)}
                  autoComplete="off"
                  className="min-h-[44px] flex-1 resize-none self-center bg-transparent px-1.5 py-2.5 text-[16px] leading-6 text-text-primary outline-none placeholder:text-text-muted"
                  style={{ maxHeight: MAX_TEXTAREA_PX }}
                />
              )}

              {/* Voice memo: tap to record, then send as a memo or turn into text. */}
              {voiceEnabled && !streaming && (
                <VoiceMemoButton
                  disabled={isLoading}
                  onMemo={handleMemo}
                  onTranscript={handleTranscript}
                  onRecordingChange={setIsRecording}
                />
              )}

              {/* Stop beside the queue button, so queueing never costs the stop. */}
              {separateStop && (
                <button type="button" onClick={onStop} aria-label="Stop Edward" title="Stop (Esc)" className={HIT}>
                  <StopFace />
                </button>
              )}

              {primary === "stop" ? (
                <button type="button" onClick={onStop} aria-label="Stop Edward" title="Stop (Esc)" className={HIT}>
                  <StopFace />
                </button>
              ) : primary === "talk" ? (
                <RoundButton label="Talk live with Edward" face={FACE_EDWARD} onClick={onEnterVoiceMode}>
                  <AudioLines aria-hidden />
                </RoundButton>
              ) : primary === "queue" ? (
                <RoundButton label="Queue message" title="Sends when Edward finishes" face={FACE_SEND} onClick={handleSubmit}>
                  <ArrowUp aria-hidden strokeWidth={2.5} />
                </RoundButton>
              ) : primary === "busy" ? (
                <RoundButton label="Edward is finishing" face={FACE_SEND_OFF} disabled>
                  <span className="h-2 w-2 rounded-full bg-terminal/70" aria-hidden />
                </RoundButton>
              ) : primary === "none" ? null : (
                <RoundButton
                  label="Send message"
                  title="Send"
                  face={primary === "send" ? FACE_SEND : FACE_SEND_OFF}
                  onClick={handleSubmit}
                  disabled={primary !== "send"}
                >
                  <ArrowUp aria-hidden strokeWidth={2.5} />
                </RoundButton>
              )}
            </div>
          </div>
        </div>
      </div>
    </LazyMotion>
  );
});
