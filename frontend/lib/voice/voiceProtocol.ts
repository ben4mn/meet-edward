// Frames on the voice socket: the session's opening message (client ->
// server) and what comes back (server -> client).

import { PREFIX_TTS_AUDIO } from "./pcm";

/** Who is speaking (2026-10-11). "app" is the chat page's live voice — the owner.
 *  "hub" is the household hub's /hub/ask — whoever is at the panel, not
 *  necessarily the owner; the backend runs those turns as `hub_voice`, without
 *  the owner's rights (texting anyone from their number, typing in their Chrome). */
export type VoiceSurface = "app" | "hub";

export interface StartSessionOptions {
  mode: "auto" | "ptt";
  sensitivity: string;
  surface?: VoiceSurface;
  conversationId?: string;
}

/** The one place a `start_session` message is built. */
export function startSessionMessage({ mode, sensitivity, surface = "app", conversationId }: StartSessionOptions) {
  const start: Record<string, unknown> = { type: "start_session", mode, sensitivity, surface };
  if (conversationId) start.conversation_id = conversationId;
  return start;
}

export type VoiceServerMessage =
  | { type: "session_started"; conversation_id: string; mode: "auto" | "ptt"; sensitivity?: string; surface?: VoiceSurface; sample_rate_in?: number; sample_rate_out?: number }
  | { type: "partial_transcript"; text: string }
  | { type: "transcript"; text: string }
  | { type: "state"; state: "listening" | "thinking" | "speaking"; reason?: string }
  | { type: "bridge"; text: string }
  | { type: "response_text"; text: string; is_final: boolean }
  | { type: "tool_activity"; tool_name: string; status: "started" | "completed" | "error"; message: string }
  | { type: "response_done" }
  | { type: "handoff"; text: string; conversation_id: string }
  | { type: "handoff_done"; outcome: string; conversation_id: string }
  | { type: "error"; message: string; code?: string }
  | { type: "pong" };

export type ParsedFrame =
  | { kind: "audio"; pcm: ArrayBuffer }
  | { kind: "json"; msg: VoiceServerMessage };

export function parseServerFrame(data: ArrayBuffer | string): ParsedFrame | null {
  if (typeof data === "string") {
    try {
      const msg = JSON.parse(data);
      if (msg && typeof msg === "object" && typeof msg.type === "string") {
        return { kind: "json", msg: msg as VoiceServerMessage };
      }
    } catch {
      /* fall through */
    }
    return null;
  }
  const bytes = new Uint8Array(data);
  if (bytes.length < 2 || bytes[0] !== PREFIX_TTS_AUDIO) return null;
  return { kind: "audio", pcm: data.slice(1) };
}
