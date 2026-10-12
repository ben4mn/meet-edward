// PCM helpers for the voice path: mic float32 -> 16 kHz int16 for Scribe,
// 24 kHz int16 from ElevenLabs -> float32 for the Web Audio graph.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  downsample,
  floatTo16BitPCM,
  int16ToFloat32,
  prefixFrame,
} from "../lib/voice/pcm.ts";

describe("floatTo16BitPCM", () => {
  it("scales and clamps", () => {
    const out = floatTo16BitPCM(new Float32Array([0, 0.5, -0.5, 1, -1, 1.7, -1.7]));
    assert.equal(out.length, 7);
    assert.equal(out[0], 0);
    assert.equal(out[1], 16383);
    assert.equal(out[2], -16384);
    assert.equal(out[3], 32767);
    assert.equal(out[4], -32768);
    assert.equal(out[5], 32767);
    assert.equal(out[6], -32768);
  });
});

describe("int16ToFloat32", () => {
  it("is the inverse within rounding, and accepts raw bytes", () => {
    const pcm = new Int16Array([0, 16383, -16384, 32767, -32768]);
    const back = int16ToFloat32(pcm.buffer);
    assert.equal(back.length, 5);
    assert.ok(Math.abs(back[1] - 0.5) < 0.001);
    assert.ok(Math.abs(back[2] + 0.5) < 0.001);
    assert.ok(back[3] <= 1 && back[4] >= -1);
  });

  it("ignores a trailing odd byte rather than throwing", () => {
    const bytes = new Uint8Array([0, 0, 0xff, 0x7f, 0x01]);
    const out = int16ToFloat32(bytes.buffer);
    assert.equal(out.length, 2);
  });
});

describe("downsample", () => {
  it("48k -> 16k keeps one sample in three", () => {
    const src = new Float32Array(48);
    for (let i = 0; i < 48; i++) src[i] = i / 48;
    const out = downsample(src, 48000, 16000);
    assert.equal(out.length, 16);
    // averaged blocks stay monotonic
    for (let i = 1; i < out.length; i++) assert.ok(out[i] > out[i - 1]);
  });

  it("returns the input untouched when rates match", () => {
    const src = new Float32Array([0.1, 0.2]);
    assert.equal(downsample(src, 16000, 16000), src);
  });

  it("44.1k -> 16k produces the expected length", () => {
    const out = downsample(new Float32Array(4410), 44100, 16000);
    assert.equal(out.length, 1600);
  });
});

describe("prefixFrame", () => {
  it("prepends the tag byte", () => {
    const payload = new Uint8Array([9, 8, 7]);
    const framed = prefixFrame(0x01, payload);
    assert.deepEqual(Array.from(framed), [1, 9, 8, 7]);
  });
});
