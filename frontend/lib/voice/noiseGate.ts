// Client-side noise gate for live voice.
//
// Scribe runs voice-activity detection on the server, and it hears
// everything the mic does: a TV two rooms away commits a "transcript"
// and Edward answers it, or barges in on himself. This gate sits between
// the mic and the socket. It tracks the room's noise floor and only lets
// frames through that rise clearly above it; everything else goes to the
// server as silence, so timing is preserved but there is nothing to
// transcribe. Pure: RMS in, open/closed out, no audio API in sight.

export type Sensitivity = "high" | "normal" | "low";

/** hears-everything → quiet-room default → noisy room */
export const SENSITIVITIES: readonly Sensitivity[] = ["high", "normal", "low"] as const;

export const SENSITIVITY_LABEL: Record<Sensitivity, string> = {
  high: "Hears everything",
  normal: "Normal",
  low: "Noisy room",
};

export function parseSensitivity(value: string | null | undefined): Sensitivity {
  return value === "high" || value === "low" ? value : "normal";
}

export interface GateParams {
  /** Gate disabled: every frame passes (the pre-gate behaviour). */
  bypass: boolean;
  /** A frame opens the gate when rms > floor * ratio ... */
  ratio: number;
  /** ... and rms > minRms, so a silent room can't open on a whisper of hiss. */
  minRms: number;
  /** How long the gate stays open after the last loud frame. */
  hangoverMs: number;
}

const PARAMS: Record<Sensitivity, GateParams> = {
  high: { bypass: true, ratio: 1, minRms: 0, hangoverMs: 0 },
  normal: { bypass: false, ratio: 2.5, minRms: 0.012, hangoverMs: 500 },
  low: { bypass: false, ratio: 4, minRms: 0.025, hangoverMs: 500 },
};

export function gateParams(s: Sensitivity): GateParams {
  return PARAMS[s];
}

export interface NoiseGate {
  params: GateParams;
  /** Running estimate of the room's noise floor (RMS). */
  floor: number;
  /** Milliseconds of hangover left before the gate closes. */
  hangoverLeft: number;
  open: boolean;
}

// The floor drops quickly (a quiet moment is the truth about the room)
// and rises slowly, so a voice — loud, but seconds long — never becomes
// the floor while a fan or a fridge — quiet, but minutes long — does.
const FLOOR_RISE_PER_MS = 0.0008; // fraction of the gap closed per ms
const FLOOR_START = 0.02;

export function createNoiseGate(sensitivity: Sensitivity): NoiseGate {
  return { params: gateParams(sensitivity), floor: FLOOR_START, hangoverLeft: 0, open: false };
}

/** Feed one frame's RMS (0..1). Returns whether the frame should be sent as-is. */
export function gateFrame(g: NoiseGate, rms: number, frameMs: number): boolean {
  if (g.params.bypass) {
    g.open = true;
    return true;
  }
  const threshold = Math.max(g.params.minRms, g.floor * g.params.ratio);
  const loud = rms > threshold;

  if (loud) {
    g.hangoverLeft = g.params.hangoverMs;
    g.open = true;
  } else {
    g.hangoverLeft = Math.max(0, g.hangoverLeft - frameMs);
    g.open = g.hangoverLeft > 0;
  }

  // Track the floor only from frames that aren't voice: quiet frames pull
  // it down at once; steady in-between noise lifts it a little at a time.
  if (rms < g.floor) {
    g.floor = rms;
  } else if (!loud) {
    g.floor += (rms - g.floor) * Math.min(1, FLOOR_RISE_PER_MS * frameMs);
  }
  return g.open;
}
