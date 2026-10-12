import type { CodexUsagePayload } from "./codexUsage";
import type { WishItem, WishPriority, WishStatus } from "./wishlist";
import type { MindIntention, MindWake } from "./mindLog";
import type { ThoughtMeta } from "./thoughtEntry";
import { redirectToLogin } from "./authSession";
// Use relative URLs in browser (works for any domain), absolute for SSR
export const API_URL = typeof window !== "undefined"
  ? ""
  : (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000");

// Auth-aware fetch wrapper that includes credentials and handles 401
async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const response = await fetch(url, {
    ...options,
    credentials: "include",
  });

  // On 401: forget the optimistic hint and go to login (a page already on
  // /login is left alone). See lib/authSession.ts.
  if (response.status === 401) {
    redirectToLogin();
    throw new Error("Authentication required");
  }

  return response;
}

export interface Settings {
  name: string;
  personality: string;
  provider: string;
  model: string;
  temperature: number;
  system_prompt: string;
  debug_streaming?: boolean;
  timezone: string;
}

export interface Model {
  id: string;
  name: string;
}

// Auth API types
export interface AuthStatus {
  configured: boolean;
  authenticated: boolean;
  /** The owner's name (EDWARD_OWNER_NAME), "" when unset; served before
   *  sign-in so the login page can greet them (lib/owner.ts). */
  owner_name?: string;
  /** No password yet: the setup form asks for the one-time code in
   *  backend/.setup_code on the Mac (backend/services/setup_code.py). */
  setup_code_required?: boolean;
}

// Auth API functions
export async function getAuthStatus(): Promise<AuthStatus> {
  const response = await fetch(`${API_URL}/api/auth/status`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error("Failed to fetch auth status");
  }
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("Backend returned non-JSON response — is the server running?");
  }
}

/** The first password, with the one-time setup code from the Mac. Throws
 *  the server's own refusal (a wrong code, not on this Mac, already set),
 *  which the login page shows as it is. */
export async function setupPassword(password: string, setupCode?: string): Promise<void> {
  const response = await fetch(`${API_URL}/api/auth/setup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(setupCode === undefined ? { password } : { password, setup_code: setupCode }),
  });
  if (!response.ok) {
    let message = "Failed to setup password";
    try {
      const data = await response.json();
      if (typeof data.detail === "string" && data.detail) message = data.detail;
    } catch {
      // Backend may be down — response isn't JSON
      if (response.status >= 500 || response.status === 0) {
        message = "Backend unavailable — is the server running?";
      }
    }
    throw new Error(message);
  }
}

export async function login(password: string): Promise<void> {
  const response = await fetch(`${API_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ password }),
  });
  if (!response.ok) {
    let message = "Login failed";
    try {
      const data = await response.json();
      message = data.detail || message;
    } catch {
      // Backend may be down — response isn't JSON
      if (response.status >= 500 || response.status === 0) {
        message = "Backend unavailable — is the server running?";
      }
    }
    throw new Error(message);
  }
}

/** Throws unless the server confirmed: a sign-out that never reached the
 *  server leaves the cookie alive (AuthContext then keeps the session). */
export async function logout(): Promise<void> {
  const response = await fetch(`${API_URL}/api/auth/logout`, {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) throw new Error(`Sign out failed (HTTP ${response.status})`);
}

export async function isChatProcessing(conversationId: string): Promise<boolean> {
  try {
    const response = await authFetch(`${API_URL}/api/chat/${conversationId}/processing`);
    if (!response.ok) return false;
    const data = await response.json();
    return data.processing === true;
  } catch {
    return false;
  }
}

export async function fetchGreeting(): Promise<string> {
  try {
    const response = await authFetch(`${API_URL}/api/greeting`, {
      method: "POST",
    });
    if (!response.ok) {
      throw new Error("Failed to fetch greeting");
    }
    const data = await response.json();
    return data.greeting;
  } catch (error) {
    console.error("Failed to fetch greeting:", error);
    return "Hey.";
  }
}

/**
 * G5: stream the greeting one delta at a time. On cache hit the
 * server emits one chunk with the cached text and closes; on miss
 * each LLM delta arrives as its own chunk. The frontend appends
 * each chunk to the visible greeting so even cold misses feel alive.
 */
export async function* fetchGreetingStream(
  signal?: AbortSignal,
  opts: { fresh?: boolean } = {},
): AsyncGenerator<string, void, unknown> {
  // `fresh=1`: the owner just resolved something the cached line may name.
  const response = await fetch(`${API_URL}/api/greeting/stream${opts.fresh ? "?fresh=1" : ""}`, {
    method: "GET",
    credentials: "include",
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`greeting stream failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    while (buffer.includes("\n\n")) {
      const sepIdx = buffer.indexOf("\n\n");
      const block = buffer.slice(0, sepIdx);
      buffer = buffer.slice(sepIdx + 2);
      for (const line of block.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (data === "[DONE]") return;
        try {
          const parsed = JSON.parse(data);
          if (typeof parsed.chunk === "string" && parsed.chunk) {
            yield parsed.chunk;
          }
        } catch {
          // ignore malformed line
        }
      }
    }
  }
}

export async function getSettings(): Promise<Settings> {
  const response = await authFetch(`${API_URL}/api/settings`);
  if (!response.ok) {
    throw new Error("Failed to fetch settings");
  }
  return response.json();
}

export async function updateSettings(settings: Partial<Settings>): Promise<Settings> {
  const response = await authFetch(`${API_URL}/api/settings`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(settings),
  });
  if (!response.ok) {
    throw new Error("Failed to update settings");
  }
  return response.json();
}

export interface ProviderInfo {
  available: boolean;
  models: Model[];
}

export interface ProvidersResponse {
  providers: Record<string, ProviderInfo>;
  models: Model[]; // backwards-compatible flat list
}

export async function getModels(): Promise<Model[]> {
  const response = await authFetch(`${API_URL}/api/settings/models`);
  if (!response.ok) {
    throw new Error("Failed to fetch models");
  }
  const data = await response.json();
  return data.models;
}

export async function getProviders(): Promise<ProvidersResponse> {
  const response = await authFetch(`${API_URL}/api/settings/models`);
  if (!response.ok) {
    throw new Error("Failed to fetch providers");
  }
  return response.json();
}

export interface MemoryItem {
  id: string;
  content: string;
  memory_type: string;
  importance: number;
  temporal_nature: string;
  tier: string;           // "observation" | "belief" | "knowledge"
  reinforcement_count: number;
  source_conversation_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  last_accessed: string | null;
  access_count: number;
  user_id: string | null;
  score?: number | null;
}

export interface MemoryStats {
  total: number;
  /** Rows not retired (valid_until null or in the future). */
  live?: number;
  /** Live rows never used in a turn since the 2026-08-21 counter reset. */
  unused_live?: number;
  created_last_24h?: number;
  by_type: Record<string, number>;
  by_tier: Record<string, number>;
  average_importance: number;
}

export interface MemoriesResponse {
  memories: MemoryItem[];
  stats: MemoryStats;
  pagination: {
    limit: number;
    offset: number;
    total: number;
  };
}

export async function getMemories(limit = 50, offset = 0): Promise<MemoriesResponse> {
  const response = await authFetch(`${API_URL}/api/debug/memories?limit=${limit}&offset=${offset}`);
  if (!response.ok) {
    throw new Error("Failed to fetch memories");
  }
  return response.json();
}

export async function getMemoryStats(): Promise<MemoryStats> {
  const response = await authFetch(`${API_URL}/api/debug/memories/stats`);
  if (!response.ok) {
    throw new Error("Failed to fetch memory stats");
  }
  return response.json();
}

// Memory management API functions
export async function searchMemories(
  query?: string,
  memoryType?: string,
  minImportance?: number,
  limit = 50,
  offset = 0,
  temporalNature?: string,
  tier?: string
): Promise<MemoriesResponse> {
  const params = new URLSearchParams();
  if (query) params.set("query", query);
  if (memoryType) params.set("memory_type", memoryType);
  if (minImportance !== undefined) params.set("min_importance", minImportance.toString());
  if (temporalNature) params.set("temporal_nature", temporalNature);
  if (tier) params.set("tier", tier);
  params.set("limit", limit.toString());
  params.set("offset", offset.toString());

  const response = await authFetch(`${API_URL}/api/memories?${params}`);
  if (!response.ok) {
    throw new Error("Failed to search memories");
  }
  return response.json();
}

export async function deleteMemory(memoryId: string): Promise<{ status: string; id: string }> {
  const response = await authFetch(`${API_URL}/api/memories/${memoryId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error("Failed to delete memory");
  }
  return response.json();
}

export async function updateMemory(
  memoryId: string,
  updates: { content?: string; memory_type?: string; importance?: number }
): Promise<MemoryItem> {
  const response = await authFetch(`${API_URL}/api/memories/${memoryId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(updates),
  });
  if (!response.ok) {
    throw new Error("Failed to update memory");
  }
  return response.json();
}

// Document API types
export interface DocumentItem {
  id: string;
  title: string;
  content: string;
  tags: string | null;
  source_conversation_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  last_accessed: string | null;
  access_count: number;
  user_id: string | null;
  score?: number | null;
}

export interface DocumentStats {
  total: number;
  by_tag: Record<string, number>;
}

export interface DocumentsResponse {
  documents: DocumentItem[];
  stats: DocumentStats;
  pagination: {
    limit: number;
    offset: number;
    total: number;
  };
}

// Document API functions
export async function searchDocuments(
  query?: string,
  tags?: string,
  limit = 50,
  offset = 0
): Promise<DocumentsResponse> {
  const params = new URLSearchParams();
  if (query) params.set("query", query);
  if (tags) params.set("tags", tags);
  params.set("limit", limit.toString());
  params.set("offset", offset.toString());

  const response = await authFetch(`${API_URL}/api/documents?${params}`);
  if (!response.ok) {
    throw new Error("Failed to search documents");
  }
  return response.json();
}

export async function getDocument(documentId: string): Promise<DocumentItem> {
  const response = await authFetch(`${API_URL}/api/documents/${documentId}`);
  if (!response.ok) {
    throw new Error("Failed to fetch document");
  }
  return response.json();
}

export async function createDocument(data: {
  title: string;
  content: string;
  tags?: string;
}): Promise<DocumentItem> {
  const response = await authFetch(`${API_URL}/api/documents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error("Failed to create document");
  }
  return response.json();
}

export async function updateDocument(
  documentId: string,
  updates: { title?: string; content?: string; tags?: string }
): Promise<DocumentItem> {
  const response = await authFetch(`${API_URL}/api/documents/${documentId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!response.ok) {
    throw new Error("Failed to update document");
  }
  return response.json();
}

export async function deleteDocument(documentId: string): Promise<{ status: string; id: string }> {
  const response = await authFetch(`${API_URL}/api/documents/${documentId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error("Failed to delete document");
  }
  return response.json();
}

// Conversation types
export type ConversationSource = "user" | "scheduled_event" | "external_message" | "heartbeat" | "orchestrator_worker" | "edward_thought";
export type ConversationChannel = "text" | "voice";

export interface Conversation {
  id: string;
  title: string;
  source: ConversationSource;
  channel: ConversationChannel;
  notified_user: boolean;  // True if Edward tried to get user's attention
  created_at: string;
  updated_at: string;
  /** Latest message in the thread (heartbeat threads), when known. */
  last_activity_at?: string | null;
  message_count: number;
  /** A wake of the mind that took an action (2026-09-25): the chat opens it
   *  as an entry with a "Right call?" (lib/thoughtEntry.ts). */
  thought?: ThoughtMeta | null;
}

export interface ConversationMessageAttachment {
  file_id?: string;
  filename: string;
  mime_type: string;
  size?: number;
}

export interface ConversationMessage {
  role: string;
  content: string;
  attachments?: ConversationMessageAttachment[];
  is_trigger?: boolean;
  trigger_type?: string;
}

export interface CCSessionSummary {
  task_id: string;
  description: string;
  status: string;
  result_summary: string | null;
  error: string | null;
}

export interface ConversationWithMessages extends Conversation {
  messages: ConversationMessage[];
  cc_sessions?: CCSessionSummary[];
}

export interface ConversationsResponse {
  conversations: Conversation[];
  has_more: boolean;
}

// Conversation API functions
export async function getConversations(
  limit = 50,
  offset = 0,
  includeScheduled = false,
  search?: string,
  group?: "yours" | "edwards",
): Promise<ConversationsResponse> {
  const params = new URLSearchParams({
    limit: limit.toString(),
    offset: offset.toString(),
    include_scheduled: includeScheduled.toString(),
  });
  if (search) {
    params.set("search", search);
  }
  if (group) {
    params.set("group", group);
  }
  const response = await authFetch(`${API_URL}/api/conversations?${params}`);
  if (!response.ok) {
    throw new Error("Failed to fetch conversations");
  }
  return response.json();
}

export async function getConversation(conversationId: string): Promise<ConversationWithMessages> {
  const response = await authFetch(`${API_URL}/api/conversations/${conversationId}`, {
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) {
    throw new Error("Failed to fetch conversation");
  }
  return response.json();
}

export async function deleteConversation(conversationId: string): Promise<{ status: string; id: string }> {
  // keepalive: the undo window flushes as the page goes away (pagehide, or
  // hidden on iOS); without it Safari cancels the request with the page.
  const response = await authFetch(`${API_URL}/api/conversations/${conversationId}`, {
    method: "DELETE",
    keepalive: true,
  });
  if (!response.ok) {
    throw new Error("Failed to delete conversation");
  }
  return response.json();
}

// Scheduled Events API types
export interface ScheduledEventItem {
  id: string;
  conversation_id: string | null;
  description: string;
  scheduled_at: string;
  next_fire_at: string;
  recurrence_pattern: string | null;
  status: string;
  created_by: string;
  delivery_channel: string | null;
  last_fired_at: string | null;
  fire_count: number;
  last_result: string | null;
  created_at: string;
  updated_at: string;
  /** `{type: "mind_wake" | "wakeup_tick" | "expression_pulse" | "surplus_tick", …}`
   *  (an object or its JSON text) when the backend sends it; lib/eventGroups
   *  prefers it to guessing from the description. */
  event_metadata?: Record<string, unknown> | string | null;
  /** The row's kind as the API names it ("mind_wake", "wakeup_tick",
   *  "expression_pulse"…); null for the owner's plain reminders (2026-09-26). */
  event_type?: string | null;
}

// Scheduled Events API functions
export async function listEvents(status?: string, sortOrder?: string, search?: string): Promise<ScheduledEventItem[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (sortOrder) params.set("sort_order", sortOrder);
  if (search) params.set("search", search);
  const response = await authFetch(`${API_URL}/api/events?${params}`);
  if (!response.ok) {
    throw new Error("Failed to fetch events");
  }
  return response.json();
}

/** One scheduled row by id (a deep link to one that is on neither page). */
export async function getEvent(id: string): Promise<ScheduledEventItem> {
  const response = await authFetch(`${API_URL}/api/events/${encodeURIComponent(id)}`);
  if (!response.ok) {
    throw new Error("Failed to fetch event");
  }
  return response.json();
}

export async function cancelEvent(id: string): Promise<ScheduledEventItem> {
  const response = await authFetch(`${API_URL}/api/events/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: "cancelled" }),
  });
  if (!response.ok) {
    throw new Error("Failed to cancel event");
  }
  return response.json();
}

export async function deleteEvent(id: string): Promise<{ status: string }> {
  const response = await authFetch(`${API_URL}/api/events/${id}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error("Failed to delete event");
  }
  return response.json();
}

// Persistent Databases API types
export interface PersistentDatabase {
  id: string;
  name: string;
  schema_name: string;
  description: string | null;
  created_at: string | null;
  updated_at: string | null;
  last_accessed: string | null;
}

export interface DatabaseTable {
  name: string;
  column_count: number;
}

export interface DatabaseColumn {
  name: string;
  data_type: string;
  is_nullable: string;
  column_default: string | null;
  ordinal_position: number;
}

// Persistent Databases API functions
export async function listDatabases(): Promise<PersistentDatabase[]> {
  const response = await authFetch(`${API_URL}/api/databases`);
  if (!response.ok) {
    throw new Error("Failed to fetch databases");
  }
  return response.json();
}

export async function getDatabaseTables(name: string): Promise<DatabaseTable[]> {
  const response = await authFetch(`${API_URL}/api/databases/${encodeURIComponent(name)}/tables`);
  if (!response.ok) {
    throw new Error("Failed to fetch database tables");
  }
  return response.json();
}

export async function getDatabaseColumns(dbName: string, tableName: string): Promise<DatabaseColumn[]> {
  const response = await authFetch(`${API_URL}/api/databases/${encodeURIComponent(dbName)}/tables/${encodeURIComponent(tableName)}/columns`);
  if (!response.ok) {
    throw new Error("Failed to fetch table columns");
  }
  return response.json();
}

export async function deleteDatabase(name: string): Promise<{ status: string; name: string }> {
  const response = await authFetch(`${API_URL}/api/databases/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error("Failed to delete database");
  }
  return response.json();
}

// File Storage API types
export interface StoredFileItem {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  category: string;
  description: string | null;
  tags: string | null;
  source: string;
  source_conversation_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  last_accessed: string | null;
  access_count: number;
  download_url: string;
}

export interface FilesResponse {
  files: StoredFileItem[];
  pagination: {
    limit: number;
    offset: number;
    total: number;
  };
}

// File Storage API functions
export async function listFiles(
  category?: string,
  source?: string,
  limit = 10,
  offset = 0
): Promise<FilesResponse> {
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (source) params.set("source", source);
  params.set("limit", limit.toString());
  params.set("offset", offset.toString());

  const response = await authFetch(`${API_URL}/api/files?${params}`);
  if (!response.ok) {
    throw new Error("Failed to fetch files");
  }
  return response.json();
}

export async function deleteFile(id: string): Promise<{ status: string; id: string }> {
  const response = await authFetch(`${API_URL}/api/files/${id}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error("Failed to delete file");
  }
  return response.json();
}

// Legacy interface for backwards compatibility
export interface StreamChatChunk {
  content?: string;
  conversation_id?: string;
  done?: boolean;
}

// New structured event types for SSE streaming
export type StreamEventType =
  | "thinking"
  | "reasoning"  // N4: Codex reasoning_summary deltas while in thinking phase
  | "tool_args_delta"  // partial JSON for an in-flight function call
  | "memory_match"     // debug-only — per-memory pgvector match
  | "progress"
  | "tool_start"
  | "code"
  | "execution_output"
  | "execution_result"
  | "tool_end"
  | "content"
  | "done"
  | "interrupted"
  | "plan_created"
  | "plan_step_update"
  | "plan_updated"
  | "plan_completed"
  | "cc_session_start"
  | "cc_text"
  | "cc_tool_use"
  | "cc_tool_result"
  | "cc_session_end"
  | "user_message";   // voice memo: what the user actually said, before Edward answers

export type ProgressStatus = "started" | "completed" | "error";

export interface ProgressStepData {
  step: string;
  status: ProgressStatus;
  message: string;
  count?: number;
  tool_name?: string;
}

export interface PlanStepData {
  id: string;
  title: string;
  status: "pending" | "in_progress" | "completed" | "error";
  result?: string | null;
}

export interface StreamEvent {
  type: StreamEventType;
  conversation_id: string;
  // Type-specific fields
  content?: string;        // for thinking, content, interrupted
  tool_name?: string;      // for tool_start, tool_end, progress
  args_summary?: string;   // for tool_start (non-execution tools)
  code?: string;           // for code
  language?: string;       // for code
  output?: string;         // for execution_output
  stream?: "stdout" | "stderr";  // for execution_output
  success?: boolean;       // for execution_result
  duration_ms?: number;    // for execution_result
  result?: string;         // for tool_end (truncated result)
  // tool_args_delta-specific fields
  call_id?: string;        // for tool_args_delta — Codex call id
  delta?: string;          // for tool_args_delta — partial JSON fragment
  // memory_match-specific fields
  memory_id?: string;      // for memory_match
  memory_type?: string;    // for memory_match
  content_preview?: string;// for memory_match
  score?: number;          // for memory_match
  // Progress-specific fields
  step?: string;           // for progress (memory_search, tool_execution, generating)
  status?: ProgressStatus; // for progress
  message?: string;        // for progress
  count?: number;          // for progress (e.g., memory count)
  // Plan-specific fields
  plan_steps?: PlanStepData[];   // for plan_created, plan_updated, plan_completed
  step_id?: string;              // for plan_step_update
  step_status?: string;          // for plan_step_update
  step_result?: string | null;   // for plan_step_update
  plan_summary?: string | null;  // for plan_completed
  // CC session fields
  task_id?: string;              // for all cc_* events
  task_description?: string;     // for cc_session_start
  text?: string;                 // for cc_text, cc_tool_result
  cc_tool_name?: string;         // for cc_tool_use
  tool_input?: string;           // for cc_tool_use
  result_summary?: string;       // for cc_session_end
  // user_message (voice memo transcript) fields
  attachments?: Array<{ file_id?: string; filename?: string; mime_type?: string; size?: number; transcript?: string }>;
}

// Skills API types
export type SkillStatusType = "connected" | "connecting" | "error" | "disabled";

export interface SkillMetadata {
  phone_number?: string;
  tools_count?: number;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  status: SkillStatusType;
  status_message: string | null;
  metadata: SkillMetadata | null;
}

export interface SkillsResponse {
  skills: Skill[];
  last_reload: string | null;
}

// Skills API functions
export async function getSkills(): Promise<SkillsResponse> {
  const response = await authFetch(`${API_URL}/api/skills`);
  if (!response.ok) {
    throw new Error("Failed to fetch skills");
  }
  return response.json();
}

export async function setSkillEnabled(skillId: string, enabled: boolean): Promise<Skill> {
  const response = await authFetch(`${API_URL}/api/skills/${skillId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) {
    throw new Error("Failed to update skill");
  }
  return response.json();
}

export async function reloadSkills(): Promise<SkillsResponse> {
  const response = await authFetch(`${API_URL}/api/skills/reload`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error("Failed to reload skills");
  }
  return response.json();
}

// Custom MCP servers API types
export interface CustomMCPServer {
  id: string;
  name: string;
  description: string | null;
  package_name: string;
  runtime: "npx" | "uvx";
  tool_prefix: string;
  enabled: boolean;
  status: "connected" | "starting" | "stopped" | "disabled" | "error";
  error: string | null;
  tool_names: string[];
  tool_count: number;
  args: string[];
  env_var_keys: string[];
  source_url: string | null;
  added_at: string | null;
  updated_at: string | null;
}

export interface CustomMCPServersResponse {
  servers: CustomMCPServer[];
}

// Custom MCP servers API functions
export async function getCustomMCPServers(): Promise<CustomMCPServersResponse> {
  const response = await authFetch(`${API_URL}/api/custom-mcp`);
  if (!response.ok) {
    throw new Error("Failed to fetch custom MCP servers");
  }
  return response.json();
}

export async function setCustomMCPEnabled(serverId: string, enabled: boolean): Promise<CustomMCPServer> {
  const response = await authFetch(`${API_URL}/api/custom-mcp/${serverId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) {
    throw new Error("Failed to update custom MCP server");
  }
  return response.json();
}

export async function restartCustomMCPServer(serverId: string): Promise<CustomMCPServer> {
  const response = await authFetch(`${API_URL}/api/custom-mcp/${serverId}/restart`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error("Failed to restart custom MCP server");
  }
  return response.json();
}

export async function removeCustomMCPServer(serverId: string): Promise<{ status: string; server_id: string }> {
  const response = await authFetch(`${API_URL}/api/custom-mcp/${serverId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error("Failed to remove custom MCP server");
  }
  return response.json();
}

/** The server refused a message in words meant for the owner (409 — e.g. a reply
 *  to a journal entry Edward is still writing, 2026-09-25). The chat shows
 *  the sentence instead of the generic error. */
export class ChatRefusedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ChatRefusedError";
  }
}

/**
 * Stream chat with structured events (new format).
 * Yields StreamEvent objects for each event from the server.
 * @param message - The message to send
 * @param conversationId - Optional conversation ID for continuing a conversation
 * @param abortSignal - Optional AbortSignal for cancelling the request
 * @param files - Optional file attachments to include
 */
export async function* streamChatEvents(
  message: string,
  conversationId?: string,
  abortSignal?: AbortSignal,
  files?: File[],
): AsyncGenerator<StreamEvent, void, unknown> {
  let body: BodyInit;
  const headers: Record<string, string> = {};

  if (files && files.length > 0) {
    // Use FormData for file uploads — don't set Content-Type (browser adds boundary)
    const formData = new FormData();
    formData.append("message", message);
    if (conversationId) {
      formData.append("conversation_id", conversationId);
    }
    for (const file of files) {
      formData.append("files", file);
    }
    body = formData;
  } else {
    // Standard JSON request
    headers["Content-Type"] = "application/json";
    body = JSON.stringify({
      message,
      conversation_id: conversationId,
    });
  }

  const response = await fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers,
    credentials: "include",
    body,
    signal: abortSignal,
  });

  // Handle 401 by redirecting to login (clears the optimistic hint too)
  if (response.status === 401) {
    redirectToLogin();
    throw new Error("Authentication required");
  }

  if (!response.ok) {
    if (response.status === 409) {
      const detail = await response
        .json()
        .then((b: { detail?: unknown }) => (typeof b?.detail === "string" ? b.detail : ""))
        .catch(() => "");
      if (detail) throw new ChatRefusedError(detail);
    }
    throw new Error("Failed to send message");
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("No response body");
  }

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const data = JSON.parse(line.slice(6)) as StreamEvent;
          yield data;
        } catch {
          // Ignore parsing errors for non-JSON lines
        }
      }
    }
  }
}

/**
 * Stream chat with backwards-compatible format.
 * Converts new structured events to the legacy StreamChatChunk format.
 */
export async function* streamChat(message: string, conversationId?: string): AsyncGenerator<StreamChatChunk, void, unknown> {
  for await (const event of streamChatEvents(message, conversationId)) {
    // Convert structured events to legacy format
    if (event.type === "content") {
      yield {
        content: event.content,
        conversation_id: event.conversation_id,
        done: false,
      };
    } else if (event.type === "done") {
      yield {
        conversation_id: event.conversation_id,
        done: true,
      };
    }
    // Other event types are ignored in legacy mode
  }
}

// Voice API types
export interface VoiceStatus {
  enabled: boolean;
  status: "connected" | "error" | "disabled";
  status_message: string;
  metadata?: {
    voice_id?: string;
  };
}

export interface VoiceTranscriptResponse {
  text: string;
  language?: string;
}

// Voice API functions

/**
 * Get voice service status
 */
export async function getVoiceStatus(): Promise<VoiceStatus> {
  const response = await authFetch(`${API_URL}/api/voice/status`);
  if (!response.ok) {
    throw new Error("Failed to get voice status");
  }
  return response.json();
}

/**
 * Transcribe audio to text
 */
export async function transcribeAudio(
  audioBlob: Blob,
  language: string = "en",
  filename: string = "audio.webm"
): Promise<VoiceTranscriptResponse> {
  const formData = new FormData();
  formData.append("audio", audioBlob, filename);
  formData.append("language", language);

  const response = await authFetch(`${API_URL}/api/voice/transcribe`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Transcription failed" }));
    throw new Error(error.detail || "Failed to transcribe audio");
  }

  return response.json();
}

/**
 * Synthesize text to speech
 * Returns the audio as an ArrayBuffer
 */
export async function synthesizeSpeech(text: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  const formData = new FormData();
  formData.append("text", text);
  formData.append("stream", "false");

  const response = await authFetch(`${API_URL}/api/voice/synthesize`, {
    method: "POST",
    body: formData,
    signal,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Synthesis failed" }));
    throw new Error(error.detail || "Failed to synthesize speech");
  }

  return response.arrayBuffer();
}

// ===== Heartbeat API =====

export interface HeartbeatEvent {
  id: string;
  source: string;
  event_type: string;
  sender: string | null;
  contact_name: string | null;
  chat_identifier: string | null;
  chat_name: string | null;
  summary: string | null;
  raw_data: Record<string, unknown> | null;
  is_from_user: boolean;
  created_at: string;
  triage_status: string;
  briefed: boolean;
}

export interface TriageCycle {
  id: string;
  cycle_number: number;
  events_total: number;
  events_rule_filtered: number;
  events_dismissed: number;
  events_noted: number;
  events_acted: number;
  events_escalated: number;
  layer_reached: number;
  haiku_input_tokens: number;
  haiku_output_tokens: number;
  sonnet_wakes: number;
  duration_ms: number;
  summary: string | null;
  created_at: string;
}

export interface AllowedSender {
  identifier: string;
  label: string;
}

export interface TrackStatus {
  enabled: boolean;
  status: string;
  poll_seconds: number;
  lookahead_minutes?: number;
}

export interface HeartbeatStatus {
  running: boolean;
  enabled: boolean;
  triage_interval_seconds: number;
  pending_count: number;
  last_triage_at: string | null;
  next_triage_at: string | null;
  listener_status: string;
  allowed_senders: AllowedSender[];
  tracks: Record<string, TrackStatus>;
  imessage_enabled: boolean;
  imessage_poll_seconds: number;
  imessage_thread_limit_direct: number;
  imessage_thread_limit_group: number;
  calendar_enabled: boolean;
  calendar_poll_seconds: number;
  calendar_lookahead_minutes: number;
  email_enabled: boolean;
  email_poll_seconds: number;
  gmail_enabled: boolean;
  gmail_poll_seconds: number;
  /** Escalations go to Edward's mind for its next wake, not to a push. */
  mind_enabled?: boolean;
}

export interface HeartbeatConfig {
  enabled: boolean;
  triage_interval_seconds: number;
  digest_token_cap: number;
  allowed_senders: AllowedSender[];
  imessage_enabled: boolean;
  imessage_poll_seconds: number;
  imessage_thread_limit_direct: number;
  imessage_thread_limit_group: number;
  email_enabled: boolean;
  email_poll_seconds: number;
  gmail_enabled: boolean;
  gmail_poll_seconds: number;
}

export async function getHeartbeatStatus(): Promise<HeartbeatStatus> {
  const response = await authFetch(`${API_URL}/api/heartbeat/status`);
  if (!response.ok) {
    throw new Error("Failed to fetch heartbeat status");
  }
  return response.json();
}

export async function getHeartbeatEvents(
  limit = 50,
  offset = 0,
  triage_status?: string,
  source?: string,
): Promise<HeartbeatEvent[]> {
  const params = new URLSearchParams();
  params.set("limit", limit.toString());
  params.set("offset", offset.toString());
  if (triage_status) params.set("triage_status", triage_status);
  if (source) params.set("source", source);

  const response = await authFetch(`${API_URL}/api/heartbeat/events?${params}`);
  if (!response.ok) {
    throw new Error("Failed to fetch heartbeat events");
  }
  return response.json();
}

export async function getTriageCycles(limit = 20): Promise<TriageCycle[]> {
  const params = new URLSearchParams({ limit: limit.toString() });
  const response = await authFetch(`${API_URL}/api/heartbeat/triage?${params}`);
  if (!response.ok) {
    throw new Error("Failed to fetch triage cycles");
  }
  return response.json();
}

export async function updateHeartbeatConfig(
  config: Partial<HeartbeatConfig>,
): Promise<HeartbeatConfig> {
  const response = await authFetch(`${API_URL}/api/heartbeat/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!response.ok) {
    throw new Error("Failed to update heartbeat config");
  }
  return response.json();
}

export async function getLatestConversationTimestamp(): Promise<string | null> {
  try {
    const data = await getConversations(1, 0, true);
    if (data.conversations.length > 0) {
      return data.conversations[0].updated_at;
    }
    return null;
  } catch {
    return null;
  }
}

export async function getRecentSenders(limit = 50): Promise<AllowedSender[]> {
  const params = new URLSearchParams({ limit: limit.toString() });
  const response = await authFetch(`${API_URL}/api/heartbeat/recent-senders?${params}`);
  if (!response.ok) {
    throw new Error("Failed to fetch recent senders");
  }
  return response.json();
}

// ===== Gmail API =====

export interface GmailStatus {
  configured: boolean;
  email_address: string | null;
  token_valid: boolean;
}

export async function getGmailStatus(): Promise<GmailStatus> {
  const response = await authFetch(`${API_URL}/api/gmail/status`);
  if (!response.ok) throw new Error("Failed to fetch Gmail status");
  return response.json();
}

export async function getGmailOAuthUrl(): Promise<string> {
  const response = await authFetch(`${API_URL}/api/gmail/oauth/url`);
  if (!response.ok) throw new Error("Failed to get Gmail OAuth URL");
  const data = await response.json();
  return data.url;
}

export async function disconnectGmail(): Promise<void> {
  const response = await authFetch(`${API_URL}/api/gmail/disconnect`, {
    method: "POST",
  });
  if (!response.ok) throw new Error("Failed to disconnect Gmail");
}

// ===== Wishlist =====
//
// Features Edward wants the owner to build. He logs them from a chat (source
// "edward", with the conversation he wished in); the owner adds their own from the
// panel (source "owner") and moves them open → planned → building → done /
// declined. Replaced the self-evolution UI (Sep 2026). Pure logic and the
// item type live in lib/wishlist.ts.

export type { WishItem, WishPriority, WishStatus } from "./wishlist";

export async function getWishlist(status?: WishStatus): Promise<WishItem[]> {
  const qs = status ? `?${new URLSearchParams({ status })}` : "";
  const response = await authFetch(`${API_URL}/api/wishlist${qs}`);
  if (!response.ok) throw new Error("Failed to fetch the wishlist");
  const data = (await response.json()) as { items: WishItem[] };
  return data.items;
}

export async function createWish(body: {
  title: string;
  why: string;
  detail?: string;
  priority?: WishPriority;
}): Promise<WishItem> {
  const response = await authFetch(`${API_URL}/api/wishlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: "Create failed" }));
    throw new Error(data.detail || "Failed to add the wish");
  }
  return response.json();
}

export async function updateWish(
  id: string,
  patch: Partial<Pick<WishItem, "status" | "title" | "why" | "detail" | "priority">>,
): Promise<WishItem> {
  const response = await authFetch(`${API_URL}/api/wishlist/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: "Update failed" }));
    throw new Error(data.detail || "Failed to update the wish");
  }
  return response.json();
}

export async function deleteWish(id: string): Promise<{ deleted: boolean }> {
  const response = await authFetch(`${API_URL}/api/wishlist/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error("Failed to delete the wish");
  return response.json();
}

export type CodexUsageStatus = CodexUsagePayload;

export async function getCodexUsage(): Promise<CodexUsageStatus> {
  const response = await authFetch(`${API_URL}/api/settings/codex-usage`);
  if (!response.ok) {
    throw new Error("Failed to fetch Codex usage");
  }
  return response.json();
}

// ============================
// Orchestrator API
// ============================

export interface OrchestratorConfig {
  enabled: boolean;
  max_concurrent_workers: number;
  max_concurrent_cc_sessions: number;
  default_worker_model: string;
  default_worker_timeout: number;
}

export interface OrchestratorTask {
  id: string;
  parent_conversation_id: string;
  worker_conversation_id: string | null;
  task_description: string;
  task_type: string;
  model: string | null;
  cc_session_id: string | null;
  status: string;
  context_mode: string;
  result_summary: string | null;
  error: string | null;
  timeout_seconds: number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrchestratorStatus {
  config: OrchestratorConfig;
  active_count: number;
  recent_tasks: OrchestratorTask[];
}

export async function getOrchestratorStatus(): Promise<OrchestratorStatus> {
  const response = await authFetch(`${API_URL}/api/orchestrator/status`);
  if (!response.ok) throw new Error("Failed to fetch orchestrator status");
  return response.json();
}

export async function getOrchestratorTasks(params?: {
  parent_conversation_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<OrchestratorTask[]> {
  const searchParams = new URLSearchParams();
  if (params?.parent_conversation_id) searchParams.set("parent_conversation_id", params.parent_conversation_id);
  if (params?.status) searchParams.set("status", params.status);
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.offset) searchParams.set("offset", String(params.offset));
  const qs = searchParams.toString();
  const response = await authFetch(`${API_URL}/api/orchestrator/tasks${qs ? `?${qs}` : ""}`);
  if (!response.ok) throw new Error("Failed to fetch orchestrator tasks");
  return response.json();
}

export async function getOrchestratorTask(taskId: string): Promise<OrchestratorTask> {
  const response = await authFetch(`${API_URL}/api/orchestrator/tasks/${taskId}`);
  if (!response.ok) throw new Error("Failed to fetch orchestrator task");
  return response.json();
}

export async function cancelOrchestratorTask(taskId: string): Promise<OrchestratorTask> {
  const response = await authFetch(`${API_URL}/api/orchestrator/tasks/${taskId}/cancel`, {
    method: "POST",
  });
  if (!response.ok) throw new Error("Failed to cancel orchestrator task");
  return response.json();
}

export async function sendWorkerMessage(taskId: string, message: string): Promise<{ response: string }> {
  const response = await authFetch(`${API_URL}/api/orchestrator/tasks/${taskId}/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!response.ok) throw new Error("Failed to send worker message");
  return response.json();
}

export async function getOrchestratorConfig(): Promise<OrchestratorConfig> {
  const response = await authFetch(`${API_URL}/api/orchestrator/config`);
  if (!response.ok) throw new Error("Failed to fetch orchestrator config");
  return response.json();
}

export async function updateOrchestratorConfig(config: Partial<OrchestratorConfig>): Promise<OrchestratorConfig> {
  const response = await authFetch(`${API_URL}/api/orchestrator/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!response.ok) throw new Error("Failed to update orchestrator config");
  return response.json();
}

// CC Event streaming
export interface CCEvent {
  event_type: string;
  session_id?: string;
  text?: string;
  tool_name?: string;
  tool_input?: string;
  error?: string;
  status?: string;
  reason?: string;
  output_summary?: string;
}

export async function* streamTaskEvents(
  taskId: string,
  abortSignal?: AbortSignal,
): AsyncGenerator<CCEvent, void, unknown> {
  const response = await authFetch(`${API_URL}/api/orchestrator/tasks/${taskId}/events`, {
    signal: abortSignal,
  });

  if (!response.ok) return;

  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const data = JSON.parse(line.slice(6)) as CCEvent;
          yield data;
        } catch {
          // Ignore parse errors
        }
      }
    }
  }
}

// ===== Usage API =====

export interface UsageStats {
  input_tokens: number;
  output_tokens: number;
  requests: number;
}

export interface ModelUsage {
  lifetime: UsageStats;
  session: UsageStats;
}

export interface ProviderUsage {
  lifetime: UsageStats;
  session: UsageStats;
  by_model: Record<string, ProviderUsage>;
}

export interface UsageSummary {
  providers: Record<string, ProviderUsage>;
  session_start: string;
}

export async function getUsage(): Promise<UsageSummary> {
  const response = await authFetch(`${API_URL}/api/usage`);
  if (!response.ok) {
    throw new Error("Failed to fetch usage");
  }
  return response.json();
}

// ===== Proactive Intelligence API =====

export interface ProactiveConfig {
  // Phase 7: the five job toggles are gone with the jobs; the dossier
  // thresholds are the only proactive knobs left.
  dossier_enabled: boolean;
  dossier_min_acted_pushes: number;
  dossier_min_inbound_threads: number;
  dossier_window_days: number;
}

export interface ProactiveWake {
  kind: string;
  cron: string;
}

export interface ProactiveStatus {
  config: ProactiveConfig;
  entity_count: number;
  open_loop_count: number;
  wakes: ProactiveWake[];
}

export interface OpenLoop {
  id: string;
  description: string;
  source_memory_id: string | null;
  source_conversation_id: string | null;
  entity_id: string | null;
  status: string;
  priority: number;
  detected_at: string | null;
  last_nudged_at: string | null;
  resolved_at: string | null;
  snooze_until: string | null;
  nudge_count: number;
}

export interface EntityInfo {
  id: string;
  name: string;
  entity_type: string;
  aliases: string[];
  mention_count: number;
  first_seen: string | null;
  last_seen: string | null;
}

export async function getProactiveConfig(): Promise<ProactiveConfig> {
  const response = await authFetch(`${API_URL}/api/proactive/config`);
  if (!response.ok) throw new Error("Failed to fetch proactive config");
  return response.json();
}

export async function updateProactiveConfig(updates: Partial<ProactiveConfig>): Promise<ProactiveConfig> {
  const response = await authFetch(`${API_URL}/api/proactive/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!response.ok) throw new Error("Failed to update proactive config");
  return response.json();
}

export async function getProactiveStatus(): Promise<ProactiveStatus> {
  const response = await authFetch(`${API_URL}/api/proactive/status`);
  if (!response.ok) throw new Error("Failed to fetch proactive status");
  return response.json();
}

export async function getOpenLoops(status?: string): Promise<OpenLoop[]> {
  const params = status ? `?status=${status}` : "";
  const response = await authFetch(`${API_URL}/api/proactive/open-loops${params}`);
  if (!response.ok) throw new Error("Failed to fetch open loops");
  return response.json();
}

export async function updateOpenLoop(id: string, update: { status: string; snooze_until?: string }): Promise<OpenLoop> {
  const response = await authFetch(`${API_URL}/api/proactive/open-loops/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  if (!response.ok) throw new Error("Failed to update open loop");
  return response.json();
}

export async function getEntities(entityType?: string): Promise<EntityInfo[]> {
  const params = entityType ? `?entity_type=${entityType}` : "";
  const response = await authFetch(`${API_URL}/api/proactive/entities${params}`);
  if (!response.ok) throw new Error("Failed to fetch entities");
  return response.json();
}

// ===== Dreaming (consolidation) API =====

export interface ConsolidationStatus {
  running: boolean;
  enabled: boolean;
  interval_seconds: number;
  lookback_hours: number;
  last_run: string | null;
  next_run: string | null;
  cycle_count: number;
  total_connections: number;
  total_flags: number;
  max_cluster_size: number;
  max_pairs_per_cluster: number;
  max_llm_calls_per_cycle: number;
}

export interface MemoryFlag {
  id: string;
  memory_id: string;
  flag_type: string;
  description: string;
  related_memory_id: string | null;
  resolved: boolean;
  resolved_at: string | null;
  created_at: string;
  /** Present when fetched with content, so a contradiction can be judged in place. */
  memory_content?: string | null;
  related_memory_content?: string | null;
}

export interface MemoryConnection {
  id: string;
  memory_id_a: string;
  memory_id_b: string;
  connection_type: string;
  strength: number;
  created_at: string;
  /** Both memories' text, when the server attaches it (`with_content`). */
  memory_a_content?: string | null;
  memory_b_content?: string | null;
}

export interface ConsolidationConfigUpdate {
  enabled?: boolean;
  interval_seconds?: number;
  lookback_hours?: number;
  max_cluster_size?: number;
  max_pairs_per_cluster?: number;
  max_llm_calls_per_cycle?: number;
}

export async function getDreamingStatus(): Promise<ConsolidationStatus> {
  const response = await authFetch(`${API_URL}/api/consolidation/status`);
  if (!response.ok) throw new Error("Failed to fetch dreaming status");
  return response.json();
}

export async function updateDreamingConfig(
  body: ConsolidationConfigUpdate,
): Promise<ConsolidationStatus> {
  const response = await authFetch(`${API_URL}/api/consolidation/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error("Failed to update dreaming config");
  return response.json();
}

export async function triggerDreamingCycle(): Promise<void> {
  const response = await authFetch(`${API_URL}/api/consolidation/trigger`, {
    method: "POST",
  });
  if (!response.ok) throw new Error("Failed to trigger dreaming cycle");
}

export async function getDreamingFlags(resolved?: boolean, limit = 50, withContent = false): Promise<MemoryFlag[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (resolved !== undefined) params.set("resolved", String(resolved));
  if (withContent) params.set("with_content", "true");
  const response = await authFetch(`${API_URL}/api/consolidation/flags?${params}`);
  if (!response.ok) throw new Error("Failed to fetch dreaming flags");
  return response.json();
}

/** One flag with both memories' text (GET /api/consolidation/flags/{id},
 *  2026-09-26); null when the server has no such flag (404). An older
 *  backend without the route throws, and the caller searches the list. */
export async function getDreamingFlag(id: string): Promise<MemoryFlag | null> {
  const response = await authFetch(`${API_URL}/api/consolidation/flags/${encodeURIComponent(id)}`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Failed to fetch the flag (${response.status})`);
  const flag = (await response.json()) as MemoryFlag;
  if (!flag || typeof flag !== "object" || (flag as { id?: unknown }).id !== id) throw new Error("Not a flag");
  return flag;
}

/** The owner's call on an open contradiction: keep side "a" (memory_id) or "b" (related_memory_id); the other is retired. */
export async function keepDreamingFlagSide(id: string, keep: "a" | "b"): Promise<MemoryFlag> {
  const response = await authFetch(`${API_URL}/api/consolidation/flags/${id}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keep }),
  });
  if (!response.ok) throw new Error("Failed to resolve contradiction");
  return response.json();
}

export async function resolveDreamingFlag(id: string, resolved: boolean): Promise<MemoryFlag> {
  const response = await authFetch(`${API_URL}/api/consolidation/flags/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resolved }),
  });
  if (!response.ok) throw new Error("Failed to resolve flag");
  return response.json();
}

export async function getDreamingConnections(limit = 50, withContent = false): Promise<MemoryConnection[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (withContent) params.set("with_content", "true");
  const response = await authFetch(`${API_URL}/api/consolidation/connections?${params}`);
  if (!response.ok) throw new Error("Failed to fetch dreaming connections");
  return response.json();
}

// ---------------------------------------------------------------------------
// Household hub — screensaver album
// ---------------------------------------------------------------------------
//
// The album is a plain directory on the host Mac (EDWARD_PHOTOS_DIR). Until
// now the only way in was SSH, which meant one person in the house could
// add a photo. These wrap the upload/delete half of /api/hub/photos so the
// settings PWA can do it from a phone.

export interface HubPhoto {
  name: string;
}

export interface HubPhotosResponse {
  photos: string[];
  skipped_unsupported: number;
  dir: string;
  dir_present: boolean;
}

export interface HubUploadResult {
  ok: boolean;
  name: string;
  original?: string;
  converted?: boolean;
  reason?: string;
}

export interface HubUploadResponse {
  saved: HubUploadResult[];
  rejected: HubUploadResult[];
  saved_count: number;
  converted_count: number;
}

export async function listHubPhotos(): Promise<HubPhotosResponse> {
  const response = await authFetch(`${API_URL}/api/hub/photos`);
  if (!response.ok) throw new Error("Failed to load hub photos");
  return response.json();
}

/** One file per request: a phone on hotel wifi uploading twenty photos in
 *  a single 60MB POST is one dropped packet away from losing all twenty. */
export async function uploadHubPhoto(file: File): Promise<HubUploadResponse> {
  const form = new FormData();
  form.append("files", file);
  // No Content-Type header — the browser must set the multipart boundary.
  const response = await authFetch(`${API_URL}/api/hub/photos`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw new Error(`Upload failed (${response.status})`);
  return response.json();
}

export async function deleteHubPhoto(name: string): Promise<void> {
  const response = await authFetch(
    `${API_URL}/api/hub/photos/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw new Error("Failed to delete photo");
}

export function hubPhotoUrl(name: string): string {
  return `${API_URL}/api/hub/photos/${encodeURIComponent(name)}`;
}

// Today feed ---------------------------------------------------------------
import type { TodayFeed, TodayAction } from "@/components/today/todayModel";
export type { TodayFeed, TodayAction };

/** How long Today waits for its feed before calling the load failed (the
 *  page then says so, rather than leaving a stale feed up and tappable). */
export const TODAY_TIMEOUT_MS = 15_000;

export async function getToday(opts: { timeoutMs?: number } = {}): Promise<TodayFeed> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? TODAY_TIMEOUT_MS);
  try {
    const response = await authFetch(`${API_URL}/api/today`, { signal: controller.signal });
    if (!response.ok) throw new Error("Failed to load Today");
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

/** Run one of the actions the backend attached to a Today need. GET
 *  actions are navigations and never come here. */
/** Returns the server's JSON answer (null when there is none): a held
 *  send's approve answers `{status, result_text, delivered}`, and only
 *  `delivered` says it went (review 2026-09-26). */
export async function runTodayAction(action: TodayAction): Promise<unknown> {
  const response = await authFetch(`${API_URL}${action.url}`, {
    method: action.method,
    headers: action.body ? { "Content-Type": "application/json" } : undefined,
    body: action.body ? JSON.stringify(action.body) : undefined,
  });
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.json()).detail ?? ""; } catch { /* ignore */ }
    throw new Error(detail || `${action.label} failed (${response.status})`);
  }
  try {
    return await response.json();
  } catch {
    return null;
  }
}

// Signals (the owner's implicit verdicts) -------------------------------------
import type { Signal } from "@/lib/signals";

/** Fire-and-forget: a signal must never cost the page anything. */
export function postSignal(signal: Signal): void {
  try {
    void authFetch(`${API_URL}/api/signals`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(signal),
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    /* ignore */
  }
}

// Heartbeat feedback ---------------------------------------------------------
export interface HeartbeatFeedback {
  id: string;
  event_id: string;
  verdict: string;
  triage_status: string | null;
  contact_key: string | null;
  note: string | null;
  created_at: string | null;
}

export async function sendHeartbeatFeedback(eventId: string, verdict: string): Promise<HeartbeatFeedback> {
  const response = await authFetch(`${API_URL}/api/heartbeat/events/${eventId}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ verdict }),
  });
  if (!response.ok) throw new Error("Failed to record feedback");
  return response.json();
}

export async function getHeartbeatFeedback(limit = 200): Promise<HeartbeatFeedback[]> {
  const response = await authFetch(`${API_URL}/api/heartbeat/feedback?limit=${limit}`);
  if (!response.ok) throw new Error("Failed to load feedback");
  return response.json();
}


/**
 * Opening a conversation answers any push that pointed at it: the interrupt
 * ladder stops escalating and the ledger marks the push opened. Fire and
 * forget — never blocks the load.
 */
export function ackConversationOpened(conversationId: string): void {
  try {
    void fetch(`${API_URL}/api/push/ack`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: conversationId, source: "opened" }),
      keepalive: true,
    }).catch(() => {});
  } catch {
    /* never let an ack break the chat */
  }
}

// ---- The mind (Settings → Mind → Thoughts; mind rebuild Phase 11) ----

export async function getMindLog(days = 7): Promise<{ days: number; wakes: MindWake[] }> {
  const response = await authFetch(`${API_URL}/api/mind/log?days=${days}`);
  if (!response.ok) throw new Error("Failed to fetch the mind's log");
  return response.json();
}

export async function getMindIntentions(): Promise<{ intentions: MindIntention[] }> {
  const response = await authFetch(`${API_URL}/api/mind/intentions`);
  if (!response.ok) throw new Error("Failed to fetch intentions");
  return response.json();
}

export async function postMindVerdict(ledgerId: number, verdict: "right_call" | "wrong_call", note?: string) {
  const response = await authFetch(`${API_URL}/api/mind/verdict`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ledger_id: ledgerId, verdict, note: note ?? null }),
  });
  if (!response.ok) throw new Error("Failed to record the verdict");
  return response.json();
}

/** POST /api/mind/wake answers 202 at once (2026-09-25): the wake runs in
 *  the background (`waking` in /api/mind/now meanwhile) and its row lands
 *  in /api/mind/log when it is done. `joined` = a wake was already running
 *  and this press joined it. */
export async function wakeMind(reason = "manual"): Promise<{ accepted: boolean; reason: string; joined: boolean }> {
  const response = await authFetch(`${API_URL}/api/mind/wake`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!response.ok) throw new Error("I couldn't start a wake just now.");
  const body = await response.json().catch(() => ({}));
  return {
    accepted: body?.accepted !== false,
    reason: typeof body?.reason === "string" ? body.reason : reason,
    joined: Boolean(body?.joined),
  };
}
