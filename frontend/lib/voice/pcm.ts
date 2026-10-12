// PCM helpers for the voice path.
//
// Mic audio arrives as float32 at the AudioContext rate (44.1 or 48 kHz);
// Scribe wants 16 kHz int16 mono. ElevenLabs returns 24 kHz int16 mono;
// the Web Audio graph wants float32. Pure functions, unit-tested.

/** Float32 [-1, 1] -> Int16, clamped, truncating toward zero. */
export function floatTo16BitPCM(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i];
    out[i] = s < 0 ? Math.max(-1, s) * 0x8000 : Math.min(1, s) * 0x7fff;
  }
  return out;
}

/** Int16 (or raw little-endian bytes) -> Float32 [-1, 1]. A trailing odd byte is ignored. */
export function int16ToFloat32(input: ArrayBuffer | Int16Array): Float32Array {
  const pcm =
    input instanceof Int16Array
      ? input
      : new Int16Array(input, 0, Math.floor(input.byteLength / 2));
  const out = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) out[i] = pcm[i] / 0x8000;
  return out;
}

/**
 * Block-average resampler. Good enough for speech going into a
 * transcriber; not a general-purpose filter.
 */
export function downsample(src: Float32Array, inRate: number, outRate: number): Float32Array {
  if (inRate === outRate) return src;
  const ratio = inRate / outRate;
  const outLen = Math.floor(src.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const start = Math.round(i * ratio);
    const end = Math.min(src.length, Math.max(start + 1, Math.round((i + 1) * ratio)));
    let sum = 0;
    for (let j = start; j < end; j++) sum += src[j];
    out[i] = sum / (end - start);
  }
  return out;
}

/** Prepend a one-byte tag to a payload (0x01 mic frames, 0x02 TTS frames). */
export function prefixFrame(tag: number, payload: Uint8Array): Uint8Array {
  const framed = new Uint8Array(payload.length + 1);
  framed[0] = tag;
  framed.set(payload, 1);
  return framed;
}

export const PREFIX_MIC_AUDIO = 0x01;
export const PREFIX_TTS_AUDIO = 0x02;
