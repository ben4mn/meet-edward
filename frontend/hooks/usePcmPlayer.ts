"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";

import { getSharedAudioContext } from "@/lib/voice/audioContext";
import { int16ToFloat32 } from "@/lib/voice/pcm";
import { nextStartTime } from "@/lib/voice/pcmSchedule";
import { levelFromBytes } from "@/lib/voice/level";

export interface UsePcmPlayerOptions {
  /** Fired once after finalize() when the last scheduled chunk has ended. */
  onDrained?: () => void;
}

export interface UsePcmPlayerReturn {
  /** Queue a chunk of 16-bit PCM for gapless playback. */
  enqueue: (pcm: ArrayBuffer, sampleRate: number) => void;
  /** No more chunks are coming for this turn. */
  finalize: () => void;
  /** Cut playback immediately (barge-in). */
  stop: () => void;
  isPlaying: () => boolean;
  /** Edward's voice level 0..1 right now (for his mouth); read inside an animation frame. */
  getOutputLevel: () => number;
}

interface OutputGraph {
  ctx: AudioContext;
  gain: GainNode;
  analyser: AnalyserNode;
  buf: Uint8Array;
}

const HEADROOM_S = 0.06;

/**
 * Plays raw PCM straight off the socket: each chunk becomes an
 * AudioBuffer scheduled to start exactly where the previous one ends.
 * No container, no decoder, nothing to guess about.
 */
export function usePcmPlayer(options: UsePcmPlayerOptions = {}): UsePcmPlayerReturn {
  const onDrainedRef = useRef(options.onDrained);
  useEffect(() => {
    onDrainedRef.current = options.onDrained;
  }, [options.onDrained]);

  const sourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  // source → gain → analyser → speakers: the analyser is a tap for the
  // mouth; it changes nothing about what is heard.
  const graphRef = useRef<OutputGraph | null>(null);
  const outputFor = useCallback((ctx: AudioContext): AudioNode => {
    const g = graphRef.current;
    if (g && g.ctx === ctx) return g.gain;
    try {
      const gain = ctx.createGain();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.3;
      gain.connect(analyser);
      analyser.connect(ctx.destination);
      graphRef.current = { ctx, gain, analyser, buf: new Uint8Array(analyser.fftSize) };
      return gain;
    } catch {
      return ctx.destination;
    }
  }, []);
  const scheduledUntilRef = useRef(0);
  const finalizedRef = useRef(false);

  const maybeDrained = useCallback(() => {
    if (finalizedRef.current && sourcesRef.current.size === 0) {
      finalizedRef.current = false;
      scheduledUntilRef.current = 0;
      onDrainedRef.current?.();
    }
  }, []);

  const enqueue = useCallback(
    (pcm: ArrayBuffer, sampleRate: number) => {
      const samples = int16ToFloat32(pcm);
      if (samples.length === 0) return;
      const ctx = getSharedAudioContext();
      const buffer = ctx.createBuffer(1, samples.length, sampleRate);
      buffer.copyToChannel(samples as Float32Array<ArrayBuffer>, 0);
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(outputFor(ctx));
      const startAt = nextStartTime({
        now: ctx.currentTime,
        scheduledUntil: scheduledUntilRef.current,
        headroom: HEADROOM_S,
      });
      src.onended = () => {
        sourcesRef.current.delete(src);
        try {
          src.disconnect();
        } catch {
          /* fine */
        }
        maybeDrained();
      };
      sourcesRef.current.add(src);
      src.start(startAt);
      scheduledUntilRef.current = startAt + buffer.duration;
    },
    [maybeDrained, outputFor],
  );

  const stop = useCallback(() => {
    finalizedRef.current = false;
    sourcesRef.current.forEach((src) => {
      src.onended = null;
      try {
        src.stop();
      } catch {
        /* not started or already stopped */
      }
      try {
        src.disconnect();
      } catch {
        /* fine */
      }
    });
    sourcesRef.current.clear();
    scheduledUntilRef.current = 0;
  }, []);

  const finalize = useCallback(() => {
    finalizedRef.current = true;
    maybeDrained();
  }, [maybeDrained]);

  const isPlaying = useCallback(() => sourcesRef.current.size > 0, []);

  const getOutputLevel = useCallback(() => {
    const g = graphRef.current;
    if (!g || sourcesRef.current.size === 0) return 0;
    g.analyser.getByteTimeDomainData(g.buf as Uint8Array<ArrayBuffer>);
    return levelFromBytes(g.buf);
  }, []);

  // Unmount: stop, and take this player's gain → analyser pair out of the
  // shared AudioContext — each voice session used to leave one wired in.
  useEffect(
    () => () => {
      stop();
      const g = graphRef.current;
      graphRef.current = null;
      if (!g) return;
      for (const node of [g.gain, g.analyser]) {
        try {
          node.disconnect();
        } catch {
          /* already gone */
        }
      }
    },
    [stop],
  );

  return useMemo(
    () => ({ enqueue, finalize, stop, isPlaying, getOutputLevel }),
    [enqueue, finalize, stop, isPlaying, getOutputLevel],
  );
}
