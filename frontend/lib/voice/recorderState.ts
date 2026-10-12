// State behind the voice-memo button: idle -> recording -> finishing -> idle.
// "intent" says what to do with the clip once MediaRecorder hands it over:
// post it as a memo, or drop the transcript into the text box.

export const MAX_MEMO_MS = 5 * 60 * 1000;

export type RecorderIntent = "send" | "text";

export interface RecorderState {
  phase: "idle" | "recording" | "finishing";
  startedAt: number | null;
  elapsedMs: number;
  intent: RecorderIntent | null;
  error: string | null;
}

export type RecorderEvent =
  | { type: "start"; startedAt: number }
  | { type: "tick"; now: number }
  | { type: "stop"; intent: RecorderIntent }
  | { type: "cancel" }
  | { type: "finished" }
  | { type: "error"; message: string };

export function initialRecorderState(): RecorderState {
  return { phase: "idle", startedAt: null, elapsedMs: 0, intent: null, error: null };
}

export function reduceRecorder(s: RecorderState, e: RecorderEvent): RecorderState {
  switch (e.type) {
    case "start":
      return { phase: "recording", startedAt: e.startedAt, elapsedMs: 0, intent: null, error: null };
    case "tick": {
      if (s.phase !== "recording" || s.startedAt === null) return s;
      const elapsedMs = Math.max(0, e.now - s.startedAt);
      if (elapsedMs > MAX_MEMO_MS) return { ...s, elapsedMs: MAX_MEMO_MS, phase: "finishing", intent: "send" };
      return { ...s, elapsedMs };
    }
    case "stop":
      if (s.phase !== "recording") return s;
      return { ...s, phase: "finishing", intent: e.intent };
    case "cancel":
      return { ...initialRecorderState(), error: s.error };
    case "finished":
      return initialRecorderState();
    case "error":
      return { ...initialRecorderState(), error: e.message };
  }
  return s;
}
