// The voice player's output graph (review 2026-09-26,
// pcm-output-graph-not-disconnected): each voice session wired a
// gain → analyser pair into the shared AudioContext and left it there when
// the view went away. Unmounting disconnects the pair.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import { cleanup, renderHook } from "@testing-library/react";

import { usePcmPlayer } from "../hooks/usePcmPlayer.ts";

const disconnected: string[] = [];

class FakeNode {
  constructor(public name: string) {}
  connect() {}
  disconnect() {
    disconnected.push(this.name);
  }
}

class FakeAudioContext {
  state = "running";
  currentTime = 0;
  destination = new FakeNode("destination");
  createGain() {
    return new FakeNode("gain");
  }
  createAnalyser() {
    return Object.assign(new FakeNode("analyser"), { fftSize: 0, smoothingTimeConstant: 0, getByteTimeDomainData() {} });
  }
  createBuffer(_c: number, length: number, rate: number) {
    return { duration: length / rate, copyToChannel() {} };
  }
  createBufferSource() {
    return Object.assign(new FakeNode("source"), { buffer: null, onended: null, start() {}, stop() {} });
  }
}

const originalAC = (window as unknown as { AudioContext?: unknown }).AudioContext;
afterEach(() => {
  cleanup();
  (window as unknown as { AudioContext?: unknown }).AudioContext = originalAC;
  disconnected.length = 0;
});

describe("usePcmPlayer", () => {
  it("disconnects its gain → analyser pair when the view unmounts", () => {
    (window as unknown as { AudioContext: unknown }).AudioContext = FakeAudioContext;
    const { result, unmount } = renderHook(() => usePcmPlayer());
    result.current.enqueue(new Int16Array([1, 2, 3, 4]).buffer, 16_000);
    unmount();
    assert.equal(disconnected.includes("gain"), true, "gain disconnected");
    assert.equal(disconnected.includes("analyser"), true, "analyser disconnected");
  });
});
