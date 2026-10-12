"use client";

// React in scope for the classic JSX transform the node test runner uses
// (__tests__/chatTurn.test.tsx renders this provider).
import React, {
  createContext,
  useContext,
  useState,
  useRef,
  useCallback,
  useEffect,
  useMemo,
  ReactNode,
} from "react";
import {
  streamChatEvents,
  getConversation,
  getConversations,
  getLatestConversationTimestamp,
  deleteConversation as deleteConversationApi,
  getVoiceStatus,
  isChatProcessing,
  Conversation,
  ackConversationOpened,
  ChatRefusedError,
} from "@/lib/api";
import {
  advanceCursor,
  applyStreamEvent,
  applyUserMessageEvent,
  finishStreamMessage,
  newCursor,
  replaceMessage,
  type StreamCursor,
} from "@/lib/streamReducer";
import {
  endTurn,
  reduceTurn,
  startTurn,
  turnOutcomeFor,
  type StreamOutcome,
  type Turn,
} from "@/lib/turnModel";
import { ConversationCache, mapHistoryMessages, referencedBlobUrls, withLiveTurn, type LiveTurn } from "@/lib/chatHistory";
import type { VoicePhase } from "@/lib/voice/voiceMachine";

export type { Turn, TurnPhase, TurnTools, TurnToolEvent } from "@/lib/turnModel";
import { normalizeSidebarFilter } from "@/components/chat/sidebarGroups";
import { useAuth } from "@/lib/AuthContext";
import { usePathname, useRouter } from "next/navigation";
import { shouldEndVoiceOnRoute } from "@/lib/voiceRoute";
import { resolveNotificationNav } from "@/lib/notificationNav";
import {
  NEW_CHAT_KEY,
  clearQueue,
  enqueue,
  mergeQueues,
  nextOnDone,
  queueKey,
  queuedFor,
  rekeyQueue,
  removeQueued,
  type ComposerQueues,
  type QueuedMessage,
} from "@/lib/composerQueue";
import { loadStash, saveStash } from "@/lib/tabStash";

const NO_KEYS: ReadonlySet<string> = new Set();

function withKey(set: ReadonlySet<string>, key: string): ReadonlySet<string> {
  return set.has(key) ? set : new Set(Array.from(set).concat(key));
}

function withoutKeys(set: ReadonlySet<string>, drop: (key: string) => boolean): ReadonlySet<string> {
  const kept = Array.from(set).filter((k) => !drop(k));
  return kept.length === set.size ? set : new Set(kept);
}

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

// Tool activity for non-execution tools (web_search, memory, messaging, etc.)
// "stopped": the stream ended (done, Stop, a cut or an error) while the tool
// was still running — it is neither a success nor a failure.
export interface ToolActivity {
  id: string;
  tool_name: string;
  status: "running" | "completed" | "error" | "stopped";
  args_summary?: string;
  result?: string;
  timestamp: number;
  /** Start to end, from the stream (live) or the history trace (reload). */
  duration_ms?: number;
}

// Progress step for showing discrete progress during operations
// ("stopped": the stream ended before the step finished).
export interface ProgressStep {
  id: string;
  step: string;
  status: "started" | "completed" | "error" | "stopped";
  message: string;
  timestamp: number;
  count?: number;
  tool_name?: string;
}

/**
 * One entry of a turn's trace, in arrival order (lib/streamReducer.ts builds
 * it live; lib/chatHistory.ts rebuilds it from the history `trace`).
 *
 * - `memory`    — the memory search (count = memories found).
 * - `reasoning` — one paragraph run of Codex reasoning summary text.
 * - `tool`      — refers by id to `message.toolActivities[]` (or, for a
 *                 Claude Code session, `message.ccSessions[]`).
 * - `code`      — refers by id to `message.codeBlocks[]`.
 * - `text`      — one segment of the answer as streamed (no separator).
 *
 * `message.content` stays the authority for the answer text (the segments
 * joined by paragraph breaks, plus any error marker); the trace is for a
 * transcript that interleaves thinking, tools and text.
 */
export type TraceItem =
  | { kind: "memory"; status: "started" | "completed" | "error" | "stopped"; count?: number }
  | { kind: "reasoning"; text: string }
  | { kind: "tool"; id: string }
  | { kind: "code"; id: string }
  | { kind: "text"; text: string };

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
  reasoningContent?: string;  // N4: streamed Codex reasoning summary
  // Partial tool-call JSON for an in-flight function call. Keyed by
  // call_id so multiple concurrent calls don't collide. Cleared when
  // the corresponding tool_start arrives.
  pendingToolArgs?: Record<string, { name: string; args: string }>;
  // Debug-only stream events (memory matches, future internals).
  // Populated only when settings.debug_streaming is on.
  memoryMatches?: Array<{ id?: string; type?: string; preview?: string; score?: number }>;
  wasInterrupted?: boolean;
  /** The stream failed mid-turn: the bubble says so in his voice, with Retry. */
  wasCutOff?: boolean;
  attachments?: MessageAttachment[];
  ccSessions?: CCSession[];
  toolActivities?: ToolActivity[];
  isTrigger?: boolean;
  triggerType?: string;
  /** The turn in arrival order (see TraceItem). Live turns and hydrated history only. */
  trace?: TraceItem[];
}

interface ChatContextType {
  messages: Message[];
  conversationId: string | undefined;
  /** A turn is streaming into the conversation on screen (the composer is busy here). */
  isLoading: boolean;
  /** Cold load: no cached copy of this thread yet; sends wait in the queue. */
  isLoadingConversation: boolean;
  /** A cached copy is on screen while the server copy is fetched. */
  isRefreshingConversation: boolean;
  canStop: boolean;
  conversations: Conversation[];
  /** The conversation loadConversation last fetched. The sidebar list is
   *  paged per group, so a thought entry opened from Today or the Mind
   *  panel is often not in it; its header reads `thought` from here. */
  activeConversation: Conversation | null;
  sidebarCollapsed: boolean;
  sourceFilter: string;
  // The turn (lib/turnModel.ts) — the one source of truth for "what is Edward doing".
  /** The assistant message the running turn streams into; null when nothing streams.
   *  A bubble is live iff `message.id === streamingMessageId` (lib/workRail.ts isLiveMessage). */
  streamingMessageId: string | null;
  /** The conversation the running turn belongs to (may not be the one on screen). */
  streamingConversationId: string | null;
  /** The latest turn — live, or how the last one ended (done / stopped / error). */
  turn: Turn | null;
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
  /** Rises on each send the owner made themselves (the composer, Retry) — never a
   *  queue drain. The transcript lands at the bottom when it changes. */
  userSendSeq: number;
  stopGeneration: () => void;
  // Queue while running: messages typed while Edward works, sent in order
  // when the turn on screen ends (lib/composerQueue.ts).
  queuedMessages: readonly QueuedMessage[];
  /** Some thread, on screen or not, has messages waiting (AppBuildReload). */
  hasQueuedMessages: boolean;
  queueMessage: (content: string, files?: File[]) => void;
  removeQueuedMessage: (id: string) => void;
  /** The owner stopped the turn with messages queued: they wait for resumeQueue. */
  queuePaused: boolean;
  resumeQueue: () => void;
  startNewChat: () => void;
  loadConversation: (id: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  refreshConversations: () => Promise<void>;
  toggleSidebar: () => void;
  setSourceFilter: (filter: string) => void;
  // Voice-related
  voiceEnabled: boolean;
  isVoiceMode: boolean;
  /** What live voice is doing, as the voice view reports it (null when it has not). */
  voicePhase: VoicePhase | null;
  /** For ContinuousVoiceView: report the voice machine's phase (presence reads it). */
  setVoicePhase: (phase: VoicePhase | null) => void;
  startVoiceMode: () => void;
  enterVoiceMode: () => void;
  exitVoiceMode: () => void;
  refreshVoiceStatus: () => Promise<void>;
  setVoiceConversationId: (id: string) => void;
}

const ChatContext = createContext<ChatContextType | undefined>(undefined);

/**
 * The conversation list's own slice (integration pass 2026-09-26): list
 * state and stable actions, memoised — no messages, no turn, no voice
 * phase. The sidebar, the drawer, the dock and the header read this, so a
 * streamed token never re-renders 30–50 conversation rows (review:
 * hidden-desktop-sidebar-rerenders-every-token).
 */
export type ChatListContextType = Pick<
  ChatContextType,
  | "conversationId"
  | "conversations"
  | "activeConversation"
  | "sidebarCollapsed"
  | "sourceFilter"
  | "searchQuery"
  | "setSearchQuery"
  | "searchResults"
  | "hasMore"
  | "isLoadingMore"
  | "loadMoreConversations"
  | "clearSearch"
  | "startNewChat"
  | "loadConversation"
  | "deleteConversation"
  | "refreshConversations"
  | "toggleSidebar"
  | "setSourceFilter"
>;

const ChatListContext = createContext<ChatListContextType | undefined>(undefined);

/** The stream of the turn in flight. One at a time; it may belong to a thread that is not on screen. */
interface ActiveStream {
  assistantId: string;
  /** Learned from the first event for a brand-new chat. */
  conversationId: string | undefined;
  /** The conversation it was started in (undefined: a new chat). */
  startedIn: string | undefined;
  /** The screen it draws on: it touches `messages` only while this is the screen's epoch. */
  epoch: number;
  controller: AbortController;
  user: Message;
  assistant: Message;
  /** The owner's messages in the thread when this turn was sent (lib/chatHistory.ts withLiveTurn). */
  baseUserCount: number;
  cursor: StreamCursor;
  /** The server sent an `error` event (the turn failed server-side). */
  serverError: boolean;
}

interface StreamView {
  assistantId: string;
  conversationId: string | null;
  epoch: number;
}

const LOAD_FAILED_ID = "error";

export function ChatProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const messagesRef = useRef<Message[]>(messages);
  messagesRef.current = messages;
  const [conversationId, setConversationIdState] = useState<string | undefined>();
  const conversationIdRef = useRef<string | undefined>(undefined);
  const [isLoadingConversation, setIsLoadingConversationState] = useState(false);
  const isLoadingConversationRef = useRef(false);
  const [isRefreshingConversation, setIsRefreshingConversation] = useState(false);
  const router = useRouter();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversationState] = useState<Conversation | null>(null);
  const activeConversationRef = useRef<Conversation | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sourceFilter, setSourceFilter] = useState("all");

  // The screen: which conversation the transcript shows. Every navigation
  // (load, new chat) is a new epoch; a stream draws only on its own epoch.
  const [screenEpoch, setScreenEpoch] = useState(0);
  const screenEpochRef = useRef(0);
  const loadSeqRef = useRef(0);
  const cacheRef = useRef(new ConversationCache(8));

  const setScreenConversationId = useCallback((id: string | undefined) => {
    conversationIdRef.current = id;
    setConversationIdState(id);
  }, []);
  const setLoadingConversation = useCallback((v: boolean) => {
    isLoadingConversationRef.current = v;
    setIsLoadingConversationState(v);
  }, []);
  const setActiveConversation = useCallback((c: Conversation | null) => {
    activeConversationRef.current = c;
    setActiveConversationState(c);
  }, []);
  const bumpScreen = useCallback(() => {
    const e = screenEpochRef.current + 1;
    screenEpochRef.current = e;
    setScreenEpoch(e);
    return e;
  }, []);

  // Hydrate from localStorage after mount to avoid SSR mismatch
  useEffect(() => {
    try {
      const storedCollapsed = window.localStorage.getItem("edward-sidebar-collapsed");
      if (storedCollapsed === "true") setSidebarCollapsed(true);
      const storedFilter = window.localStorage.getItem("edward-source-filter");
      if (storedFilter) setSourceFilter(storedFilter);
    } catch { /* localStorage unavailable */ }
  }, []);

  // Search & pagination state
  const [searchQuery, setSearchQueryRaw] = useState("");
  const [searchResults, setSearchResults] = useState<Conversation[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [conversationOffset, setConversationOffset] = useState(0);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The turn in flight (see ActiveStream) and what React shows of it.
  const streamRef = useRef<ActiveStream | null>(null);
  const [streamView, setStreamView] = useState<StreamView | null>(null);
  const [turn, setTurn] = useState<Turn | null>(null);
  const [canStopState, setCanStop] = useState(false);
  const lastKnownTimestampRef = useRef<string | null>(null);
  const streamInterruptedRef = useRef<{ conversationId: string } | null>(null);
  // A cut stream is being recovered: the queue holds until the reload has
  // landed or recovery bailed. State, not the ref above — the ref is
  // cleared before recovery's first await and changing it never re-ran the
  // drain (2026-09-26 review).
  const [recovering, setRecovering] = useState(false);
  // The last turn that finished in each conversation, so a cached thread's
  // refresh that was fetched before it ended cannot erase it.
  const turnsDoneRef = useRef(new Map<string, { seq: number; pair: LiveTurn }>());
  // The owner's own sends (see userSendSeq).
  const [userSendSeq, setUserSendSeq] = useState(0);
  // Previews of photos and memos the owner sent (object URLs). Released once no
  // message on screen, in the cache or in the running turn shows them — the
  // server's copy of a thread points at /api/files instead (2026-09-26
  // review: they were never revoked, and the PWA stays open for days).
  const sentUrlsRef = useRef(new Set<string>());
  const { isAuthenticated, isLoading: authLoading } = useAuth();

  // Queue while running (2026-09-24): per conversation, in memory, and
  // mirrored into the tab's stash so a page load — Next's own, after a
  // deploy — does not drop it (pre-deploy review, 2026-09-27).
  const [queues, setQueues] = useState<ComposerQueues>({});
  const queuesRef = useRef(queues);
  queuesRef.current = queues;
  const queueSeqRef = useRef(0);
  const lastDrainedRef = useRef<string | null>(null);
  // Queues that wait for the owner's word ("Send now"): the one they stopped, and
  // every queue that came back from before a page load. A set — one key
  // let a second Stop elsewhere unpause the first.
  const [pausedQueues, setPausedQueues] = useState<ReadonlySet<string>>(NO_KEYS);
  // Not state: the restore must not re-render the whole chat when there
  // was nothing to bring back (the usual load).
  const queuesRestoredRef = useRef(false);

  // What was queued before this page loaded comes back paused: its turn
  // was cut by the load, and it goes when the owner says so.
  useEffect(() => {
    let live = true;
    void loadStash<ComposerQueues>("queues").then((saved) => {
      if (!live) return;
      queuesRestoredRef.current = true;
      const kept = saved && typeof saved === "object" ? saved : {};
      const merged = mergeQueues(queuesRef.current, kept);
      const back = Object.keys(kept).filter((k) => queuedFor(merged, k).length > 0);
      if (back.length) {
        setQueues((q) => mergeQueues(q, kept));
        setPausedQueues((p) => new Set(Array.from(p).concat(back)));
      } else if (Object.keys(queuesRef.current).length > 0 || saved) {
        // Queued before the stash was read, or an empty entry to replace.
        void saveStash("queues", queuesRef.current);
      }
    });
    return () => {
      live = false;
    };
  }, []);

  // Written only once the restore above has read the stash back.
  useEffect(() => {
    if (queuesRestoredRef.current) void saveStash("queues", queues);
  }, [queues]);

  // Voice state (simplified — WebSocket connection is managed by ContinuousVoiceView)
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [isVoiceMode, setIsVoiceMode] = useState(false);
  const [voicePhase, setVoicePhase] = useState<VoicePhase | null>(null);

  // Load conversations on mount (only when authenticated)
  useEffect(() => {
    if (isAuthenticated && !authLoading) {
      refreshConversations();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, authLoading]);

  // Persist sidebar state (a blocked or full storage must not break the app)
  useEffect(() => {
    try {
      window.localStorage.setItem("edward-sidebar-collapsed", String(sidebarCollapsed));
    } catch { /* localStorage unavailable */ }
  }, [sidebarCollapsed]);

  // Persist sourceFilter state
  useEffect(() => {
    try {
      window.localStorage.setItem("edward-source-filter", sourceFilter);
    } catch { /* localStorage unavailable */ }
  }, [sourceFilter]);

  // Define callbacks before effects that use them
  const refreshVoiceStatus = useCallback(async () => {
    try {
      const status = await getVoiceStatus();
      setVoiceEnabled(status.enabled && status.status === "connected");
    } catch (error) {
      console.error("Failed to check voice status:", error);
      setVoiceEnabled(false);
    }
  }, []);

  // Voice status: once on open (deferred so the UI renders first) and again
  // whenever the app comes back to the foreground — the skill is toggled
  // rarely, and a 30 s poll ran in every hidden tab (2026-09-26 review).
  useEffect(() => {
    if (isAuthenticated && !authLoading) {
      const timeout = setTimeout(refreshVoiceStatus, 500);
      const onVisible = () => {
        if (document.visibilityState === "visible") void refreshVoiceStatus();
      };
      document.addEventListener("visibilitychange", onVisible);
      return () => {
        clearTimeout(timeout);
        document.removeEventListener("visibilitychange", onVisible);
      };
    }
  }, [isAuthenticated, authLoading, refreshVoiceStatus]);

  // The sidebar shows one group at a time; fetch that group server-side so
  // "Yours" isn't an all-thoughts page filtered down to nothing.
  const sidebarGroup = normalizeSidebarFilter(sourceFilter);
  const sidebarGroupRef = useRef(sidebarGroup);
  sidebarGroupRef.current = sidebarGroup;
  // Edward's group pages per source on the server, so "load more" is a
  // page index, not a row offset.
  const edwardsPageRef = useRef(0);
  const EDWARDS_PAGE = 15;

  const refreshConversations = useCallback(async () => {
    try {
      const group = sidebarGroupRef.current;
      // Edward's group is thought-heavy; a bigger first page lets the
      // threads / workers / scheduled sections show up without "load more".
      const data = await getConversations(group === "edwards" ? EDWARDS_PAGE : 30, 0, group === "edwards", undefined, group);
      if (sidebarGroupRef.current !== group) return; // filter changed mid-flight
      edwardsPageRef.current = 1;
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

  // Refetch the list when the sidebar group changes
  useEffect(() => {
    if (isAuthenticated && !authLoading) {
      refreshConversations();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sidebarGroup]);

  // Auto-refresh sidebar when external events create conversations
  useEffect(() => {
    if (!isAuthenticated || authLoading) return;

    const poll = async () => {
      // Skip while streaming or tab is hidden
      if (streamRef.current || document.visibilityState === "hidden") return;
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
        const group = sidebarGroupRef.current;
        const data = await getConversations(20, 0, group === "edwards", query.trim(), group);
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
      const group = sidebarGroupRef.current;
      const data =
        group === "edwards"
          ? await getConversations(EDWARDS_PAGE, edwardsPageRef.current * EDWARDS_PAGE, true, undefined, group)
          : await getConversations(50, conversationOffset, false, undefined, group);
      if (group === "edwards") edwardsPageRef.current += 1;
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

  // A brand-new chat gets its id from the first stream event. Put it in the
  // URL (replace, not push) so back/forward return to this conversation and
  // the page's reconciler sees URL == state instead of reloading it.
  useEffect(() => {
    if (typeof window === "undefined" || !conversationId) return;
    if (window.location.pathname !== "/chat") return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("c") === conversationId) return;
    if (params.get("c")) return; // a different conversation is named; the page reconciler owns that case
    params.set("c", conversationId);
    window.history.replaceState(window.history.state, "", `/chat?${params.toString()}`);
  }, [conversationId]);

  /** Remember the thread we are leaving, so coming back is instant. */
  const snapshotScreen = useCallback(() => {
    const id = conversationIdRef.current;
    if (!id || isLoadingConversationRef.current) return;
    const current = messagesRef.current;
    if (!current.length || current.some((m) => m.id === LOAD_FAILED_ID)) return;
    cacheRef.current.set(id, { conversation: activeConversationRef.current, messages: current });
  }, []);

  const releaseSentUrls = useCallback((onScreen: Message[]) => {
    const urls = sentUrlsRef.current;
    if (!urls.size) return;
    const lists: Message[][] = [onScreen, ...cacheRef.current.messageLists()];
    if (streamRef.current) lists.push([streamRef.current.user]);
    const keep = referencedBlobUrls(lists);
    for (const u of Array.from(urls)) {
      if (keep.has(u)) continue;
      urls.delete(u);
      try {
        URL.revokeObjectURL(u);
      } catch {
        /* already gone */
      }
    }
  }, []);

  useEffect(
    () => () => {
      for (const u of Array.from(sentUrlsRef.current)) {
        try {
          URL.revokeObjectURL(u);
        } catch {
          /* already gone */
        }
      }
      sentUrlsRef.current.clear();
    },
    [],
  );

  /** The live turn of a stream answering `id`, re-bound to the current screen. */
  const liveTurnFor = useCallback((id: string, epoch?: number): LiveTurn | null => {
    const s = streamRef.current;
    if (!s || s.conversationId !== id) return null;
    if (epoch !== undefined && s.epoch !== epoch) {
      s.epoch = epoch;
      setStreamView((v) => (v && v.assistantId === s.assistantId ? { ...v, epoch } : v));
    }
    return { user: s.user, assistant: s.assistant, baseUserCount: s.baseUserCount };
  }, []);

  const startNewChat = useCallback(() => {
    snapshotScreen();
    loadSeqRef.current += 1; // a load still in flight must not land on the new chat
    bumpScreen();
    setMessages([]);
    setScreenConversationId(undefined);
    setActiveConversation(null);
    setLoadingConversation(false);
    setIsRefreshingConversation(false);
  }, [snapshotScreen, bumpScreen, setScreenConversationId, setActiveConversation, setLoadingConversation]);

  const loadConversation = useCallback(async (id: string) => {
    const seq = ++loadSeqRef.current;
    snapshotScreen();
    const epoch = bumpScreen();
    // Optimistic: highlight sidebar immediately
    setScreenConversationId(id);
    const live = liveTurnFor(id, epoch);
    const cached = cacheRef.current.get(id);
    if (cached) {
      // Seen recently: show it now, fetch the server copy behind it.
      setMessages(withLiveTurn(cached.messages, live));
      setActiveConversation(cached.conversation);
      setLoadingConversation(false);
      setIsRefreshingConversation(true);
    } else {
      setMessages(live ? [live.user, live.assistant] : []);
      setActiveConversation(null);
      setLoadingConversation(true);
      setIsRefreshingConversation(false);
    }
    // Optimistically clear notification dot in sidebar
    setConversations((prev) =>
      prev.map((c) => c.id === id && c.notified_user ? { ...c, notified_user: false } : c)
    );
    // Opening the thread is the acknowledgement: it stops the interrupt
    // ladder (display → push → text) and marks the push opened in the ledger.
    ackConversationOpened(id);
    const doneBefore = turnsDoneRef.current.get(id)?.seq ?? 0;
    try {
      const data = await getConversation(id);
      let mapped = mapHistoryMessages(id, data);
      // A turn finished here while the copy was in flight: the copy may
      // predate it — keep the finished exchange (it replaces the server's
      // own copy when the server has it).
      const done = turnsDoneRef.current.get(id);
      if (done && done.seq !== doneBefore) mapped = withLiveTurn(mapped, done.pair);
      cacheRef.current.set(id, { conversation: data, messages: mapped });
      if (seq !== loadSeqRef.current) {
        releaseSentUrls(messagesRef.current);
        return; // the owner has moved on
      }
      setActiveConversation(data);
      const shown = withLiveTurn(mapped, liveTurnFor(id));
      setMessages(shown);
      releaseSentUrls(shown);
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      console.error("Error loading conversation:", error);
      if (!cached && !live) {
        setMessages([{
          id: LOAD_FAILED_ID,
          role: "assistant",
          content: "Failed to load this conversation. It may not have any messages yet.",
        }]);
      }
    } finally {
      if (seq === loadSeqRef.current) {
        setLoadingConversation(false);
        setIsRefreshingConversation(false);
      }
    }
  }, [snapshotScreen, bumpScreen, setScreenConversationId, liveTurnFor, setActiveConversation, setLoadingConversation, releaseSentUrls]);

  // A stream cut short (hidden tab, dropped connection): once the thread is
  // on screen and visible, wait for the server to finish and reload it.
  const recoveringRef = useRef(false);
  const recoverInterrupted = useCallback(async () => {
    if (document.visibilityState !== "visible" || recoveringRef.current) return;
    const interrupted = streamInterruptedRef.current;
    if (!interrupted) return;
    streamInterruptedRef.current = null;
    // Only recover if we're still viewing that conversation
    if (interrupted.conversationId !== conversationIdRef.current) {
      setRecovering(false);
      return;
    }
    recoveringRef.current = true;
    try {
      const stillProcessing = await isChatProcessing(interrupted.conversationId);
      if (stillProcessing) {
        // Poll until done (max 120s)
        for (let i = 0; i < 120; i++) {
          await new Promise((r) => setTimeout(r, 1000));
          if (conversationIdRef.current !== interrupted.conversationId) return;
          const still = await isChatProcessing(interrupted.conversationId);
          if (!still) break;
        }
      }
      if (conversationIdRef.current !== interrupted.conversationId) return;
      cacheRef.current.delete(interrupted.conversationId);
      await loadConversation(interrupted.conversationId);
    } finally {
      recoveringRef.current = false;
      setRecovering(false);
    }
  }, [loadConversation]);

  useEffect(() => {
    const onVisibilityRestore = () => {
      void recoverInterrupted();
    };
    document.addEventListener("visibilitychange", onVisibilityRestore);
    return () => document.removeEventListener("visibilitychange", onVisibilityRestore);
  }, [recoverInterrupted]);

  // Listen for service worker messages (e.g. push notification clicks on iOS PWA).
  // A tap goes wherever the push pointed — Edward's chain of thought
  // (/chat?c=…), Today, a settings room — by pushing the URL, so the page
  // reconciles it like any other navigation. It used to load chat state
  // without navigating, which from the Today tab changed nothing on screen.
  useEffect(() => {
    const handleNotificationNav = (url: string) => {
      const path = resolveNotificationNav(url, window.location.origin);
      if (!path) return;
      if (path.startsWith("/chat?")) setSourceFilter("all"); // the linked conversation must be visible in the sidebar
      router.push(path);
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
  }, [router]);

  // Called when the Undo window ends (lib/pendingDeletes.ts), which has just
  // stopped hiding the row: take it out of the list *and* the search
  // results in the same tick, before the network call, so it never flashes
  // back. A failure puts the server's list back and rejects, so the caller
  // can say so (2026-09-26 review: it used to reappear in search results
  // for good, flash back for the round trip, and fail silently).
  const searchQueryRef = useRef(searchQuery);
  searchQueryRef.current = searchQuery;
  const deleteConversation = useCallback(
    async (id: string) => {
      setConversations((prev) => prev.filter((c) => c.id !== id));
      setSearchResults((r) => (r ? r.filter((c) => c.id !== id) : r));
      try {
        await deleteConversationApi(id);
        cacheRef.current.delete(id);
        setQueues((q) => clearQueue(q, id));

        // If the deleted conversation is the current one, start fresh
        if (conversationIdRef.current === id) {
          startNewChat();
        }
      } catch (error) {
        console.error("Error deleting conversation:", error);
        void refreshConversations();
        if (searchQueryRef.current.trim()) setSearchQuery(searchQueryRef.current);
        throw error;
      }
    },
    [startNewChat, refreshConversations, setSearchQuery]
  );

  const enqueueFor = useCallback((key: string, content: string, files?: File[]) => {
    queueSeqRef.current += 1;
    const item: QueuedMessage = {
      id: `q-${Date.now()}-${queueSeqRef.current}`,
      text: content,
      files: files && files.length > 0 ? files : undefined,
    };
    setQueues((q) => enqueue(q, key, item).queues);
  }, []);

  const sendInternal = useCallback(
    async (content: string, files: File[] | undefined, origin: "owner" | "queue") => {
      if (!content.trim() && (!files || files.length === 0)) return;
      // A turn is already streaming (perhaps for another thread), or this
      // thread's history is still loading: the message waits in this
      // thread's queue and goes when that is over (the drain effect).
      if (streamRef.current || isLoadingConversationRef.current) {
        enqueueFor(queueKey(conversationIdRef.current), content, files);
        return;
      }

      // Build attachment metadata for the user message
      const messageAttachments: MessageAttachment[] = (files || []).map((file, i) => {
        const att: MessageAttachment = {
          id: `att-${Date.now()}-${i}`,
          filename: file.name,
          mime_type: file.type,
          size: file.size,
        };
        // Create local preview URL for images and voice memos
        if (file.type.startsWith("image/") || file.type.startsWith("audio/")) {
          att.preview_url = URL.createObjectURL(file);
          sentUrlsRef.current.add(att.preview_url);
        }
        return att;
      });

      const stamp = Date.now();
      const userMessage: Message = {
        id: stamp.toString(),
        role: "user",
        content,
        attachments: messageAttachments.length > 0 ? messageAttachments : undefined,
      };
      const assistantId = (stamp + 1).toString();
      const assistantMessage: Message = {
        id: assistantId,
        role: "assistant",
        content: "",
        codeBlocks: [],
        progressSteps: [],
        isThinking: false,
        wasInterrupted: false,
      };

      const startedIn = conversationIdRef.current;
      const s: ActiveStream = {
        assistantId,
        conversationId: startedIn,
        startedIn,
        epoch: screenEpochRef.current,
        controller: new AbortController(),
        user: userMessage,
        assistant: assistantMessage,
        baseUserCount: messagesRef.current.filter((m) => m.role === "user" && m.id !== LOAD_FAILED_ID).length,
        cursor: newCursor(),
        serverError: false,
      };
      streamRef.current = s;
      streamInterruptedRef.current = null;
      const onScreen = () => s.epoch === screenEpochRef.current;

      setMessages((prev) => [...prev, userMessage, assistantMessage]);
      // The owner's own send lands them at the bottom; a queue drain keeps
      // their place (they may be reading the answer it waited for).
      if (origin === "owner") setUserSendSeq((n) => n + 1);
      setStreamView({ assistantId, conversationId: startedIn ?? null, epoch: s.epoch });
      setTurn(startTurn(assistantId, startedIn, stamp));
      setCanStop(true);

      let outcome: StreamOutcome = "cut";
      let refusal: string | undefined;
      try {
        for await (const event of streamChatEvents(content, startedIn, s.controller.signal, files)) {
          // A new chat learns its id from the first event. The screen follows
          // only if it still shows this chat.
          if (event.conversation_id && !s.conversationId) {
            const assigned = event.conversation_id;
            s.conversationId = assigned;
            if (onScreen()) setScreenConversationId(assigned);
            setStreamView((v) => (v && v.assistantId === assistantId ? { ...v, conversationId: assigned } : v));
            if (!startedIn) {
              setQueues((q) => rekeyQueue(q, NEW_CHAT_KEY, assigned));
              setPausedQueues((p) => (p.has(NEW_CHAT_KEY) ? withKey(withoutKeys(p, (k) => k === NEW_CHAT_KEY), assigned) : p));
            }
          }

          if ((event.type as string) === "error") s.serverError = true;

          if (event.type === "user_message") {
            // Voice memo: fill in the transcript and the stored file ids.
            s.user = applyUserMessageEvent(s.user, event);
            const user = s.user;
            if (onScreen()) setMessages((prev) => replaceMessage(prev, user.id, user));
            continue;
          }

          const now = Date.now();
          const step = advanceCursor(s.cursor, event, assistantId, now);
          s.cursor = step.cursor;
          const next = applyStreamEvent(s.assistant, event, step.ctx);
          if (next !== s.assistant) {
            s.assistant = next;
            if (onScreen()) setMessages((prev) => replaceMessage(prev, assistantId, next));
          }
          setTurn((t) => (t && t.id === assistantId ? reduceTurn(t, event, now) : t));
          if (event.type === "done") outcome = "done";
        }
        if (outcome !== "done" && s.serverError) outcome = "error";
      } catch (error) {
        if (s.controller.signal.aborted || (error instanceof Error && error.name === "AbortError")) {
          outcome = "stopped"; // Stop: keep the partial answer, marked interrupted
        } else if (document.visibilityState === "hidden") {
          outcome = "cut"; // the phone locked mid-turn; the server may still be answering
        } else {
          console.error("Error sending message:", error);
          outcome = "error";
          refusal = error instanceof ChatRefusedError ? error.message : undefined;
        }
      }

      // Whatever the ending, nothing is left live (isThinking, pending tool
      // args, running tools, executing code), on screen or in the cache.
      s.assistant = finishStreamMessage(s.assistant, outcome, { refusal });
      const final = s.assistant;
      if (onScreen()) {
        setMessages((prev) => replaceMessage(prev, assistantId, final));
      } else if (s.conversationId) {
        cacheRef.current.patch(s.conversationId, (e) => ({ ...e, messages: replaceMessage(e.messages, assistantId, final) }));
      }
      if (s.conversationId) {
        const prev = turnsDoneRef.current.get(s.conversationId)?.seq ?? 0;
        turnsDoneRef.current.set(s.conversationId, {
          seq: prev + 1,
          pair: { user: s.user, assistant: final, baseUserCount: s.baseUserCount },
        });
      }
      // Stop means stop: with messages queued for this thread, the queue
      // waits for the owner (the chip's "Send now") instead of starting the next
      // turn the moment this one ends. Set in the same batch as the end of
      // the stream, so the drain effect sees it.
      if (outcome === "stopped") {
        const key = queueKey(s.conversationId);
        if (queuedFor(queuesRef.current, key).length > 0) setPausedQueues((p) => withKey(p, key));
      }
      setTurn((t) => (t && t.id === assistantId ? endTurn(t, turnOutcomeFor(outcome), Date.now()) : t));
      if (streamRef.current === s) streamRef.current = null;
      setStreamView((v) => (v && v.assistantId === assistantId ? null : v));
      setCanStop(false);

      if (outcome === "cut" && s.conversationId) {
        // Closed without `done`: reload the thread once the server is through.
        streamInterruptedRef.current = { conversationId: s.conversationId };
        setRecovering(true);
        void recoverInterrupted();
      }
      // The new/updated conversation for the sidebar, wherever the screen is.
      void refreshConversations();
    },
    [enqueueFor, setScreenConversationId, recoverInterrupted, refreshConversations]
  );

  const sendMessage = useCallback(
    (content: string, files?: File[]) => sendInternal(content, files, "owner"),
    [sendInternal],
  );

  const queueMessage = useCallback(
    (content: string, files?: File[]) => {
      enqueueFor(queueKey(conversationIdRef.current), content, files);
    },
    [enqueueFor],
  );

  const removeQueuedMessage = useCallback(
    (id: string) => {
      const key = queueKey(conversationIdRef.current);
      setQueues((q) => removeQueued(q, key, id));
    },
    [],
  );

  // When the turn ends — done or error — the next queued message for the
  // conversation on screen goes, one at a time. Held while any turn streams
  // (one at a time), while history loads, while live voice owns the thread,
  // while a cut stream is being recovered (the server may still be
  // answering), and after the owner's Stop until they say "Send now".
  const streamBusy = streamView !== null;
  useEffect(() => {
    const key = queueKey(conversationId);
    const { item } = nextOnDone(queues, key, {
      isLoading: streamBusy,
      isLoadingConversation,
      interrupted: recovering || streamInterruptedRef.current !== null,
      voiceMode: isVoiceMode,
      paused: pausedQueues.has(key),
    });
    if (!item || lastDrainedRef.current === item.id) return;
    lastDrainedRef.current = item.id;
    setQueues((q) => removeQueued(q, key, item.id));
    void sendInternal(item.text, item.files, "queue");
  }, [queues, conversationId, streamBusy, isLoadingConversation, isVoiceMode, recovering, pausedQueues, sendInternal]);

  // A paused queue that has emptied (the owner removed the chips) is no longer paused.
  useEffect(() => {
    setPausedQueues((p) => withoutKeys(p, (k) => queuedFor(queues, k).length === 0));
  }, [queues]);

  const resumeQueue = useCallback(
    () => setPausedQueues((p) => withoutKeys(p, (k) => k === queueKey(conversationIdRef.current))),
    [],
  );

  const queuedMessages = queuedFor(queues, queueKey(conversationId));
  const queuePaused = pausedQueues.has(queueKey(conversationId)) && queuedMessages.length > 0;

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => !prev);
  }, []);

  const stopGeneration = useCallback(() => {
    const s = streamRef.current;
    if (s && s.epoch === screenEpochRef.current && !s.controller.signal.aborted) {
      s.controller.abort();
      setCanStop(false);
    }
  }, []);

  // Simplified voice mode: just toggle the flag, WebSocket is managed by ContinuousVoiceView
  const startVoiceMode = useCallback(() => {
    if (!voiceEnabled) return;
    setIsVoiceMode(true);
  }, [voiceEnabled]);

  const enterVoiceMode = useCallback(() => {
    if (!voiceEnabled) return;
    setIsVoiceMode(true);
  }, [voiceEnabled]);

  // Exit voice mode: refresh the sidebar and show what was said in the
  // chat. The backend only creates the conversation row on the first
  // spoken turn, so an id with no row behind it means nothing was said —
  // fall back to a fresh chat rather than keeping a ghost id around.
  const exitVoiceMode = useCallback(async () => {
    setIsVoiceMode(false);
    setVoicePhase(null);
    await refreshConversations();
    const id = conversationIdRef.current;
    if (!id) return;
    try {
      await getConversation(id);
    } catch {
      startNewChat();
      return;
    }
    cacheRef.current.delete(id); // voice added turns the cached copy lacks
    await loadConversation(id);
  }, [refreshConversations, loadConversation, startNewChat]);

  // Leaving /chat while live voice is on unmounts the voice view: end the
  // mode at once, or presence reads "Listening" with no mic open, the queue
  // stays held and /chat reopens the mic unasked (review 2026-09-26). The
  // voice turns the cached copy lacks are dropped so /chat reloads them.
  const endVoiceMode = useCallback(() => {
    setIsVoiceMode(false);
    setVoicePhase(null);
    const id = conversationIdRef.current;
    if (id) cacheRef.current.delete(id);
  }, []);
  const pathname = usePathname();
  const prevPathRef = useRef(pathname);
  useEffect(() => {
    const was = prevPathRef.current;
    prevPathRef.current = pathname;
    if (was != null && pathname != null && shouldEndVoiceOnRoute(was, pathname, isVoiceMode)) endVoiceMode();
  }, [pathname, isVoiceMode, endVoiceMode]);

  // Allow ContinuousVoiceView to sync the conversation ID back to context
  const setVoiceConversationId = useCallback((id: string) => {
    setScreenConversationId(id);
  }, [setScreenConversationId]);

  const isLoading = streamView !== null && streamView.epoch === screenEpoch;

  const listValue = useMemo<ChatListContextType>(
    () => ({
      conversationId,
      conversations,
      activeConversation,
      sidebarCollapsed,
      sourceFilter,
      searchQuery,
      setSearchQuery,
      searchResults,
      hasMore,
      isLoadingMore,
      loadMoreConversations,
      clearSearch,
      startNewChat,
      loadConversation,
      deleteConversation,
      refreshConversations,
      toggleSidebar,
      setSourceFilter,
    }),
    [
      conversationId,
      conversations,
      activeConversation,
      sidebarCollapsed,
      sourceFilter,
      searchQuery,
      setSearchQuery,
      searchResults,
      hasMore,
      isLoadingMore,
      loadMoreConversations,
      clearSearch,
      startNewChat,
      loadConversation,
      deleteConversation,
      refreshConversations,
      toggleSidebar,
      setSourceFilter,
    ],
  );

  return (
    <ChatContext.Provider
      value={{
        messages,
        conversationId,
        isLoading,
        isLoadingConversation,
        isRefreshingConversation,
        canStop: canStopState && isLoading,
        conversations,
        activeConversation,
        sidebarCollapsed,
        sourceFilter,
        streamingMessageId: streamView?.assistantId ?? null,
        streamingConversationId: streamView?.conversationId ?? null,
        turn,
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
        userSendSeq,
        stopGeneration,
        queuedMessages,
        // Empty queues are deleted (composerQueue.withQueue), so any key is a wait.
        hasQueuedMessages: Object.keys(queues).length > 0,
        queueMessage,
        removeQueuedMessage,
        queuePaused,
        resumeQueue,
        startNewChat,
        loadConversation,
        deleteConversation,
        refreshConversations,
        toggleSidebar,
        setSourceFilter,
        // Voice
        voiceEnabled,
        isVoiceMode,
        voicePhase,
        setVoicePhase,
        startVoiceMode,
        enterVoiceMode,
        exitVoiceMode,
        refreshVoiceStatus,
        setVoiceConversationId,
      }}
    >
      <ChatListContext.Provider value={listValue}>{children}</ChatListContext.Provider>
    </ChatContext.Provider>
  );
}

/** The conversation list (sidebar, drawer, dock, header): it changes with
 *  the list, never with a streamed token. */
export function useChatList(): ChatListContextType {
  const context = useContext(ChatListContext);
  if (context === undefined) {
    throw new Error("useChatList must be used within a ChatProvider");
  }
  return context;
}

export function useChat() {
  const context = useContext(ChatContext);
  if (context === undefined) {
    throw new Error("useChat must be used within a ChatProvider");
  }
  return context;
}

/** What presence reads from the chat (lib/presence.tsx). Null outside a ChatProvider. */
export interface ChatPresenceSlice {
  turn: Turn | null;
  isVoiceMode: boolean;
  voicePhase: VoicePhase | null;
}

export function useChatPresence(): ChatPresenceSlice | null {
  const context = useContext(ChatContext);
  if (!context) return null;
  return { turn: context.turn, isVoiceMode: context.isVoiceMode, voicePhase: context.voicePhase };
}

const NO_QUEUE: readonly QueuedMessage[] = [];

/**
 * The composer's view of the queue. Safe outside a ChatProvider (no queue:
 * typing during a stream then falls back to the old Stop-only slot).
 */
export function useComposerQueue(): {
  queued: readonly QueuedMessage[];
  /** Any thread's queue, not just the one on screen: they live only in memory. */
  anyQueued: boolean;
  queueMessage?: (content: string, files?: File[]) => void;
  removeQueuedMessage?: (id: string) => void;
  /** The owner stopped the turn: the queue waits for resumeQueue ("Send now"). */
  paused: boolean;
  resumeQueue?: () => void;
} {
  const context = useContext(ChatContext);
  return {
    queued: context?.queuedMessages ?? NO_QUEUE,
    anyQueued: context?.hasQueuedMessages ?? false,
    queueMessage: context?.queueMessage,
    removeQueuedMessage: context?.removeQueuedMessage,
    paused: context?.queuePaused ?? false,
    resumeQueue: context?.resumeQueue,
  };
}
