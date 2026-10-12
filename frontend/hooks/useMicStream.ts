"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useBuildReloadHold } from "@/lib/buildReload";
import { getSharedAudioContext } from "@/lib/voice/audioContext";
import { downsample, floatTo16BitPCM } from "@/lib/voice/pcm";
import { micErrorMessage } from "@/lib/voice/micErrors";

export interface UseMicStreamOptions {
  /** Called with 16-bit little-endian PCM at targetSampleRate, plus the
   *  frame's raw RMS (0..1) so a gate can decide without re-scanning it. */
  onFrame: (pcm16: ArrayBuffer, rms: number) => void;
  onError?: (error: Error) => void;
  targetSampleRate?: number;
}

export interface UseMicStreamReturn {
  isActive: boolean;
  start: () => Promise<void>;
  stop: () => void;
  /** Recent RMS level 0..1, for a meter. Read it inside an animation frame. */
  getLevel: () => number;
  /** False once the OS has ended the mic track (iOS does this when the screen locks). */
  isLive: () => boolean;
}

// micErrorMessage lives in lib/voice/micErrors.ts (a leaf, so the chat's
// memo button can say the same words without pulling this hook in).
export { micErrorMessage } from "@/lib/voice/micErrors";

// Runs inside the AudioWorklet thread. Batches 2048 frames (~43 ms at 48 kHz)
// per message so the main thread isn't woken 375 times a second.
const WORKLET_SOURCE = `
class EdwardMicCapture extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(2048); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice(0)); this.n = 0; }
    }
    return true;
  }
}
registerProcessor("edward-mic-capture", EdwardMicCapture);
`;

let workletUrl: string | null = null;
const workletLoaded = new WeakSet<AudioContext>();

async function ensureWorklet(ctx: AudioContext): Promise<boolean> {
  if (!ctx.audioWorklet) return false;
  if (workletLoaded.has(ctx)) return true;
  try {
    if (!workletUrl) {
      workletUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "application/javascript" }));
    }
    await ctx.audioWorklet.addModule(workletUrl);
    workletLoaded.add(ctx);
    return true;
  } catch (err) {
    console.warn("[useMicStream] AudioWorklet unavailable, falling back:", err);
    return false;
  }
}

/**
 * Continuous microphone capture as 16 kHz PCM16 frames.
 *
 * Browser echo cancellation and noise suppression are requested so
 * Edward's own voice from the speakers mostly stays out of the mic.
 */
export function useMicStream(options: UseMicStreamOptions): UseMicStreamReturn {
  const { targetSampleRate = 16000 } = options;
  const [isActive, setIsActive] = useState(false);
  // Open or opening: a new deploy's reload waits (lib/buildReload.ts).
  // Whoever is talking to Edward hands-free — on /hub/ask nobody touches
  // the screen — sends no tap for minutes, and a reload ended the
  // conversation mid-sentence (2026-09-27 review). Set before the grant, so
  // a stop-then-start (the phone waking) never leaves a gap.
  const [open, setOpen] = useState(false);
  useBuildReloadHold(open);

  const onFrameRef = useRef(options.onFrame);
  const onErrorRef = useRef(options.onError);
  useEffect(() => {
    onFrameRef.current = options.onFrame;
  }, [options.onFrame]);
  useEffect(() => {
    onErrorRef.current = options.onError;
  }, [options.onError]);

  const streamRef = useRef<MediaStream | null>(null);
  const nodesRef = useRef<AudioNode[]>([]);
  const levelRef = useRef(0);
  const activeRef = useRef(false);

  const handleChunk = useCallback(
    (f32: Float32Array, inRate: number) => {
      if (!activeRef.current) return;
      let sum = 0;
      for (let i = 0; i < f32.length; i++) sum += f32[i] * f32[i];
      const rms = Math.sqrt(sum / Math.max(1, f32.length));
      levelRef.current = Math.min(1, rms * 4);
      const ds = downsample(f32, inRate, targetSampleRate);
      onFrameRef.current(floatTo16BitPCM(ds).buffer as ArrayBuffer, rms);
    },
    [targetSampleRate],
  );

  const stop = useCallback(() => {
    activeRef.current = false;
    for (const node of nodesRef.current) {
      try {
        node.disconnect();
      } catch {
        /* already gone */
      }
      const port = (node as AudioWorkletNode).port;
      if (port) port.onmessage = null;
      const sp = node as ScriptProcessorNode;
      if ("onaudioprocess" in sp) sp.onaudioprocess = null;
    }
    nodesRef.current = [];
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    levelRef.current = 0;
    setIsActive(false);
    setOpen(false);
  }, []);

  const start = useCallback(async () => {
    if (typeof window === "undefined" || activeRef.current) return;
    setOpen(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;

      const ctx = getSharedAudioContext();
      if (ctx.state === "suspended") {
        try {
          await ctx.resume();
        } catch {
          /* the view will prompt for a tap */
        }
      }

      const source = ctx.createMediaStreamSource(stream);
      // A muted sink keeps the graph "pulled" so capture nodes actually run.
      const sink = ctx.createGain();
      sink.gain.value = 0;
      sink.connect(ctx.destination);

      activeRef.current = true;
      if (await ensureWorklet(ctx)) {
        const node = new AudioWorkletNode(ctx, "edward-mic-capture", { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
        node.port.onmessage = (e: MessageEvent<Float32Array>) => handleChunk(e.data, ctx.sampleRate);
        source.connect(node);
        node.connect(sink);
        nodesRef.current = [source, node, sink];
      } else {
        const sp = ctx.createScriptProcessor(2048, 1, 1);
        sp.onaudioprocess = (e) => handleChunk(e.inputBuffer.getChannelData(0).slice(0), ctx.sampleRate);
        source.connect(sp);
        sp.connect(sink);
        nodesRef.current = [source, sp, sink];
      }
      setIsActive(true);
    } catch (err) {
      stop();
      onErrorRef.current?.(micErrorMessage(err));
    }
  }, [handleChunk, stop]);

  useEffect(() => stop, [stop]);

  const getLevel = useCallback(() => levelRef.current, []);

  const isLive = useCallback(() => {
    const tracks = streamRef.current?.getAudioTracks() ?? [];
    return activeRef.current && tracks.length > 0 && tracks.every((t) => t.readyState === "live");
  }, []);

  return useMemo(() => ({ isActive, start, stop, getLevel, isLive }), [isActive, start, stop, getLevel, isLive]);
}
