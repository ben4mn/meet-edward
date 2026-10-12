"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";

import { unlockAudio } from "@/lib/voice/audioContext";
import { PREFIX_MIC_AUDIO, prefixFrame } from "@/lib/voice/pcm";
import { initialVoiceState, reduceVoice, type VoiceViewState } from "@/lib/voice/voiceMachine";
import { parseServerFrame, startSessionMessage, type VoiceSurface } from "@/lib/voice/voiceProtocol";
import { createNoiseGate, gateFrame, type NoiseGate, type Sensitivity } from "@/lib/voice/noiseGate";
import { useMicStream } from "./useMicStream";
import { usePcmPlayer } from "./usePcmPlayer";

export type VoiceMode = "auto" | "ptt";

export interface UseVoiceSessionReturn {
  state: VoiceViewState;
  connect: (conversationId?: string, mode?: VoiceMode, sensitivity?: Sensitivity) => void;
  disconnect: (opts?: { keep?: boolean }) => void;
  /** Cut Edward off (also happens automatically when you start talking). */
  interrupt: () => void;
  pttPress: () => void;
  pttRelease: () => void;
  /** Mic RMS 0..1 for the level ring; read inside an animation frame. */
  getMicLevel: () => number;
  /** Edward's voice level 0..1 for his mouth; read inside an animation frame. */
  getOutputLevel: () => number;
  /** iOS keeps audio muted until a tap; call this from one. */
  unlock: () => Promise<boolean>;
}

const PING_INTERVAL_MS = 15000;

function buildWsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  let host = window.location.host;
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  if (apiUrl) {
    try {
      host = new URL(apiUrl).host;
    } catch {
      /* keep window host */
    }
  }
  return `${protocol}//${host}/api/voice/ws`;
}

/**
 * The live voice session: one WebSocket, the mic streaming into it the
 * whole time, PCM coming back out. All decisions live in the reducer in
 * lib/voice/voiceMachine.ts; this hook only moves bytes.
 *
 * `surface` says who is speaking, on every start_session (resumes too):
 * "app" is the owner, "hub" the household hub's panel (lib/voice/voiceProtocol.ts).
 */
export function useVoiceSession(surface: VoiceSurface = "app"): UseVoiceSessionReturn {
  const [state, dispatch] = useReducer(reduceVoice, undefined, initialVoiceState);
  const stateRef = useRef(state);
  stateRef.current = state;
  const surfaceRef = useRef(surface);
  surfaceRef.current = surface;

  const wsRef = useRef<WebSocket | null>(null);
  const pingRef = useRef<number | null>(null);
  /** What connect() was last asked for, so a resume can repeat it. */
  const lastConnectRef = useRef<{ conversationId?: string; mode: VoiceMode; sensitivity: Sensitivity }>({
    mode: "auto",
    sensitivity: "normal",
  });
  /** Noise gate in front of the socket; rebuilt per connect() from the sensitivity. */
  const gateRef = useRef<NoiseGate>(createNoiseGate("normal"));

  const player = usePcmPlayer({
    onDrained: () => dispatch({ type: "playback_drained" }),
  });

  const sendJson = useCallback((data: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify(data));
      } catch {
        /* closing */
      }
    }
  }, []);

  const mic = useMicStream({
    onFrame: (pcm, rms) => {
      const ws = wsRef.current;
      const s = stateRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (s.phase === "idle" || s.phase === "connecting" || s.phase === "error") return;
      if (s.mode === "ptt" && !s.pttHeld) return;
      // Hands-free only: holding the button is already a deliberate act.
      // A gated frame still goes out, as silence, so Scribe's clock runs
      // on and its silence timer can commit what came before.
      const frameMs = (pcm.byteLength / 2 / 16000) * 1000;
      const open = s.mode === "ptt" || gateFrame(gateRef.current, rms, frameMs);
      const bytes = open ? new Uint8Array(pcm) : new Uint8Array(pcm.byteLength);
      ws.send(prefixFrame(PREFIX_MIC_AUDIO, bytes));
    },
    onError: (err) => {
      dispatch({ type: "server", msg: { type: "error", message: err.message } });
    },
  });

  // Barge-in / interrupt: the reducer bumps a counter, we cut audio.
  const lastStopSeq = useRef(0);
  useEffect(() => {
    if (state.stopPlaybackSeq !== lastStopSeq.current) {
      lastStopSeq.current = state.stopPlaybackSeq;
      player.stop();
    }
  }, [state.stopPlaybackSeq, player]);

  const clearPing = () => {
    if (pingRef.current !== null) {
      window.clearInterval(pingRef.current);
      pingRef.current = null;
    }
  };

  /** `keep`: a reconnect — the conversation and its transcript stay on screen. */
  const disconnect = useCallback((opts: { keep?: boolean } = {}) => {
    clearPing();
    mic.stop();
    player.stop();
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      if (ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ type: "end_session" }));
        } catch {
          /* closing */
        }
      }
      ws.onclose = null;
      ws.close();
    }
    dispatch({ type: "disconnect", keep: Boolean(opts.keep) });
  }, [mic, player]);

  const connect = useCallback(
    (conversationId?: string, mode: VoiceMode = "auto", sensitivity: Sensitivity = "normal") => {
      const existing = wsRef.current;
      if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
        return;
      }
      lastConnectRef.current = { conversationId, mode, sensitivity };
      gateRef.current = createNoiseGate(sensitivity);
      void unlockAudio();
      dispatch({ type: "connect" });

      const ws = new WebSocket(buildWsUrl());
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        ws.send(JSON.stringify(startSessionMessage({ mode, sensitivity, surface: surfaceRef.current, conversationId })));
        clearPing();
        pingRef.current = window.setInterval(() => sendJson({ type: "ping" }), PING_INTERVAL_MS);
      };

      ws.onmessage = (event: MessageEvent) => {
        const handle = (data: ArrayBuffer | string) => {
          const frame = parseServerFrame(data);
          if (!frame) return;
          if (frame.kind === "audio") {
            player.enqueue(frame.pcm, stateRef.current.outputSampleRate);
            return;
          }
          dispatch({ type: "server", msg: frame.msg });
          if (frame.msg.type === "session_started") {
            void mic.start();
          } else if (frame.msg.type === "response_done") {
            player.finalize();
          }
        };
        if (event.data instanceof Blob) {
          event.data.arrayBuffer().then(handle);
        } else {
          handle(event.data);
        }
      };

      ws.onclose = (event) => {
        clearPing();
        mic.stop();
        if (wsRef.current === ws) wsRef.current = null;
        dispatch({ type: "socket_closed", clean: event.wasClean });
      };

      ws.onerror = () => {
        // onclose follows with wasClean=false.
      };
    },
    [mic, player, sendJson],
  );

  const interrupt = useCallback(() => {
    player.stop();
    sendJson({ type: "interrupt" });
  }, [player, sendJson]);

  const pttPress = useCallback(() => {
    dispatch({ type: "ptt_press" });
  }, []);

  const pttRelease = useCallback(() => {
    dispatch({ type: "ptt_release" });
    sendJson({ type: "commit" });
  }, [sendJson]);

  // Phone screen went dark and came back: iOS ends the mic track and
  // usually drops the socket. Pick the conversation back up rather than
  // leaving Edward stuck on "Connection lost".
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      const s = stateRef.current;
      if (s.lostConnection) {
        const { conversationId, mode, sensitivity } = lastConnectRef.current;
        connect(s.conversationId || conversationId, mode, sensitivity);
        return;
      }
      const active = s.phase === "listening" || s.phase === "thinking" || s.phase === "speaking";
      if (active && !mic.isLive()) {
        mic.stop();
        void mic.start();
      }
      void unlockAudio();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [connect, mic]);

  useEffect(() => {
    return () => {
      clearPing();
      const ws = wsRef.current;
      if (ws) {
        ws.onclose = null;
        ws.close();
        wsRef.current = null;
      }
    };
  }, []);

  return {
    state,
    connect,
    disconnect,
    interrupt,
    pttPress,
    pttRelease,
    getMicLevel: mic.getLevel,
    getOutputLevel: player.getOutputLevel,
    unlock: unlockAudio,
  };
}
