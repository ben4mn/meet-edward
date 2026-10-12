"use client";

// The chat transcript (2026-09-26).
//
// A reading column (max-w-3xl, shared with the composer) of MessageBubbles.
// A bubble is live only when the turn streams into it (ChatContext
// streamingMessageId), and only that bubble re-renders while it streams.
//
// Scrolling (lib/scrollStick.ts): a ResizeObserver keeps the newest line in
// view while a turn streams, writing scrollTop directly (no smooth scroll per
// token); opening a thread or sending lands at the bottom at once; the
// owner scrolling up lets go and new text raises "New reply ↓" instead of
// yanking them down. Only their own scroll lets go: wheel, touch, a scrolling key or the
// scrollbar mark intent, and a scroll outside that window is layout (the
// composer re-measuring) — never a reason to drop the live turn. Tapping a
// disclosure in the transcript (anything with aria-expanded) lets go too, so
// it opens under their finger.
//
// Empty: a new chat is Edward and his greeting (ChatEmptyState); a thread
// still loading is a skeleton of a conversation, not a spinner.

import React, { useCallback, useLayoutEffect, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { ArrowDown } from "lucide-react";
import { MessageBubble } from "./MessageBubble";
import { TurnAnnouncer } from "./TurnAnnouncer";
import { ChatEmptyState } from "./ChatEmptyState";
import { Skeleton } from "@/components/ui/Feedback";
import { EmptyState } from "@/components/ui/primitives";
import { usePrefersReducedMotion } from "@/components/ui/motion";
import { useChat, type Message } from "@/lib/ChatContext";
import { isLiveMessage } from "@/lib/workRail";
import { retryQuestion } from "@/lib/answerText";
import {
  INITIAL_STICK,
  INTENT_MS,
  distanceFromBottom,
  isScrollKey,
  reduceStick,
  showNewReplyPill,
  type StickEvent,
  type StickState,
} from "@/lib/scrollStick";

// Layout effects only exist in the browser; on the server this is a plain effect (no warning).
const useIsoLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

// Voice view owns the mic, an AudioWorklet and a PCM player. Defer it
// until the user actually opens voice mode.
const ContinuousVoiceView = dynamic(
  () => import("./ContinuousVoiceView").then((m) => m.ContinuousVoiceView),
  { ssr: false },
);

interface ChatWindowProps {
  messages: Message[];
  isLoading: boolean;
}

function ConversationSkeleton() {
  return (
    <div className="min-h-0 flex-1 overflow-hidden bg-primary-bg" role="status" aria-label="Loading the conversation">
      <div className="mx-auto w-full max-w-3xl space-y-8 px-4 pt-6 md:px-6">
        <div className="flex justify-end">
          <div className="h-11 w-2/3 rounded-lg bg-surface-3 motion-safe:animate-pulse" />
        </div>
        <Skeleton lines={4} />
        <div className="flex justify-end">
          <div className="h-11 w-2/5 rounded-lg bg-surface-3 motion-safe:animate-pulse" />
        </div>
        <Skeleton lines={3} />
      </div>
    </div>
  );
}

export function ChatWindow({ messages, isLoading }: ChatWindowProps) {
  const {
    isVoiceMode,
    isLoadingConversation,
    conversationId,
    exitVoiceMode,
    setVoiceConversationId,
    streamingMessageId,
    turn,
    sendMessage,
    userSendSeq,
    voiceEnabled,
    setVoicePhase,
  } = useChat();

  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef<StickState>(INITIAL_STICK);
  const streamingRef = useRef(isLoading);
  streamingRef.current = isLoading;
  const [pill, setPill] = useState(false);
  const reduced = usePrefersReducedMotion();

  const apply = useCallback((event: StickEvent) => {
    const { state, pin } = reduceStick(stickRef.current, event);
    stickRef.current = state;
    const el = scrollRef.current;
    if (pin && el) el.scrollTop = el.scrollHeight;
    setPill(showNewReplyPill(state));
  }, []);

  // The owner's hands on the transcript: a pointer or finger down on it, or a
  // wheel / touch move / scrolling key within the last INTENT_MS.
  const intentRef = useRef({ until: 0, down: false });

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const intent = intentRef.current;
    const user = intent.down || Date.now() < intent.until;
    apply({ type: "scroll", top: el.scrollTop, distance: distanceFromBottom(el), user });
  }, [apply]);

  // Opening or closing a disclosure in the transcript (a receipt, a pack
  // card, a tool's details): the owner is reading here, so let go before it grows.
  const onClickCapture = useCallback(
    (e: React.MouseEvent) => {
      const t = e.target as Element | null;
      if (t && typeof t.closest === "function" && t.closest("[aria-expanded]")) apply({ type: "toggle" });
    },
    [apply],
  );

  // A different thread, or the first messages of one: straight to the bottom.
  const hasMessages = messages.length > 0;
  useIsoLayoutEffect(() => {
    if (hasMessages) apply({ type: "reset" });
  }, [conversationId, hasMessages, apply]);

  // The owner sent something themselves (the composer, Retry): follow it
  // down. A queued message that sends itself when the turn ends is not a
  // send they just made — they may be scrolled up reading the answer it
  // waited for, so it keeps their place (stuck follows, scrolled-up gets the pill).
  const prevSendSeq = useRef(userSendSeq);
  useIsoLayoutEffect(() => {
    if (prevSendSeq.current === userSendSeq) return;
    prevSendSeq.current = userSendSeq;
    apply({ type: "send" });
  }, [userSendSeq, apply]);

  // Intent: what makes a scroll the owner's (lib/scrollStick.ts INTENT_MS).
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const intent = intentRef.current;
    const mark = () => {
      intent.until = Date.now() + INTENT_MS;
    };
    const down = () => {
      intent.down = true;
      mark();
    };
    const up = () => {
      if (!intent.down) return;
      intent.down = false;
      mark();
    };
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = Boolean(t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)));
      if (isScrollKey(e.key, typing)) mark();
    };
    const passive = { passive: true } as const;
    el.addEventListener("wheel", mark, passive);
    el.addEventListener("touchstart", down, passive);
    el.addEventListener("touchmove", mark, passive);
    el.addEventListener("pointerdown", down, passive);
    el.addEventListener("focusin", mark);
    window.addEventListener("touchend", up, passive);
    window.addEventListener("touchcancel", up, passive);
    window.addEventListener("pointerup", up, passive);
    window.addEventListener("pointercancel", up, passive);
    window.addEventListener("keydown", key);
    return () => {
      el.removeEventListener("wheel", mark);
      el.removeEventListener("touchstart", down);
      el.removeEventListener("touchmove", mark);
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("focusin", mark);
      window.removeEventListener("touchend", up);
      window.removeEventListener("touchcancel", up);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      window.removeEventListener("keydown", key);
      intent.down = false;
    };
  }, [hasMessages, isVoiceMode]);

  // Content growing (tokens, a rail opening) or the viewport shrinking (the keyboard).
  useEffect(() => {
    const content = contentRef.current;
    const scroller = scrollRef.current;
    if (!content || !scroller || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => apply({ type: "grow", streaming: streamingRef.current }));
    ro.observe(content);
    ro.observe(scroller);
    return () => ro.disconnect();
  }, [apply, hasMessages, isVoiceMode]);

  const jump = useCallback(() => {
    const el = scrollRef.current;
    const { state } = reduceStick(stickRef.current, { type: "jump" });
    stickRef.current = state;
    setPill(false);
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: reduced ? "auto" : "smooth" });
  }, [reduced]);

  const onRetry = useCallback((text: string) => void sendMessage(text), [sendMessage]);

  const lastAssistant = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === "assistant") return i;
    return -1;
  }, [messages]);

  // The turn's one screen-reader voice, mounted whatever the view shows
  // (sr-only: absolutely positioned, so it never takes a flex slot).
  const announcer = <TurnAnnouncer turn={turn} messages={messages} />;

  if (isVoiceMode) {
    return (
      <>
        <ContinuousVoiceView
          conversationId={conversationId}
          onExitVoiceMode={exitVoiceMode}
          onConversationCreated={setVoiceConversationId}
          onPhase={setVoicePhase}
        />
        {announcer}
      </>
    );
  }

  if (!hasMessages) {
    let empty: React.ReactNode;
    if (isLoadingConversation) empty = <ConversationSkeleton />;
    else if (conversationId) {
      empty = (
        <div className="flex min-h-0 flex-1 items-center justify-center bg-primary-bg px-6">
          <EmptyState title="Nothing here yet">Say something and I&rsquo;ll pick it up from here.</EmptyState>
        </div>
      );
    } else empty = <ChatEmptyState />;
    return (
      <>
        {empty}
        {announcer}
      </>
    );
  }

  return (
    <>
      <div className="relative flex min-h-0 flex-1 flex-col bg-primary-bg">
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain [overflow-anchor:none]"
          data-testid="chat-scroll"
        >
          <div ref={contentRef} onClickCapture={onClickCapture} className="mx-auto w-full max-w-3xl space-y-6 px-4 pb-8 pt-5 md:px-6">
            {messages.map((m, i) => {
              const isLast = i === lastAssistant;
              return (
                <MessageBubble
                  key={m.id}
                  message={m}
                  live={isLiveMessage(m.id, streamingMessageId)}
                  turn={turn && turn.id === m.id ? turn : null}
                  retryText={isLast && !isLoading ? retryQuestion(messages, i) : undefined}
                  onRetry={onRetry}
                  canReadAloud={voiceEnabled}
                  isLast={isLast}
                />
              );
            })}
          </div>
        </div>
        {pill ? (
          <button
            type="button"
            onClick={jump}
            className="absolute bottom-3 left-1/2 z-10 inline-flex h-9 -translate-x-1/2 items-center gap-1.5 rounded-full border border-border bg-surface-elevated px-3.5 text-footnote font-medium text-text-primary shadow-e2 transition-transform duration-press active:scale-[.97] motion-safe:animate-fade-rise focus:outline-none focus-visible:ring-2 focus-visible:ring-terminal/60"
          >
            New reply
            <ArrowDown className="h-3.5 w-3.5" aria-hidden />
          </button>
        ) : null}
      </div>
      {announcer}
    </>
  );
}
