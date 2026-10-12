// What the live-voice mic says when it can't start (L14, 2026-09-27
// follow-ups). getUserMedia's errors reached Ellis as "No microphone found."
// and "Microphone permission denied…" — not Edward's voice. They are first
// person and say what to do; anything unrecognised passes through unchanged.
import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, renderHook } from "@testing-library/react";

import { micErrorMessage, useMicStream } from "../hooks/useMicStream.ts";

const named = (name: string, message = "raw") => Object.assign(new Error(message), { name });

describe("micErrorMessage", () => {
  it("no microphone: I can't find one", () => {
    assert.equal(micErrorMessage(named("NotFoundError")).message, "I can't find a microphone. Plug one in, then try again.");
    assert.equal(micErrorMessage(named("DevicesNotFoundError")).message, "I can't find a microphone. Plug one in, then try again.");
  });

  it("blocked: I'm not allowed to use it, and how to allow it", () => {
    for (const name of ["NotAllowedError", "PermissionDeniedError"]) {
      assert.equal(
        micErrorMessage(named(name)).message,
        "I'm not allowed to use the microphone. Allow it for this site, then try again.",
      );
    }
  });

  it("busy: another app has it", () => {
    for (const name of ["NotReadableError", "TrackStartError"]) {
      assert.equal(micErrorMessage(named(name)).message, "Another app is using the microphone. Close it, then try again.");
    }
  });

  it("never the old copy", () => {
    for (const name of ["NotFoundError", "NotAllowedError", "PermissionDeniedError"]) {
      assert.doesNotMatch(micErrorMessage(named(name)).message, /No microphone found|permission denied/i);
    }
  });

  it("anything else passes through; a non-Error becomes one", () => {
    const odd = named("AbortError", "The operation was aborted.");
    assert.equal(micErrorMessage(odd), odd);
    assert.equal(micErrorMessage("boom").message, "boom");
  });
});

// micErrorMessage alone proved nothing about what Ellis reads: put the old
// inline branch back in the hook's catch and every test above still passed
// — and since those strings name the microphone too, the headline would
// still read "I can't hear you" (2026-09-27 review). This drives the hook.
describe("useMicStream says a failed start in Edward's words", () => {
  const before = Object.getOwnPropertyDescriptor(globalThis.navigator, "mediaDevices");
  afterEach(() => {
    cleanup();
    if (before) Object.defineProperty(globalThis.navigator, "mediaDevices", before);
    else delete (globalThis.navigator as unknown as { mediaDevices?: unknown }).mediaDevices;
  });

  for (const [name, said] of [
    ["NotFoundError", "I can't find a microphone. Plug one in, then try again."],
    ["NotAllowedError", "I'm not allowed to use the microphone. Allow it for this site, then try again."],
    ["NotReadableError", "Another app is using the microphone. Close it, then try again."],
  ] as const) {
    it(`${name} reaches onError as "${said}"`, async () => {
      // Under Node 25 `navigator` is Node's own, not jsdom's.
      Object.defineProperty(globalThis.navigator, "mediaDevices", {
        configurable: true,
        value: { getUserMedia: async () => Promise.reject(named(name, "Requested device not found")) },
      });
      const errors: string[] = [];
      const { result } = renderHook(() => useMicStream({ onFrame: () => {}, onError: (e) => errors.push(e.message) }));
      await act(async () => {
        await result.current.start();
      });
      assert.deepEqual(errors, [said]);
      assert.equal(result.current.isActive, false);
    });
  }
});
