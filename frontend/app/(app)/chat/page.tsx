"use client";

import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Paperclip } from "lucide-react";

import { decideChatRoute, readVoiceParam, withoutVoiceParam } from "@/lib/chatRoute";
import { readPromptParam, withoutPrompt } from "@/lib/chatPrompt";
import { ChatWindow } from "@/components/chat/ChatWindow";
import { InputBar, type ComposerPrefill, type InputBarHandle } from "@/components/chat/InputBar";
import { ConversationHeader } from "@/components/chat/ConversationHeader";
import { useChat } from "@/lib/ChatContext";
import { useWakeLock } from "@/lib/useWakeLock";

function carriesFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

export default function ChatPage() {
  const {
    messages,
    conversations,
    conversationId,
    isVoiceMode,
    isLoading,
    canStop,
    sendMessage,
    stopGeneration,
    startNewChat,
    loadConversation,
    refreshConversations,
    voiceEnabled,
    enterVoiceMode,
    startVoiceMode,
  } = useChat();
  // The phone stays awake while Edward is working on this chat (tools,
  // long answers); released the moment the turn ends.
  useWakeLock(isLoading);
  const searchParams = useSearchParams();
  const router = useRouter();
  const currentIdRef = useRef<string | undefined>(conversationId);
  currentIdRef.current = conversationId;
  const composerRef = useRef<InputBarHandle>(null);

  // Reconcile the URL with what is on screen on every navigation, including
  // back/forward. Reads conversationId through a ref so a conversation that
  // gains an id mid-stream (and replaces the URL to match) does not reload.
  useEffect(() => {
    const cid = searchParams.get("c");
    const action = decideChatRoute({ cid, currentId: currentIdRef.current });
    if (action === "load" && cid) {
      loadConversation(cid).then(() => {
        refreshConversations();
      });
    } else if (action === "new") {
      startNewChat();
    }
  }, [searchParams, loadConversation, refreshConversations, startNewChat]);

  // ?prompt= (an idea's "Try it" on Today): put it in the
  // composer once for the owner to send, then drop it from the URL so a reload
  // or back does not type it again. Each arrival carries a new nonce, so
  // the same suggestion twice refills a box the owner cleared (2026-09-27: as a
  // plain string the second one changed nothing).
  const [prefill, setPrefill] = useState<ComposerPrefill | undefined>();
  useEffect(() => {
    const prompt = readPromptParam(searchParams);
    if (!prompt) return;
    setPrefill({ text: prompt, nonce: Date.now() });
    router.replace(`/chat${withoutPrompt(searchParams.toString())}`, { scroll: false });
  }, [searchParams, router]);

  // ?voice=1 (the "Talk live" home-screen shortcut): open live voice once
  // voice is known to be on, and drop the param straight away so back or a
  // reload never reopens the mic.
  const wantsVoiceRef = useRef(false);
  useEffect(() => {
    if (!readVoiceParam(searchParams)) return;
    wantsVoiceRef.current = true;
    router.replace(`/chat${withoutVoiceParam(searchParams.toString())}`, { scroll: false });
  }, [searchParams, router]);
  useEffect(() => {
    if (!wantsVoiceRef.current || !voiceEnabled || isVoiceMode) return;
    wantsVoiceRef.current = false;
    startVoiceMode();
  });

  // The whole column is a drop target.
  const [dropping, setDropping] = useState(false);
  const depthRef = useRef(0);
  const onDragEnter = useCallback((e: DragEvent) => {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    depthRef.current += 1;
    setDropping(true);
  }, []);
  const onDragOver = useCallback((e: DragEvent) => {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }, []);
  const onDragLeave = useCallback((e: DragEvent) => {
    if (!carriesFiles(e)) return;
    depthRef.current = Math.max(0, depthRef.current - 1);
    if (depthRef.current === 0) setDropping(false);
  }, []);
  const onDrop = useCallback((e: DragEvent) => {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    depthRef.current = 0;
    setDropping(false);
    if (e.dataTransfer.files?.length) composerRef.current?.addFiles(e.dataTransfer.files);
    composerRef.current?.focus();
  }, []);

  return (
    <div
      className="relative flex min-h-0 flex-1 flex-col overflow-hidden"
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {!isVoiceMode && (
        <ConversationHeader conversation={conversations.find((c) => c.id === conversationId) ?? null} />
      )}
      <ChatWindow messages={messages} isLoading={isLoading} />
      <InputBar
        ref={composerRef}
        onSend={sendMessage}
        onStop={stopGeneration}
        onEnterVoiceMode={messages.length === 0 ? startVoiceMode : enterVoiceMode}
        isLoading={isLoading}
        canStop={canStop}
        voiceEnabled={voiceEnabled}
        prefill={prefill}
      />
      {dropping ? (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-2 z-40 flex animate-fade-in items-center justify-center rounded-xl border-2 border-dashed border-text-muted/40 bg-primary-bg/85 backdrop-blur-sm"
        >
          <div className="flex flex-col items-center gap-3 text-center">
            <span className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-surface-3 text-text-primary shadow-e1">
              <Paperclip className="h-5 w-5" />
            </span>
            <p className="text-title3 text-text-primary">Drop to share with Edward</p>
            <p className="text-footnote text-text-muted">Images, PDFs, text and audio, up to 10 MB each</p>
          </div>
        </div>
      ) : null}
    </div>
  );
}
