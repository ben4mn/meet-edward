"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";

import { micErrorMessage } from "@/lib/voice/micErrors";
import { pickRecorderMime } from "@/lib/voice/recorderMime";
import {
  initialRecorderState,
  reduceRecorder,
  type RecorderIntent,
  type RecorderState,
} from "@/lib/voice/recorderState";

export interface UseVoiceRecorderOptions {
  /** The finished clip and what the user asked to do with it. */
  onClip: (file: File, intent: RecorderIntent) => void;
}

export interface UseVoiceRecorderReturn {
  state: RecorderState;
  start: () => Promise<void>;
  stop: (intent: RecorderIntent) => void;
  cancel: () => void;
}

/** MediaRecorder wrapper for voice memos in the chat input. */
export function useVoiceRecorder(options: UseVoiceRecorderOptions): UseVoiceRecorderReturn {
  const [state, dispatch] = useReducer(reduceRecorder, undefined, initialRecorderState);
  const onClipRef = useRef(options.onClip);
  useEffect(() => {
    onClipRef.current = options.onClip;
  }, [options.onClip]);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const intentRef = useRef<RecorderIntent | null>(null);
  const extRef = useRef("webm");
  const tickRef = useRef<number | null>(null);

  const releaseMic = useCallback(() => {
    if (tickRef.current !== null) {
      window.clearInterval(tickRef.current);
      tickRef.current = null;
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    recorderRef.current = null;
    chunksRef.current = [];
  }, []);

  const start = useCallback(async () => {
    if (recorderRef.current) return;
    if (typeof MediaRecorder === "undefined") {
      dispatch({ type: "error", message: "This browser can't record audio." });
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const pick = pickRecorderMime((m) => MediaRecorder.isTypeSupported(m));
      extRef.current = pick.extension;
      const rec = pick.mimeType ? new MediaRecorder(stream, { mimeType: pick.mimeType }) : new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = () => {
        const type = rec.mimeType || pick.mimeType || "audio/webm";
        const blob = new Blob(chunksRef.current, { type });
        const intent = intentRef.current;
        releaseMic();
        dispatch({ type: "finished" });
        if (intent && blob.size > 0) {
          const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
          onClipRef.current(new File([blob], `memo-${stamp}.${extRef.current}`, { type }), intent);
        }
      };
      recorderRef.current = rec;
      rec.start(250);
      dispatch({ type: "start", startedAt: Date.now() });
      tickRef.current = window.setInterval(() => dispatch({ type: "tick", now: Date.now() }), 200);
    } catch (err) {
      releaseMic();
      // The same words live voice says (lib/voice/micErrors.ts, 2026-09-27).
      dispatch({ type: "error", message: micErrorMessage(err).message || "I couldn't start recording." });
    }
  }, [releaseMic]);

  const stop = useCallback((intent: RecorderIntent) => {
    const rec = recorderRef.current;
    if (!rec || rec.state === "inactive") return;
    intentRef.current = intent;
    dispatch({ type: "stop", intent });
    rec.stop();
  }, []);

  const cancel = useCallback(() => {
    const rec = recorderRef.current;
    intentRef.current = null;
    if (rec && rec.state !== "inactive") {
      rec.onstop = null;
      rec.stop();
    }
    releaseMic();
    dispatch({ type: "cancel" });
  }, [releaseMic]);

  // The reducer auto-stops at the maximum length; honour it.
  useEffect(() => {
    if (state.phase === "finishing" && state.intent && recorderRef.current?.state === "recording") {
      intentRef.current = state.intent;
      recorderRef.current.stop();
    }
  }, [state.phase, state.intent]);

  useEffect(() => releaseMic, [releaseMic]);

  return { state, start, stop, cancel };
}
