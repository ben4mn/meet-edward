// The level of Edward's own voice, for his mouth (2026-09-26). An
// AnalyserNode after the PCM player's gain hands back unsigned bytes
// centred on 128 (getByteTimeDomainData); the mouth wants 0..1. Speech sits
// around RMS 0.1, so the curve opens the mouth well past halfway there.

const GAIN = 5;
const NOISE_FLOOR = 0.01;

export function levelFromBytes(bytes: Uint8Array): number {
  if (bytes.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < bytes.length; i++) {
    const v = (bytes[i] - 128) / 128;
    sum += v * v;
  }
  const rms = Math.sqrt(sum / bytes.length);
  if (rms < NOISE_FLOOR) return 0;
  return Math.min(1, rms * GAIN);
}
