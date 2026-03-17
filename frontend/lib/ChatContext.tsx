"use client";

import {
  createContext,
  useContext,
  useState,
  useRef,
  useCallback,
  useEffect,
  ReactNode,
} from "react";
import {
  streamChatEvents,
  getConversation,
  getConversations,
  getLatestConversationTimestamp,
  deleteConversation as deleteConversationApi,
  Conversation,
  StreamEvent,
} from "@/lib/api";
import { useAuth } from "@/lib/AuthContext";

// Code execution block within a message
export interface CodeBlock {
  id: string;
  code: string;
  language: string;
  output?: string;
  success?: boolean;
  duration_ms?: number;
  isExecuting: boolean;
}

// Plan step within a plan block
export interface PlanStep {
  id: string;
  title: string;
  status: "pending" | "in_progress" | "completed" | "error";
  result?: string | null;
}

// Plan block within a message
export interface PlanBlock {
  id: string;
  steps: PlanStep[];
  isComplete: boolean;
  summary?: string | null;
}

// CC session event within an inline CC session block
export interface CCSessionEvent {
  id: string;
  type: "text" | "tool_use" | "tool_result";
  text?: string;
  toolName?: string;
  toolInput?: string;
  timestamp: number;
}

// CC session block within a message
export interface CCSession {
  id: string;           // task_id
  description: string;
  status: "running" | "completed" | "failed";
  events: CCSessionEvent[];
  resultSummary?: string;
}

// Progress step for showing discrete progress during operations
export interface ProgressStep {
  id: string;
  step: string;
  status: "started" | "completed" | "error";
  message: string;
  timestamp: number;
  count?: number;
  tool_name?: string;
}

export interface MessageAttachment {
  id: string;
  file_id?: string;
  filename: string;
  mime_type: string;
  size: number;
  preview_url?: string; // Object URL for local image preview
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  // Optional fields for enhanced messages
  codeBlocks?: CodeBlock[];
  planBlock?: PlanBlock;
  progressSteps?: ProgressStep[];
  isThinking?: boolean;
  thinkingContent?: string;
  wasInterrupted?: boolean;
  attachments?: MessageAttachment[];
  ccSessions?: CCSession[];
  isTrigger?: boolean;
  triggerType?: string;
}

interface ChatContextType {
  messages: Message[];
  conversationId: string | undefined;
  isLoading: boolean;
  isLoadingConversation: boolean;
  canStop: boolean;
  conversations: Conversation[];
  sidebarCollapsed: boolean;
  sourceFilter: string;
  // Search & pagination
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  searchResults: Conversation[] | null;
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMoreConversations: () => Promise<void>;
  clearSearch: () => void;
  // Core actions
  sendMessage: (content: string, files?: File[]) => Promise<void>;
  stopGeneration: () => void;
  startNewChat: () => void;
  loadConversation: (id: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  refreshConversations: () => Promise<void>;
  toggleSidebar: () => void;
  setSourceFilter: (filter: string) => void;
}

const ChatContext = createContext<ChatContextType | undefined>(undefined);

export function ChatProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingConversation, setIsLoadingConversation] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    if (typeof window !== "undefined") {
      const stored = localStorage.getItem("edward-sidebar-collapsed");
      return stored === "true";
    }
    return false;
  });
  const [sourceFilter, setSourceFilter] = useState(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("edward-source-filter") || "all";
    }
    return "all";
  });

  // Search & pagination state
  const [searchQuery, setSearchQueryRaw] = useState("");
  const [searchResults, setSearchResults] = useState<Conversation[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [conversationOffset, setConversationOffset] = useState(0);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const streamingContentRef = useRef("");
  const abortControllerRef = useRef<AbortController | null>(null);
  const lastKnownTimestampRef = useRef<string | null>(null);
  const [canStop, setCanStop] = useState(false);
  const { isAuthenticated, isLoading: authLoading } = useAuth();

  // Load conversations on mount (only when authenticated)
  useEffect(() => {
    if (isAuthenticated && !authLoading) {
      refreshConversations();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, authLoading]);

  // Persist sidebar state
  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem("edward-sidebar-collapsed", String(sidebarCollapsed));
    }
  }, [sidebarCollapsed]);

  // Persist sourceFilter state
  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem("edward-source-filter", sourceFilter);
    }
  }, [sourceFilter]);

  const refreshConversations = useCallback(async () => {
    try {
      const data = await getConversations(20, 0, true);
      // Deduplicate by id as a safety net against race conditions / double-mount
      const seen = new Set<string>();
      const unique = data.conversations.filter((c) => {
        if (seen.has(c.id)) return false;
        seen.add(c.id);
        return true;
      });
      setConversations(unique);
      setHasMore(data.has_more ?? false);
      setConversationOffset(unique.length);
      // Track latest timestamp for polling
      if (unique.length > 0) {
        lastKnownTimestampRef.current = unique[0].updated_at;
      }
    } catch (error) {
      console.error("Error fetching conversations:", error);
    }
  }, []);

  // Auto-refresh sidebar when external events create conversations
  useEffect(() => {
    if (!isAuthenticated || authLoading) return;

    const poll = async () => {
      // Skip while streaming or tab is hidden
      if (abortControllerRef.current || document.visibilityState === "hidden") return;
      const latest = await getLatestConversationTimestamp();
      if (!latest) return;
      if (!lastKnownTimestampRef.current) {
        // First poll — just record baseline
        lastKnownTimestampRef.current = latest;
        return;
      }
      if (latest !== lastKnownTimestampRef.current) {
        await refreshConversations();
      }
    };

    const interval = setInterval(poll, 20000);

    // Poll immediately when tab becomes visible
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        poll();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, authLoading, refreshConversations]);

  // iOS PWA: recover stuck "Thinking" state when app returns to foreground after
  // the network connection was dropped while backgrounded (ngrok drops long SSE streams).
  useEffect(() => {
    if (!isAuthenticated) return;

    const onVisibility = async () => {
      if (document.visibilityState !== "visible") return;
      if (!isLoading) return; // not streaming, nothing to recover

      // Give the stream 2s to recover on its own first
      await new Promise(r => setTimeout(r, 2000));
      if (!isLoading) return; // recovered naturally

      // Stream is still stuck — abort it, recover last reply from DB
      const cid = conversationId;
      abortControllerRef.current?.abort();
      abortControllerRef.current = null;
      setIsLoading(false);
      setCanStop(false);
      setMessages(prev => {
        const msgs = [...prev];
        const last = msgs[msgs.length - 1];
        if (last?.role === "assistant") {
          msgs[msgs.length - 1] = { ...last, isThinking: false };
        }
        return msgs;
      });

      if (cid) {
        try {
          const conv = await getConversation(cid);
          const lastAssistant = [...(conv.messages || [])].reverse().find(m => m.role === "assistant");
          if (lastAssistant?.content) {
            setMessages(prev => {
              const msgs = [...prev];
              const last = msgs[msgs.length - 1];
              if (last?.role === "assistant") {
                msgs[msgs.length - 1] = { ...last, content: lastAssistant.content, isThinking: false };
              }
              return msgs;
            });
          }
        } catch {
          // recovery failed silently
        }
      }
      await refreshConversations();
    };

    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [isAuthenticated, isLoading, conversationId, refreshConversations]);

  // Debounced search
  const setSearchQuery = useCallback((query: string) => {
    setSearchQueryRaw(query);
    if (searchTimerRef.current) {
      clearTimeout(searchTimerRef.current);
    }
    if (!query.trim()) {
      setSearchResults(null);
      return;
    }
    searchTimerRef.current = setTimeout(async () => {
      try {
        const data = await getConversations(20, 0, true, query.trim());
        setSearchResults(data.conversations);
      } catch (error) {
        console.error("Search failed:", error);
      }
    }, 300);
  }, []);

  const clearSearch = useCallback(() => {
    setSearchQueryRaw("");
    setSearchResults(null);
    if (searchTimerRef.current) {
      clearTimeout(searchTimerRef.current);
    }
  }, []);

  const loadMoreConversations = useCallback(async () => {
    if (isLoadingMore || !hasMore) return;
    setIsLoadingMore(true);
    try {
      const data = await getConversations(50, conversationOffset, true);
      const seen = new Set(conversations.map((c) => c.id));
      const newConvs = data.conversations.filter((c) => !seen.has(c.id));
      setConversations((prev) => [...prev, ...newConvs]);
      setHasMore(data.has_more ?? false);
      setConversationOffset((prev) => prev + newConvs.length);
    } catch (error) {
      console.error("Error loading more conversations:", error);
    } finally {
      setIsLoadingMore(false);
    }
  }, [isLoadingMore, hasMore, conversationOffset, conversations]);

  const startNewChat = useCallback(() => {
    setMessages([]);
    setConversationId(undefined);
  }, []);

  const loadConversation = useCallback(async (id: string) => {
    // Optimistic: highlight sidebar immediately and clear stale content
    setConversationId(id);
    setMessages([]);
    setIsLoadingConversation(true);
    try {
      const data = await getConversation(id);
      const mappedMessages = data.messages.map((msg, index) => {
        const message: Message = {
          id: `${id}-${index}`,
          role: msg.role as "user" | "assistant",
          content: msg.content,
        };
        // Map attachment metadata from API response
        if (msg.attachments && msg.attachments.length > 0) {
          message.attachments = msg.attachments.map((att, i) => ({
            id: `${id}-${index}-att-${i}`,
            file_id: att.file_id,
            filename: att.filename,
            mime_type: att.mime_type,
            size: att.size || 0,
          }));
        }
        // Map trigger fields
        if (msg.is_trigger) {
          message.isTrigger = true;
          message.triggerType = msg.trigger_type;
        }
        return message;
      });

      // Attach CC session summaries to the last assistant message
      if (data.cc_sessions && data.cc_sessions.length > 0) {
        for (let i = mappedMessages.length - 1; i >= 0; i--) {
          if (mappedMessages[i].role === "assistant") {
            mappedMessages[i] = {
              ...mappedMessages[i],
              ccSessions: data.cc_sessions.map((s) => ({
                id: s.task_id,
                description: s.description,
                status: s.status === "completed" ? "completed" : "failed",
                events: [],
                resultSummary: s.result_summary || undefined,
              })),
            };
            break;
          }
        }
      }

      setMessages(mappedMessages);
    } catch (error) {
      console.error("Error loading conversation:", error);
      setMessages([{
        id: "error",
        role: "assistant",
        content: "Failed to load this conversation. It may not have any messages yet.",
      }]);
    } finally {
      setIsLoadingConversation(false);
    }
  }, []);

  // Listen for service worker messages (e.g. push notification clicks on iOS PWA)
  useEffect(() => {
    const handleNotificationNav = (url: string) => {
      try {
        const parsed = new URL(url, window.location.origin);
        const convId = parsed.searchParams.get('c');
        if (convId) {
          loadConversation(convId).then(() => refreshConversations());
        }
      } catch { /* invalid URL */ }
    };

    // Primary: service worker postMessage
    const swHandler = (event: MessageEvent) => {
      if (event.data?.type === 'NOTIFICATION_CLICK' && event.data?.url) {
        handleNotificationNav(event.data.url);
      }
    };
    navigator.serviceWorker?.addEventListener('message', swHandler);

    // Fallback: BroadcastChannel (survives iOS PWA focus() race)
    let bc: BroadcastChannel | null = null;
    try {
      bc = new BroadcastChannel('edward-notification');
      bc.onmessage = (event: MessageEvent) => {
        if (event.data?.type === 'NOTIFICATION_CLICK' && event.data?.url) {
          handleNotificationNav(event.data.url);
        }
      };
    } catch { /* BroadcastChannel not available */ }

    return () => {
      navigator.serviceWorker?.removeEventListener('message', swHandler);
      bc?.close();
    };
  }, [loadConversation, refreshConversations]);

  const deleteConversation = useCallback(
    async (id: string) => {
      try {
        await deleteConversationApi(id);
        setConversations((prev) => prev.filter((c) => c.id !== id));

        // If the deleted conversation is the current one, start fresh
        if (conversationId === id) {
          startNewChat();
        }
      } catch (error) {
        console.error("Error deleting conversation:", error);
      }
    },
    [conversationId, startNewChat]
  );

  const sendMessage = useCallback(
    async (content: string, files?: File[]) => {
      if ((!content.trim() && (!files || files.length === 0)) || isLoading) return;

      // Build attachment metadata for the user message
      const messageAttachments: MessageAttachment[] = (files || []).map((file, i) => {
        const att: MessageAttachment = {
          id: `att-${Date.now()}-${i}`,
          filename: file.name,
          mime_type: file.type,
          size: file.size,
        };
        // Create local preview URL for images
        if (file.type.startsWith("image/")) {
          att.preview_url = URL.createObjectURL(file);
        }
        return att;
      });

      const userMessage: Message = {
        id: Date.now().toString(),
        role: "user",
        content,
        attachments: messageAttachments.length > 0 ? messageAttachments : undefined,
      };

      setMessages((prev) => [...prev, userMessage]);
      setIsLoading(true);

      const assistantMessageId = (Date.now() + 1).toString();
      const assistantMessage: Message = {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        codeBlocks: [],
        progressSteps: [],
        isThinking: false,
        wasInterrupted: false,
      };

      setMessages((prev) => [...prev, assistantMessage]);

      // Create abort controller for this request
      abortControllerRef.current = new AbortController();
      setCanStop(true);

      streamingContentRef.current = "";
      let newConversationId: string | undefined;
      let currentCodeBlock: CodeBlock | null = null;
      let codeBlockCounter = 0;
      let doneSeen = false;
      let contentSeen = false;

      // Helper to update the assistant message
      const updateAssistantMessage = (updates: Partial<Message>) => {
        setMessages((prev) => {
          const newMessages = [...prev];
          const lastMessage = newMessages[newMessages.length - 1];
          if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId) {
            newMessages[newMessages.length - 1] = {
              ...lastMessage,
              ...updates,
            };
          }
          return newMessages;
        });
      };

      const EXECUTION_TOOLS = new Set([
        "execute_code", "execute_javascript", "execute_sql", "execute_shell",
      ]);

      try {

        for await (const event of streamChatEvents(content, conversationId, abortControllerRef.current?.signal, files)) {
          // Track conversation_id from first event
          if (event.conversation_id && !newConversationId) {
            newConversationId = event.conversation_id;
            setConversationId(newConversationId);
          }

          switch (event.type) {
            case "thinking":
              updateAssistantMessage({
                isThinking: true,
                thinkingContent: event.content,
              });
              break;

            case "progress":
              // Add or update progress step
              if (event.step && event.status && event.message) {
                // Capture values to satisfy TypeScript narrowing
                const stepName = event.step;
                const stepStatus = event.status;
                const stepMessage = event.message;
                const stepCount = event.count;
                const stepToolName = event.tool_name;

                setMessages((prev) => {
                  const newMessages = [...prev];
                  const lastMessage = newMessages[newMessages.length - 1];
                  if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId) {
                    const existingSteps = [...(lastMessage.progressSteps || [])];
                    const stepId = `${stepName}-${stepToolName || "main"}`;

                    // Find existing step with same id
                    const existingIndex = existingSteps.findIndex(s => s.id === stepId);

                    const newStep: ProgressStep = {
                      id: stepId,
                      step: stepName,
                      status: stepStatus,
                      message: stepMessage,
                      timestamp: Date.now(),
                      count: stepCount,
                      tool_name: stepToolName,
                    };

                    if (existingIndex >= 0) {
                      // Update existing step
                      existingSteps[existingIndex] = newStep;
                    } else {
                      // Add new step
                      existingSteps.push(newStep);
                    }

                    newMessages[newMessages.length - 1] = {
                      ...lastMessage,
                      isThinking: stepStatus !== "completed",
                      progressSteps: existingSteps,
                    };
                  }
                  return newMessages;
                });
              }
              break;

            case "tool_start":
              if (event.tool_name && EXECUTION_TOOLS.has(event.tool_name)) {
                // Create a new code block and immediately add to message state
                codeBlockCounter++;
                currentCodeBlock = {
                  id: `code-${assistantMessageId}-${codeBlockCounter}`,
                  code: "",
                  language: "python",
                  isExecuting: true,
                };
                const newBlock = { ...currentCodeBlock };
                setMessages((prev) => {
                  const newMessages = [...prev];
                  const lastMessage = newMessages[newMessages.length - 1];
                  if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId) {
                    const existingBlocks = [...(lastMessage.codeBlocks || [])];
                    existingBlocks.push(newBlock);
                    newMessages[newMessages.length - 1] = {
                      ...lastMessage,
                      codeBlocks: existingBlocks,
                    };
                  }
                  return newMessages;
                });
              }
              break;

            case "code":
              if (currentCodeBlock && event.code) {
                currentCodeBlock.code = event.code;
                currentCodeBlock.language = event.language || "python";
                const updatedBlock = { ...currentCodeBlock };
                // Upsert the code block in message state
                setMessages((prev) => {
                  const newMessages = [...prev];
                  const lastMessage = newMessages[newMessages.length - 1];
                  if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId) {
                    const existingBlocks = [...(lastMessage.codeBlocks || [])];
                    const existingIndex = existingBlocks.findIndex(b => b.id === updatedBlock.id);
                    if (existingIndex >= 0) {
                      existingBlocks[existingIndex] = updatedBlock;
                    } else {
                      existingBlocks.push(updatedBlock);
                    }
                    newMessages[newMessages.length - 1] = {
                      ...lastMessage,
                      codeBlocks: existingBlocks,
                    };
                  }
                  return newMessages;
                });
              }
              break;

            case "execution_output":
              if (currentCodeBlock && event.output) {
                currentCodeBlock.output = (currentCodeBlock.output || "") + event.output;
                const updatedBlock = { ...currentCodeBlock };
                // Upsert the code block with output
                setMessages((prev) => {
                  const newMessages = [...prev];
                  const lastMessage = newMessages[newMessages.length - 1];
                  if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId) {
                    const existingBlocks = [...(lastMessage.codeBlocks || [])];
                    const blockIndex = existingBlocks.findIndex(b => b.id === updatedBlock.id);
                    if (blockIndex >= 0) {
                      existingBlocks[blockIndex] = updatedBlock;
                    } else {
                      existingBlocks.push(updatedBlock);
                    }
                    newMessages[newMessages.length - 1] = {
                      ...lastMessage,
                      codeBlocks: existingBlocks,
                    };
                  }
                  return newMessages;
                });
              }
              break;

            case "execution_result":
              if (currentCodeBlock) {
                currentCodeBlock.success = event.success;
                currentCodeBlock.duration_ms = event.duration_ms;
                currentCodeBlock.isExecuting = false;
                const updatedBlock = { ...currentCodeBlock };
                // Upsert the code block with result
                setMessages((prev) => {
                  const newMessages = [...prev];
                  const lastMessage = newMessages[newMessages.length - 1];
                  if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId) {
                    const existingBlocks = [...(lastMessage.codeBlocks || [])];
                    const blockIndex = existingBlocks.findIndex(b => b.id === updatedBlock.id);
                    if (blockIndex >= 0) {
                      existingBlocks[blockIndex] = updatedBlock;
                    } else {
                      existingBlocks.push(updatedBlock);
                    }
                    newMessages[newMessages.length - 1] = {
                      ...lastMessage,
                      codeBlocks: existingBlocks,
                    };
                  }
                  return newMessages;
                });
              }
              break;

            case "tool_end":
              // Clear thinking state and reset current code block
              updateAssistantMessage({ isThinking: false });
              currentCodeBlock = null;
              break;

            case "plan_created":
              if (event.plan_steps) {
                const planBlock: PlanBlock = {
                  id: `plan-${assistantMessageId}`,
                  steps: event.plan_steps.map(s => ({
                    id: s.id,
                    title: s.title,
                    status: s.status as PlanStep["status"],
                    result: s.result,
                  })),
                  isComplete: false,
                };
                updateAssistantMessage({ planBlock });
              }
              break;

            case "plan_step_update":
              if (event.step_id) {
                setMessages((prev) => {
                  const newMessages = [...prev];
                  const lastMessage = newMessages[newMessages.length - 1];
                  if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId && lastMessage.planBlock) {
                    const updatedSteps = lastMessage.planBlock.steps.map(step =>
                      step.id === event.step_id
                        ? { ...step, status: (event.step_status || step.status) as PlanStep["status"], result: event.step_result ?? step.result }
                        : step
                    );
                    newMessages[newMessages.length - 1] = {
                      ...lastMessage,
                      planBlock: { ...lastMessage.planBlock, steps: updatedSteps },
                    };
                  }
                  return newMessages;
                });
              }
              break;

            case "plan_updated":
              if (event.plan_steps) {
                setMessages((prev) => {
                  const newMessages = [...prev];
                  const lastMessage = newMessages[newMessages.length - 1];
                  if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId && lastMessage.planBlock) {
                    newMessages[newMessages.length - 1] = {
                      ...lastMessage,
                      planBlock: {
                        ...lastMessage.planBlock,
                        steps: event.plan_steps!.map(s => ({
                          id: s.id,
                          title: s.title,
                          status: s.status as PlanStep["status"],
                          result: s.result,
                        })),
                      },
                    };
                  }
                  return newMessages;
                });
              }
              break;

            case "plan_completed":
              setMessages((prev) => {
                const newMessages = [...prev];
                const lastMessage = newMessages[newMessages.length - 1];
                if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId && lastMessage.planBlock) {
                  const finalSteps = event.plan_steps
                    ? event.plan_steps.map(s => ({
                        id: s.id,
                        title: s.title,
                        status: s.status as PlanStep["status"],
                        result: s.result,
                      }))
                    : lastMessage.planBlock.steps.map(s => ({ ...s, status: "completed" as const }));
                  newMessages[newMessages.length - 1] = {
                    ...lastMessage,
                    planBlock: {
                      ...lastMessage.planBlock,
                      steps: finalSteps,
                      isComplete: true,
                      summary: event.plan_summary,
                    },
                  };
                }
                return newMessages;
              });
              break;

            case "cc_session_start":
              if (event.task_id) {
                const newSession: CCSession = {
                  id: event.task_id,
                  description: event.task_description || "",
                  status: "running",
                  events: [],
                };
                setMessages((prev) => {
                  const newMessages = [...prev];
                  const lastMessage = newMessages[newMessages.length - 1];
                  if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId) {
                    const sessions = [...(lastMessage.ccSessions || []), newSession];
                    newMessages[newMessages.length - 1] = { ...lastMessage, ccSessions: sessions };
                  }
                  return newMessages;
                });
              }
              break;

            case "cc_text":
            case "cc_tool_use":
            case "cc_tool_result":
              if (event.task_id) {
                const ccEvent: CCSessionEvent = {
                  id: `cc-evt-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
                  type: event.type === "cc_text" ? "text" : event.type === "cc_tool_use" ? "tool_use" : "tool_result",
                  text: event.text,
                  toolName: event.cc_tool_name,
                  toolInput: event.tool_input,
                  timestamp: Date.now(),
                };
                const targetTaskId = event.task_id;
                setMessages((prev) => {
                  const newMessages = [...prev];
                  const lastMessage = newMessages[newMessages.length - 1];
                  if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId && lastMessage.ccSessions) {
                    const sessions = lastMessage.ccSessions.map((s) =>
                      s.id === targetTaskId ? { ...s, events: [...s.events, ccEvent] } : s
                    );
                    newMessages[newMessages.length - 1] = { ...lastMessage, ccSessions: sessions };
                  }
                  return newMessages;
                });
              }
              break;

            case "cc_session_end":
              if (event.task_id) {
                const endTaskId = event.task_id;
                const endStatus = event.status === "completed" ? "completed" : "failed";
                const endSummary = event.result_summary;
                setMessages((prev) => {
                  const newMessages = [...prev];
                  const lastMessage = newMessages[newMessages.length - 1];
                  if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId && lastMessage.ccSessions) {
                    const sessions = lastMessage.ccSessions.map((s) =>
                      s.id === endTaskId ? { ...s, status: endStatus as CCSession["status"], resultSummary: endSummary } : s
                    );
                    newMessages[newMessages.length - 1] = { ...lastMessage, ccSessions: sessions };
                  }
                  return newMessages;
                });
              }
              break;

            case "content":
              if (event.content) {
                contentSeen = true;
                streamingContentRef.current += event.content;
                updateAssistantMessage({
                  content: streamingContentRef.current,
                  isThinking: false,
                });
              }
              break;

            case "error":
              // LLM call failed — clear thinking state, content event will follow
              updateAssistantMessage({ isThinking: false });
              break;

            case "done":
              doneSeen = true;
              updateAssistantMessage({ isThinking: false });
              break;
          }
        }

        if (!doneSeen) {
          updateAssistantMessage({
            isThinking: false,
            ...(contentSeen ? {} : { content: "Sorry, the response stream ended unexpectedly. Please try again." }),
          });
          // Stream dropped mid-response (e.g. ngrok timeout) but backend likely saved the full reply.
          // Try to recover the completed response from the DB.
          if (contentSeen && (newConversationId || conversationId)) {
            try {
              const conv = await getConversation((newConversationId || conversationId)!);
              const lastAssistant = [...(conv.messages || [])].reverse().find(m => m.role === "assistant");
              if (lastAssistant?.content) {
                updateAssistantMessage({ content: lastAssistant.content, isThinking: false });
              }
            } catch {
              // Recovery failed silently — partial content already shown
            }
          }
        }

        // Refresh conversations list after sending (to get the new/updated conversation)
        await refreshConversations();
      } catch (error) {
        // Check if this was an abort (user clicked stop)
        if (error instanceof Error && error.name === "AbortError") {
          // Mark the message as interrupted but keep the partial content
          setMessages((prev) => {
            const newMessages = [...prev];
            const lastMessage = newMessages[newMessages.length - 1];
            if (lastMessage.role === "assistant" && lastMessage.id === assistantMessageId) {
              newMessages[newMessages.length - 1] = {
                ...lastMessage,
                wasInterrupted: true,
                isThinking: false,
                // Clear any in-progress code blocks
                codeBlocks: lastMessage.codeBlocks?.map(block => ({
                  ...block,
                  isExecuting: false,
                })),
              };
            }
            return newMessages;
          });
          // Still refresh conversations as the partial response might be saved
          await refreshConversations();
        } else {
          console.error("Error sending message:", error);
          // Network error mid-stream (e.g. ngrok dropped the connection).
          // If we were already streaming content, try to recover the full reply from DB.
          if (contentSeen && (newConversationId || conversationId)) {
            updateAssistantMessage({ isThinking: false });
            try {
              const conv = await getConversation((newConversationId || conversationId)!);
              const lastAssistant = [...(conv.messages || [])].reverse().find(m => m.role === "assistant");
              if (lastAssistant?.content) {
                updateAssistantMessage({ content: lastAssistant.content, isThinking: false });
              }
            } catch {
              updateAssistantMessage({ content: "Sorry, I encountered an error. Please try again.", isThinking: false });
            }
          } else {
            setMessages((prev) => {
              const newMessages = [...prev];
              const lastMessage = newMessages[newMessages.length - 1];
              if (lastMessage.role === "assistant") {
                lastMessage.content = "Sorry, I encountered an error. Please try again.";
              }
              return newMessages;
            });
          }
        }
      } finally {
        setIsLoading(false);
        setCanStop(false);
        abortControllerRef.current = null;
        // Guarantee thinking state is always cleared when stream ends
        updateAssistantMessage({ isThinking: false });
      }
    },
    [conversationId, isLoading, refreshConversations]
  );

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => !prev);
  }, []);

  const stopGeneration = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
      setCanStop(false);
    }
  }, []);

  return (
    <ChatContext.Provider
      value={{
        messages,
        conversationId,
        isLoading,
        isLoadingConversation,
        canStop,
        conversations,
        sidebarCollapsed,
        sourceFilter,
        // Search & pagination
        searchQuery,
        setSearchQuery,
        searchResults,
        hasMore,
        isLoadingMore,
        loadMoreConversations,
        clearSearch,
        // Core actions
        sendMessage,
        stopGeneration,
        startNewChat,
        loadConversation,
        deleteConversation,
        refreshConversations,
        toggleSidebar,
        setSourceFilter,
      }}
    >
      {children}
    </ChatContext.Provider>
  );
}

export function useChat() {
  const context = useContext(ChatContext);
  if (context === undefined) {
    throw new Error("useChat must be used within a ChatProvider");
  }
  return context;
}
