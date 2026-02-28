"use client";

import { useEffect, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { ChatWindow } from "@/components/chat/ChatWindow";
import { InputBar } from "@/components/chat/InputBar";
import { useChat } from "@/lib/ChatContext";

export default function ChatPage() {
  const {
    messages,
    isLoading,
    canStop,
    sendMessage,
    stopGeneration,
    startNewChat,
    loadConversation,
    refreshConversations,
  } = useChat();
  const searchParams = useSearchParams();
  const router = useRouter();
  const lastHandledRef = useRef<string | null>(null);

  // Handle ?c= query parameter or start fresh
  useEffect(() => {
    const conversationId = searchParams.get("c");
    if (conversationId) {
      // Skip if we already handled this exact conversation ID
      if (lastHandledRef.current === conversationId) return;
      lastHandledRef.current = conversationId;
      loadConversation(conversationId).then(() => {
        refreshConversations();
        router.replace("/chat", { scroll: false });
      });
    } else if (lastHandledRef.current !== "none") {
      lastHandledRef.current = "none";
      startNewChat();
    }
  }, [searchParams, loadConversation, refreshConversations, startNewChat, router]);

  return (
    <div className="flex-1 flex flex-col overflow-hidden min-h-0">
      <ChatWindow messages={messages} isLoading={isLoading} />
      <InputBar
        onSend={sendMessage}
        onStop={stopGeneration}
        isLoading={isLoading}
        canStop={canStop}
      />
    </div>
  );
}
