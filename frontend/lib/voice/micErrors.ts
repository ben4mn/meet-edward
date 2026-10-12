// What the microphone says when it cannot start, in Edward's words.
// Pure: no React, no audio (__tests__/micStreamErrors.test.ts,
// voiceRecorderErrors.test.ts).

/**
 * getUserMedia's failure in Edward's words (L14, 2026-09-27 follow-ups: the
 * owner used to read "No microphone found." and "Microphone permission denied…"). First
 * person, and each says what to do; anything unrecognised passes through.
 * The voice view's headline reads a mic failure as "I can't hear you"
 * (lib/voice/voicePresence.ts voiceStatus), so every line names the mic.
 * Live voice (hooks/useMicStream) and the chat's voice memo
 * (hooks/useVoiceRecorder) both say these (2026-09-27 integration: the memo
 * still said "Microphone permission denied.").
 */
export function micErrorMessage(err: unknown): Error {
  const error = err instanceof Error ? err : new Error(String(err));
  switch (error.name) {
    case "NotAllowedError":
    case "PermissionDeniedError":
      return new Error("I'm not allowed to use the microphone. Allow it for this site, then try again.");
    case "NotFoundError":
    case "DevicesNotFoundError":
      return new Error("I can't find a microphone. Plug one in, then try again.");
    case "NotReadableError":
    case "TrackStartError":
      return new Error("Another app is using the microphone. Close it, then try again.");
    default:
      return error;
  }
}
