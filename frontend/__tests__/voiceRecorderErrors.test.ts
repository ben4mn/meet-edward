// The voice memo's mic errors in Edward's words (2026-09-27 integration).
// Live voice learned to say "I'm not allowed to use the microphone…" (L14),
// but the chat's memo button still said "Microphone permission denied." and
// passed "Requested device not found" through raw. Both now read the one
// micErrorMessage.
import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, renderHook } from "@testing-library/react";

import { useVoiceRecorder } from "../hooks/useVoiceRecorder.ts";

const named = (name: string, message = "raw") => Object.assign(new Error(message), { name });

describe("useVoiceRecorder says a failed start in Edward's words", () => {
  const beforeDevices = Object.getOwnPropertyDescriptor(globalThis.navigator, "mediaDevices");
  const g = globalThis as unknown as { MediaRecorder?: unknown };
  const beforeRecorder = g.MediaRecorder;
  afterEach(() => {
    cleanup();
    if (beforeDevices) Object.defineProperty(globalThis.navigator, "mediaDevices", beforeDevices);
    else delete (globalThis.navigator as unknown as { mediaDevices?: unknown }).mediaDevices;
    g.MediaRecorder = beforeRecorder;
  });

  for (const [name, said] of [
    ["NotAllowedError", "I'm not allowed to use the microphone. Allow it for this site, then try again."],
    ["NotFoundError", "I can't find a microphone. Plug one in, then try again."],
    ["NotReadableError", "Another app is using the microphone. Close it, then try again."],
  ] as const) {
    it(`${name} reads "${said}"`, async () => {
      g.MediaRecorder = class {
        static isTypeSupported() {
          return false;
        }
      };
      // Under Node 25 `navigator` is Node's own, not jsdom's.
      Object.defineProperty(globalThis.navigator, "mediaDevices", {
        configurable: true,
        value: { getUserMedia: async () => Promise.reject(named(name, "Requested device not found")) },
      });
      const { result } = renderHook(() => useVoiceRecorder({ onClip: () => {} }));
      await act(async () => {
        await result.current.start();
      });
      assert.equal(result.current.state.error, said);
      assert.equal(result.current.state.phase, "idle");
    });
  }
});
