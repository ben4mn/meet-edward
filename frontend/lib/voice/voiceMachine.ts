// Browser-side voice state machine. Server messages and local audio
// events go in, a view state comes out. The hook wires this to a
// WebSocket, a mic, and a PCM player; this file knows about none of them.

import type { VoiceServerMessage } from "./voiceProtocol";

export type VoicePhase = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "error";

export interface VoiceToolActivity {
  tool_name: string;
  status: "started" | "completed" | "error";
  message: string;
}

/** One back-and-forth for the view's rolling transcript. */
export interface VoiceExchange {
  /** What the owner said (empty for something Edward says unprompted: a handoff report). */
  you: string;
  edward: string;
  /** The owner talked over him (or pressed Stop) before he finished. */
  cut?: boolean;
}

/** The rolling transcript keeps this many exchanges. */
export const MAX_EXCHANGES = 3;

export interface VoiceViewState {
  phase: VoicePhase;
  conversationId: string | null;
  mode: "auto" | "ptt";
  inputSampleRate: number;
  outputSampleRate: number;
  /** Partial transcript while the user is still talking. */
  liveCaption: string;
  /** Last committed transcript. */
  lastTranscript: string;
  /** Spoken filler line while the model thinks. */
  bridge: string;
  /** Edward's sentences for the current turn. */
  subtitles: string[];
  toolActivity: VoiceToolActivity[];
  /** Server says the turn is over, but audio may still be queued locally. */
  awaitingPlaybackEnd: boolean;
  /** Bumped whenever local playback must stop right now (barge-in, interrupt). */
  stopPlaybackSeq: number;
  errorMessage: string | null;
  pttHeld: boolean;
  /** The socket dropped under us (phone screen went dark, network blip); safe to reconnect. */
  lostConnection: boolean;
  /** A tool-heavy turn was handed off and is still running (its handoff line), or null. */
  backgroundWork: string | null;
  /** Handed-off jobs this session reported done (handoff_done) — the "acted" beat reads this. */
  handoffsDone: number;
  /** The last few exchanges, oldest first; kept across turns and reconnects. */
  exchanges: VoiceExchange[];
  /** Edward's reply is still arriving into the last exchange. */
  exchangeOpen: boolean;
}

export type VoiceEvent =
  | { type: "connect" }
  /** `keep`: a reconnect (mode or mic sensitivity changed) — the conversation
   *  and its rolling transcript stay; only ending the session clears them. */
  | { type: "disconnect"; keep?: boolean }
  | { type: "server"; msg: VoiceServerMessage }
  | { type: "socket_closed"; clean: boolean }
  | { type: "playback_drained" }
  | { type: "ptt_press" }
  | { type: "ptt_release" };

export function initialVoiceState(): VoiceViewState {
  return {
    phase: "idle",
    conversationId: null,
    mode: "auto",
    inputSampleRate: 16000,
    outputSampleRate: 24000,
    liveCaption: "",
    lastTranscript: "",
    bridge: "",
    subtitles: [],
    toolActivity: [],
    awaitingPlaybackEnd: false,
    stopPlaybackSeq: 0,
    errorMessage: null,
    pttHeld: false,
    lostConnection: false,
    backgroundWork: null,
    handoffsDone: 0,
    exchanges: [],
    exchangeOpen: false,
  };
}

function capped(list: VoiceExchange[]): VoiceExchange[] {
  return list.length > MAX_EXCHANGES ? list.slice(list.length - MAX_EXCHANGES) : list;
}

function joinSpoken(prev: string, next: string): string {
  if (!prev || /\s$/.test(prev) || !next) return prev + next;
  return `${prev} ${next}`;
}

/** The owner said something: a new exchange opens for Edward's reply. */
function heard(s: VoiceViewState, text: string): VoiceViewState {
  return { ...s, exchanges: capped([...s.exchanges, { you: text, edward: "" }]), exchangeOpen: true };
}

/** A piece of Edward's reply: into the open exchange, or one of his own. */
function replied(s: VoiceViewState, text: string): VoiceViewState {
  if (!text) return s;
  const last = s.exchanges[s.exchanges.length - 1];
  if (s.exchangeOpen && last) {
    return { ...s, exchanges: [...s.exchanges.slice(0, -1), { ...last, edward: joinSpoken(last.edward, text) }] };
  }
  return { ...s, exchanges: capped([...s.exchanges, { you: "", edward: text }]), exchangeOpen: true };
}

/** Talked over or stopped: what was said stays, marked as cut off. */
function cutOff(s: VoiceViewState): VoiceViewState {
  const last = s.exchanges[s.exchanges.length - 1];
  if (!s.exchangeOpen || !last) return { ...s, exchangeOpen: false };
  const next = last.edward ? { ...last, cut: true } : last;
  return { ...s, exchanges: [...s.exchanges.slice(0, -1), next], exchangeOpen: false };
}

function clearTurn(s: VoiceViewState): VoiceViewState {
  return { ...s, bridge: "", subtitles: [], toolActivity: [], awaitingPlaybackEnd: false };
}

export function reduceVoice(s: VoiceViewState, e: VoiceEvent): VoiceViewState {
  switch (e.type) {
    case "connect":
      return {
        ...initialVoiceState(),
        conversationId: s.conversationId,
        mode: s.mode,
        phase: "connecting",
        exchanges: s.exchanges,
        handoffsDone: s.handoffsDone,
      };

    case "disconnect":
      if (!e.keep) return initialVoiceState();
      return { ...initialVoiceState(), conversationId: s.conversationId, mode: s.mode, exchanges: s.exchanges, handoffsDone: s.handoffsDone };

    case "socket_closed":
      if (s.phase === "idle" || s.phase === "error") return s;
      return e.clean
        ? { ...clearTurn(s), phase: "idle", liveCaption: "", pttHeld: false }
        : { ...clearTurn(s), phase: "error", errorMessage: "Connection lost", pttHeld: false, lostConnection: true };

    case "playback_drained":
      return { ...s, subtitles: [], toolActivity: [], awaitingPlaybackEnd: false };

    case "ptt_press":
      return { ...s, pttHeld: true };

    case "ptt_release":
      return { ...s, pttHeld: false };

    case "server":
      return reduceServer(s, e.msg);
  }
  return s;
}

function reduceServer(s: VoiceViewState, msg: VoiceServerMessage): VoiceViewState {
  switch (msg.type) {
    case "session_started":
      return {
        ...s,
        phase: "listening",
        conversationId: msg.conversation_id,
        mode: msg.mode === "ptt" ? "ptt" : "auto",
        inputSampleRate: msg.sample_rate_in ?? s.inputSampleRate,
        outputSampleRate: msg.sample_rate_out ?? s.outputSampleRate,
        errorMessage: null,
      };

    case "partial_transcript":
      return { ...s, liveCaption: msg.text };

    case "transcript":
      return heard({ ...s, liveCaption: "", lastTranscript: msg.text }, msg.text);

    case "state": {
      const interrupted = msg.reason === "barge_in" || msg.reason === "interrupt";
      if (interrupted) {
        return cutOff({ ...clearTurn(s), phase: msg.state, stopPlaybackSeq: s.stopPlaybackSeq + 1 });
      }
      if (msg.state === "thinking") {
        return { ...clearTurn(s), phase: "thinking" };
      }
      return { ...s, phase: msg.state };
    }

    case "bridge":
      return { ...s, bridge: msg.text };

    case "response_text":
      return replied({ ...s, subtitles: [...s.subtitles, msg.text] }, msg.text);

    case "tool_activity":
      return {
        ...s,
        toolActivity: [...s.toolActivity, { tool_name: msg.tool_name, status: msg.status, message: msg.message }],
      };

    case "response_done":
      return { ...s, bridge: "", awaitingPlaybackEnd: true, exchangeOpen: false };

    case "handoff":
      return { ...s, backgroundWork: msg.text || "Working on it in the background." };

    case "handoff_done":
      return { ...s, backgroundWork: null, handoffsDone: s.handoffsDone + 1 };

    case "error":
      return cutOff({ ...s, phase: "error", errorMessage: msg.message || "Voice error", pttHeld: false, lostConnection: false });

    case "pong":
      return s;
  }
  return s;
}
