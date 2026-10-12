// A line he says aloud plays once through the Mac mini's speakers
// (2026-09-28): POST /api/voice/synthesize, once per line id (remembered for
// the tab's session), never when Ellis turned it off, never a stale line.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, render } from "@testing-library/react";

import { SPOKEN_KEY, useSpeakAloud } from "../app/(display)/display/hooks/useSpeakAloud.ts";
import type { SpokenLine } from "../app/(display)/display/lib/stageModel.ts";

const originalFetch = globalThis.fetch;
let calls: { url: string; text: string | null; stream: string | null }[] = [];

beforeEach(() => {
  calls = [];
  sessionStorage.clear();
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const body = init?.body as FormData | undefined;
    calls.push({ url: String(input), text: (body?.get("text") as string) ?? null, stream: (body?.get("stream") as string) ?? null });
    return new Response(new ArrayBuffer(8), { status: 200, headers: { "Content-Type": "audio/mpeg" } });
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

function Probe({ line, enabled }: { line: SpokenLine | null; enabled: boolean }) {
  useSpeakAloud(line, enabled);
  return null;
}

const fresh = (over: Partial<SpokenLine> = {}): SpokenLine => ({
  id: "l1", text: "Biscuit's evening meds are due at 6.", at: new Date(Date.now() - 3_000).toISOString(), aloud: true, ...over,
});

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 10)); });

describe("useSpeakAloud", () => {
  it("synthesizes a fresh aloud line once, even across remounts", async () => {
    const { unmount } = render(<Probe line={fresh()} enabled />);
    await flush();
    unmount();
    render(<Probe line={fresh()} enabled />);
    await flush();
    assert.equal(calls.length, 1);
    assert.match(calls[0].url, /\/api\/voice\/synthesize$/);
    assert.equal(calls[0].text, "Biscuit's evening meds are due at 6.");
    assert.equal(calls[0].stream, "false");
    assert.ok((sessionStorage.getItem(SPOKEN_KEY) ?? "").includes("l1"));
  });

  it("stays quiet when speak aloud is off, for a quiet line, or an old one", async () => {
    render(<Probe line={fresh()} enabled={false} />);
    render(<Probe line={fresh({ id: "l2", aloud: false })} enabled />);
    render(<Probe line={fresh({ id: "l3", at: new Date(Date.now() - 120_000).toISOString() })} enabled />);
    await flush();
    assert.equal(calls.length, 0);
  });

  it("'played' holds a line only once its audio really started — refused or failed, it never did", async () => {
    const g = globalThis as unknown as { Audio?: unknown };
    const hadAudio = g.Audio;
    const urlApi = URL as unknown as { createObjectURL?: unknown; revokeObjectURL?: unknown };
    const hadCreate = urlApi.createObjectURL;
    const hadRevoke = urlApi.revokeObjectURL;
    const made: FakeAudio[] = [];
    let refuse = false;
    class FakeAudio {
      onplaying: (() => void) | null = null;
      onended: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor() { made.push(this); }
      play() { return refuse ? Promise.reject(new Error("NotAllowedError")) : Promise.resolve(); }
      pause() {}
    }
    g.Audio = FakeAudio;
    urlApi.createObjectURL = () => "blob:x";
    urlApi.revokeObjectURL = () => {};
    try {
      let seen: ReadonlySet<string> = new Set();
      function Seen({ line }: { line: SpokenLine }) {
        seen = useSpeakAloud(line, true).played;
        return null;
      }
      render(<Seen line={fresh({ id: "p1" })} />);
      await flush();
      assert.equal(made.length, 1);
      assert.equal(seen.has("p1"), false, "synthesized is not said");
      await act(async () => made[0].onplaying?.());
      assert.equal(seen.has("p1"), true, "it played");

      refuse = true;
      render(<Seen line={fresh({ id: "p2" })} />);
      await flush();
      assert.equal(seen.has("p2"), false, "Chrome refused it: never said aloud");
      cleanup();
      render(<Seen line={fresh({ id: "p1" })} />);
      await flush();
      assert.equal(seen.has("p1"), true, "a reload still knows it played");
    } finally {
      g.Audio = hadAudio;
      urlApi.createObjectURL = hadCreate;
      urlApi.revokeObjectURL = hadRevoke;
    }
  });
});
