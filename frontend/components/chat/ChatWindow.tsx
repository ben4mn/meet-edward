"use client";

import { useRef, useEffect, useState, useCallback } from "react";
import { MessageBubble } from "./MessageBubble";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { Loader2 } from "lucide-react";
import { fetchGreeting } from "@/lib/api";
import { useChat } from "@/lib/ChatContext";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

interface ChatWindowProps {
  messages: Message[];
  isLoading: boolean;
}

const SCROLL_THRESHOLD = 150;

export function ChatWindow({ messages, isLoading }: ChatWindowProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef(true);
  const [greeting, setGreeting] = useState<string | null>(null);
  const [loadingGreeting, setLoadingGreeting] = useState(false);
  const greetingFetched = useRef(false);

  const {
    isLoadingConversation,
    conversationId,
  } = useChat();

  const handleScroll = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    isNearBottomRef.current = distanceFromBottom <= SCROLL_THRESHOLD;
  }, []);

  useEffect(() => {
    if (isNearBottomRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);

  // Fetch greeting when there are no messages
  useEffect(() => {
    if (messages.length === 0 && !greetingFetched.current && !loadingGreeting) {
      greetingFetched.current = true;
      setLoadingGreeting(true);
      fetchGreeting()
        .then(setGreeting)
        .finally(() => setLoadingGreeting(false));
    }
    // Reset when starting a new conversation
    if (messages.length > 0) {
      greetingFetched.current = false;
      setGreeting(null);
    }
  }, [messages.length, loadingGreeting]);

  if (isLoadingConversation && messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center p-4 bg-primary-bg min-h-0">
        <div className="text-center">
          <Loader2 className="w-6 h-6 text-terminal animate-spin mx-auto" />
          <p className="text-sm text-text-secondary mt-2 font-mono">Loading conversation...</p>
        </div>
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center p-4 bg-primary-bg min-h-0">
        <div className="text-center max-w-md">
          <EdwardAvatar size="lg" animated className="mx-auto mb-4" />
          {loadingGreeting ? (
            <div className="h-8 flex items-center justify-center">
              <div className="w-2 h-2 bg-terminal rounded-full animate-pulse" />
            </div>
          ) : (
            <h2 className="text-xl font-semibold mb-2 text-text-primary font-mono">
              {greeting || "Hey there! Good to see you."}
            </h2>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto p-4 space-y-4 bg-primary-bg min-h-0"
    >
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
      <div ref={messagesEndRef} />
    </div>
  );
}
