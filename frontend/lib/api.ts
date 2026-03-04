// Use relative URLs in browser (works for any domain), absolute for SSR
const API_URL = typeof window !== "undefined"
  ? ""
  : (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000");

// Auth-aware fetch wrapper that includes credentials and handles 401
async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const response = await fetch(url, {
    ...options,
    credentials: "include",
  });

  // On 401, redirect to login (only in browser context, and not already on login page)
  if (response.status === 401 && typeof window !== "undefined") {
    if (window.location.pathname !== "/login") {
      window.location.href = "/login";
    }
    throw new Error("Authentication required");
  }

  return response;
}

export interface Settings {
  name: string;
  personality: string;
  model: string;
  temperature: number;
  system_prompt: string;
}

export interface Model {
  id: string;
  name: string;
}

// Auth API types
export interface AuthStatus {
  configured: boolean;
  authenticated: boolean;
}

// Auth API functions
export async function getAuthStatus(): Promise<AuthStatus> {
  const response = await fetch(`${API_URL}/api/auth/status`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error("Failed to fetch auth status");
  }
  return response.json();
}

export async function setupPassword(password: string): Promise<void> {
  const response = await fetch(`${API_URL}/api/auth/setup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ password }),
  });
  if (!response.ok) {
    const data = await response.json();
    throw new Error(data.detail || "Failed to setup password");
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
    const data = await response.json();
    throw new Error(data.detail || "Login failed");
  }
}

export async function logout(): Promise<void> {
  await fetch(`${API_URL}/api/auth/logout`, {
    method: "POST",
    credentials: "include",
  });
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
    return "Hey there! Good to see you.";
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

export async function getModels(): Promise<Model[]> {
  const response = await authFetch(`${API_URL}/api/settings/models`);
  if (!response.ok) {
    throw new Error("Failed to fetch models");
  }
  const data = await response.json();
  return data.models;
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
export type ConversationSource = "user" | "scheduled_event" | "external_message" | "heartbeat" | "orchestrator_worker";
export type ConversationChannel = "text" | "voice";

export interface Conversation {
  id: string;
  title: string;
  source: ConversationSource;
  channel: ConversationChannel;
  notified_user: boolean;  // True if Edward tried to get user's attention
  created_at: string;
  updated_at: string;
  message_count: number;
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
  search?: string
): Promise<ConversationsResponse> {
  const params = new URLSearchParams({
    limit: limit.toString(),
    offset: offset.toString(),
    include_scheduled: includeScheduled.toString(),
  });
  if (search) {
    params.set("search", search);
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
  const response = await authFetch(`${API_URL}/api/conversations/${conversationId}`, {
    method: "DELETE",
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
  | "cc_session_end";

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
  code?: string;           // for code
  language?: string;       // for code
  output?: string;         // for execution_output
  stream?: "stdout" | "stderr";  // for execution_output
  success?: boolean;       // for execution_result
  duration_ms?: number;    // for execution_result
  result?: string;         // for tool_end (truncated result)
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

  // Handle 401 by redirecting to login
  if (response.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login";
    throw new Error("Authentication required");
  }

  if (!response.ok) {
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
  calendar_enabled: boolean;
  calendar_poll_seconds: number;
  calendar_lookahead_minutes: number;
  email_enabled: boolean;
  email_poll_seconds: number;
  whatsapp_enabled: boolean;
  whatsapp_poll_seconds: number;
}

export interface HeartbeatConfig {
  enabled: boolean;
  triage_interval_seconds: number;
  digest_token_cap: number;
  allowed_senders: AllowedSender[];
  imessage_enabled: boolean;
  imessage_poll_seconds: number;
  calendar_enabled: boolean;
  calendar_poll_seconds: number;
  calendar_lookahead_minutes: number;
  email_enabled: boolean;
  email_poll_seconds: number;
  whatsapp_enabled: boolean;
  whatsapp_poll_seconds: number;
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

// ===== Evolution API =====

export interface EvolutionConfig {
  enabled: boolean;
  min_interval_seconds: number;
  auto_trigger: boolean;
  require_tests: boolean;
  max_files_per_cycle: number;
}

export interface EvolutionCycle {
  id: string;
  trigger: string;
  description: string;
  branch_name: string | null;
  status: string;
  step: string | null;
  files_changed: string[];
  test_output: string | null;
  review_summary: string | null;
  error: string | null;
  rollback_tag: string | null;
  cc_session_id: string | null;
  duration_ms: number | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string | null;
}

export interface EvolutionStatus {
  config: EvolutionConfig;
  current_cycle: EvolutionCycle | null;
  last_cycle_at: string | null;
}

export async function getEvolutionStatus(): Promise<EvolutionStatus> {
  const response = await authFetch(`${API_URL}/api/evolution/status`);
  if (!response.ok) {
    throw new Error("Failed to fetch evolution status");
  }
  return response.json();
}

export async function getEvolutionHistory(
  limit = 20,
  offset = 0,
): Promise<EvolutionCycle[]> {
  const params = new URLSearchParams({
    limit: limit.toString(),
    offset: offset.toString(),
  });
  const response = await authFetch(`${API_URL}/api/evolution/history?${params}`);
  if (!response.ok) {
    throw new Error("Failed to fetch evolution history");
  }
  return response.json();
}

export async function updateEvolutionConfig(
  config: Partial<EvolutionConfig>,
): Promise<EvolutionConfig> {
  const response = await authFetch(`${API_URL}/api/evolution/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!response.ok) {
    throw new Error("Failed to update evolution config");
  }
  return response.json();
}

export async function triggerEvolution(
  description: string,
  trigger = "manual",
): Promise<{ status: string; description: string }> {
  const response = await authFetch(`${API_URL}/api/evolution/trigger`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ description, trigger }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: "Trigger failed" }));
    throw new Error(data.detail || "Failed to trigger evolution");
  }
  return response.json();
}

export async function rollbackEvolution(
  cycleId: string,
): Promise<{ status: string; message: string }> {
  const response = await authFetch(`${API_URL}/api/evolution/rollback/${cycleId}`, {
    method: "POST",
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: "Rollback failed" }));
    throw new Error(data.detail || "Failed to rollback evolution");
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
