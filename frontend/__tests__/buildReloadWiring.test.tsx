// H3 wiring (2026-09-27 follow-ups): the hook that reloads a tab running an
// old bundle, where it is mounted, the route it asks, the service worker
// rule that keeps that answer off the cache, and the one build id baked
// into both the client and the server. The decision itself is pinned in
// buildReload.test.ts.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { BUILD_ID_PATH, LAST_ATTEMPT_KEY, reloadHeld, useBuildReload, type BuildReloadDeps } from "../lib/buildReload.ts";
import { AppBuildReload } from "../components/layout/AppBuildReload.tsx";
import { InputBar } from "../components/chat/InputBar.tsx";
import { useMicStream, type UseMicStreamReturn } from "../hooks/useMicStream.ts";
import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider, useChat } from "../lib/ChatContext.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";
import { clearDraftFiles, writeDraftFiles } from "../lib/draftFiles.ts";

const ROOT = path.resolve(__dirname, "..");
const read = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8");
const require = createRequire(import.meta.url);
const originalFetch = globalThis.fetch;

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
  window.sessionStorage.clear();
});

/** A clock the test moves, a server that answers `served`, a reload that is counted. */
function harness(served: string | null, baked = "old-build") {
  const h = { t: 1_800_000_000_000, reloads: 0, asked: 0, served };
  const deps: BuildReloadDeps = {
    bakedId: baked,
    fetchServed: async () => {
      h.asked++;
      return h.served;
    },
    reload: () => void h.reloads++,
    now: () => h.t,
    firstCheckMs: 60 * 60_000, // the test triggers checks itself
    everyMs: 60 * 60_000,
  };
  return { h, deps };
}

/** What a tab coming back into view does: check at once. */
async function comeBack() {
  await act(async () => {
    document.dispatchEvent(new window.Event("visibilitychange"));
    await new Promise((r) => setTimeout(r, 5));
  });
}

function Display({ deps }: { deps: BuildReloadDeps }) {
  useBuildReload("display", undefined, deps);
  return <div>screen</div>;
}

describe("useBuildReload on a display", () => {
  it("reloads when the server runs a newer build, and remembers it tried", async () => {
    const { h, deps } = harness("new-build");
    render(<Display deps={deps} />);
    await comeBack();
    assert.equal(h.asked, 1);
    assert.equal(h.reloads, 1);
    assert.deepEqual(JSON.parse(window.sessionStorage.getItem(LAST_ATTEMPT_KEY) ?? "null"), { to: "new-build", at: h.t });
  });

  it("does nothing when the build is current or the server does not say", async () => {
    for (const served of ["old-build", null]) {
      const { h, deps } = harness(served);
      render(<Display deps={deps} />);
      await comeBack();
      assert.equal(h.reloads, 0, `served ${served}`);
      cleanup();
    }
  });

  it("waits while someone is touching the screen, then reloads", async () => {
    const { h, deps } = harness("new-build");
    render(<Display deps={deps} />);
    fireEvent.pointerDown(document.body);
    await comeBack();
    assert.equal(h.reloads, 0, "a tap a moment ago");
    h.t += 31_000;
    await comeBack();
    assert.equal(h.reloads, 1);
  });

  // A page that scrolls itself (the /screen reply card follows a streamed
  // answer) must not read as someone at the screen, or it would never
  // reload; a person scrolling also sends wheel, touch or key events.
  it("a programmatic scroll is not someone at the screen", async () => {
    const { h, deps } = harness("new-build");
    render(<Display deps={deps} />);
    document.body.dispatchEvent(new window.Event("scroll"));
    await comeBack();
    assert.equal(h.reloads, 1);
  });

  it("a page load's own pageshow is not a check; coming back from the back/forward cache is", async () => {
    const { h, deps } = harness("new-build");
    render(<Display deps={deps} />);
    await act(async () => {
      window.dispatchEvent(Object.assign(new window.Event("pageshow"), { persisted: false }));
      await new Promise((r) => setTimeout(r, 5));
    });
    assert.equal(h.asked, 0, "a fresh load was just served");
    await act(async () => {
      window.dispatchEvent(Object.assign(new window.Event("pageshow"), { persisted: true }));
      await new Promise((r) => setTimeout(r, 5));
    });
    assert.equal(h.asked, 1);
    assert.equal(h.reloads, 1);
  });

  // 2026-09-27 review: on display routes nothing but a tap held a reload,
  // so a video someone was watching on /screen started over, and a message
  // half-typed into the /screen compose bar was lost, half a minute after
  // the last touch.
  it("never while a video is playing or a half-typed message sits in a box", async () => {
    const { h, deps } = harness("new-build");
    function Screen() {
      useBuildReload("display", undefined, deps);
      return (
        <>
          <video data-testid="show" />
          <textarea aria-label="Ask Edward" defaultValue="" />
        </>
      );
    }
    render(<Screen />);
    const video = screen.getByTestId("show") as HTMLVideoElement;
    Object.defineProperty(video, "paused", { configurable: true, value: false });
    h.t += 10 * 60_000;
    await comeBack();
    assert.equal(h.reloads, 0, "the video would start over");
    Object.defineProperty(video, "paused", { configurable: true, value: true });

    fireEvent.change(screen.getByRole("textbox", { name: "Ask Edward" }), { target: { value: "what's on tonight" } });
    await comeBack();
    assert.equal(h.reloads, 0, "the message would be lost");
    fireEvent.change(screen.getByRole("textbox", { name: "Ask Edward" }), { target: { value: "" } });
    await comeBack();
    assert.equal(h.reloads, 1);
  });

  // Live voice on /hub/ask: whoever is talking to Edward hands-free sends
  // no tap for minutes, so half a minute in the kiosk read as idle and a
  // deploy's reload ended the conversation mid-sentence (2026-09-27 review).
  // The microphone holds while it is open or opening.
  it("never while the microphone is open", async () => {
    const before = Object.getOwnPropertyDescriptor(globalThis.navigator, "mediaDevices");
    // Under Node 25 `navigator` is Node's own. A grant still pending: the mic is opening.
    Object.defineProperty(globalThis.navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: () => new Promise<MediaStream>(() => {}) },
    });
    try {
      const { h, deps } = harness("new-build");
      let mic!: UseMicStreamReturn;
      function Ask() {
        useBuildReload("display", undefined, deps);
        mic = useMicStream({ onFrame: () => {} });
        return null;
      }
      render(<Ask />);
      act(() => void mic.start());
      h.t += 10 * 60_000;
      await comeBack();
      assert.equal(h.asked, 1);
      assert.equal(h.reloads, 0, "a reload would end the voice session");
      act(() => mic.stop());
      await comeBack();
      assert.equal(h.reloads, 1);
    } finally {
      if (before) Object.defineProperty(globalThis.navigator, "mediaDevices", before);
      else delete (globalThis.navigator as unknown as { mediaDevices?: unknown }).mediaDevices;
    }
  });

  it("is off without a baked id (next dev without one, tests)", async () => {
    const { h, deps } = harness("new-build", "");
    render(<Display deps={deps} />);
    await comeBack();
    assert.equal(h.asked, 0);
  });
});

// ---- the app: never while Ellis is in the middle of something -----------------

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value="/chat">
        <SearchParamsContext.Provider value={new URLSearchParams() as never}>
          <ToastProvider>
            <AuthProvider>
              <ChatProvider>{children}</ChatProvider>
            </AuthProvider>
          </ToastProvider>
        </SearchParamsContext.Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>
  );
}

/** getUserMedia and a MediaRecorder that records until stopped. Under
 *  Node 25 `navigator` is Node's own, not jsdom's, so it is patched there. */
function fakeMicrophone(): () => void {
  class FakeRecorder {
    static isTypeSupported() {
      return true;
    }
    state = "inactive";
    mimeType = "audio/webm";
    ondataavailable: ((e: unknown) => void) | null = null;
    onstop: (() => void) | null = null;
    start() {
      this.state = "recording";
    }
    stop() {
      this.state = "inactive";
      this.onstop?.();
    }
  }
  const g = globalThis as unknown as { MediaRecorder?: unknown };
  const w = window as unknown as { MediaRecorder?: unknown };
  const before = { g: g.MediaRecorder, w: w.MediaRecorder, devices: Object.getOwnPropertyDescriptor(globalThis.navigator, "mediaDevices") };
  g.MediaRecorder = FakeRecorder;
  w.MediaRecorder = FakeRecorder;
  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) },
  });
  return () => {
    g.MediaRecorder = before.g;
    w.MediaRecorder = before.w;
    if (before.devices) Object.defineProperty(globalThis.navigator, "mediaDevices", before.devices);
    else delete (globalThis.navigator as unknown as { mediaDevices?: unknown }).mediaDevices;
  };
}

describe("AppBuildReload", () => {
  it("holds while the composer has text, and reloads once it is empty", async () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const p = String(input).replace(/^https?:\/\/[^/]+/, "");
      if (p.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
      if (p.startsWith("/api/conversations")) return json({ conversations: [], has_more: false });
      return json({});
    }) as typeof fetch;
    const { h, deps } = harness("new-build");
    render(
      <Shell>
        <AppBuildReload deps={deps} />
        <InputBar onSend={() => {}} isLoading={false} />
      </Shell>,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Message Edward" }), { target: { value: "half a thought" } });
    await comeBack();
    assert.equal(h.asked, 1);
    assert.equal(h.reloads, 0, "never with something in the composer");

    fireEvent.change(screen.getByRole("textbox", { name: "Message Edward" }), { target: { value: "" } });
    await comeBack();
    await waitFor(() => assert.equal(h.reloads, 1));
  });

  // A memo is tap to start, tap to stop, up to five minutes of Ellis talking
  // with no tap, key or scroll: two minutes in, the app read as idle and a
  // deploy's reload tore down the MediaRecorder and the memo with it
  // (2026-09-27 review).
  it("holds while a voice memo is being recorded", async () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const p = String(input).replace(/^https?:\/\/[^/]+/, "");
      if (p.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
      if (p.startsWith("/api/conversations")) return json({ conversations: [], has_more: false });
      return json({});
    }) as typeof fetch;
    const restore = fakeMicrophone();
    try {
      const { h, deps } = harness("new-build");
      render(
        <Shell>
          <AppBuildReload deps={deps} />
          <InputBar onSend={() => {}} isLoading={false} voiceEnabled onEnterVoiceMode={() => {}} />
        </Shell>,
      );
      const mic = await screen.findByRole("button", { name: "Record a voice memo" });
      fireEvent.pointerDown(mic); // the start tap is his last input
      fireEvent.click(mic);
      await screen.findByRole("group", { name: "Recording a voice memo" });
      // The memo button tells the composer it is recording from an effect,
      // and the composer's hold registers in the next commit's: the group can
      // be on screen a moment before (about 1 run in 50 under load).
      await waitFor(() => assert.equal(reloadHeld(), true));
      h.t += 150_000; // two and a half minutes of talking
      await comeBack();
      assert.equal(h.asked, 1);
      assert.equal(h.reloads, 0, "never in the middle of a memo");

      fireEvent.click(screen.getByRole("button", { name: "Discard recording" }));
      await waitFor(() => assert.equal(screen.queryByRole("group", { name: "Recording a voice memo" }), null));
      h.t += 150_000;
      await comeBack();
      await waitFor(() => assert.equal(h.reloads, 1));
    } finally {
      restore();
    }
  });

  // The composer was the only thing on the app that held a reload: a new
  // document pasted into its sheet and read over for two minutes was gone
  // (2026-09-27 review).
  it("holds while any other box on the page has something written in it", async () => {
    globalThis.fetch = (async () => json({ conversations: [], has_more: false })) as typeof fetch;
    const { h, deps } = harness("new-build");
    render(
      <Shell>
        <AppBuildReload deps={deps} />
        <textarea aria-label="Text" defaultValue="" />
      </Shell>,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Text" }), { target: { value: "Biscuit — med protocol" } });
    h.t += 10 * 60_000;
    await comeBack();
    assert.equal(h.reloads, 0);
    fireEvent.change(screen.getByRole("textbox", { name: "Text" }), { target: { value: "" } });
    await comeBack();
    assert.equal(h.reloads, 1);
  });

  // Attachments live only in memory (lib/draftFiles.ts): a reload would
  // drop the ones kept for a thread Ellis is not looking at, where no
  // composer is mounted to hold it.
  it("holds while any thread keeps attachments, even with no composer on screen", async () => {
    globalThis.fetch = (async () => json({ conversations: [], has_more: false })) as typeof fetch;
    const { h, deps } = harness("new-build");
    writeDraftFiles("c-elsewhere", [new File(["x"], "plan.txt", { type: "text/plain" })]);
    render(
      <Shell>
        <AppBuildReload deps={deps} />
      </Shell>,
    );
    await comeBack();
    assert.equal(h.reloads, 0);
    clearDraftFiles("c-elsewhere");
    await comeBack();
    assert.equal(h.reloads, 1);
  });
});

// A reload closes the chat's stream, and the server then cancels Edward's
// turn part-way through its tools; it also closes live voice. The wiring
// used to be pinned only by the words isTurnLive and isVoiceMode appearing
// in AppBuildReload's source, so dropping turn and voice from what it hands
// useBuildReload kept every test green (2026-09-27 review). These drive a
// real turn and real voice mode through the ChatProvider.
describe("AppBuildReload waits out a live turn and live voice", () => {
  type Chat = ReturnType<typeof useChat>;
  let chat: Chat;
  function Probe() {
    chat = useChat();
    return null;
  }
  interface Stream {
    send: (e: Record<string, unknown>) => void;
    close: () => void;
  }
  let streams: Stream[] = [];
  function installServer({ voice }: { voice: boolean }) {
    streams = [];
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const p = String(input).replace(/^https?:\/\/[^/]+/, "");
      if (p.startsWith("/api/auth/status")) return json({ configured: true, authenticated: true });
      if (p.startsWith("/api/voice/status")) return json(voice ? { enabled: true, status: "connected" } : { enabled: false, status: "disabled" });
      if (p === "/api/chat") {
        let controller!: ReadableStreamDefaultController<Uint8Array>;
        const body = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) });
        const enc = new TextEncoder();
        init?.signal?.addEventListener("abort", () => {
          try {
            controller.error(new DOMException("aborted", "AbortError"));
          } catch {
            /* closed */
          }
        });
        streams.push({
          send: (e) => controller.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)),
          close: () => controller.close(),
        });
        return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      if (/^\/api\/chat\/[^/]+\/processing/.test(p)) return json({ processing: false });
      if (p.startsWith("/api/conversations/latest")) return json({ updated_at: null });
      if (p.startsWith("/api/conversations/c-turn")) return json({ id: "c-turn", title: "Calendar", source: "user", messages: [] });
      if (p.startsWith("/api/conversations")) return json({ conversations: [], has_more: false });
      return json({});
    }) as typeof fetch;
  }
  const settle = (ms = 25) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));

  it("never mid chat turn; once the turn is done, it reloads", async () => {
    installServer({ voice: false });
    const { h, deps } = harness("new-build");
    render(
      <Shell>
        <AppBuildReload deps={deps} />
        <Probe />
      </Shell>,
    );
    await settle();
    await act(async () => {
      void chat.sendMessage("check the calendar");
      await new Promise((r) => setTimeout(r, 20));
    });
    assert.equal(streams.length, 1);
    await act(async () => {
      streams[0].send({ type: "tool_start", conversation_id: "c-turn", tool_name: "list_events" });
      await new Promise((r) => setTimeout(r, 20));
    });
    h.t += 10 * 60_000; // ten quiet minutes while Edward works
    await comeBack();
    assert.equal(h.asked, 1);
    assert.equal(h.reloads, 0, "a reload would cancel Edward's turn");

    await act(async () => {
      streams[0].send({ type: "content", conversation_id: "c-turn", content: "You're free after 3." });
      streams[0].send({ type: "done", conversation_id: "c-turn" });
      streams[0].close();
      await new Promise((r) => setTimeout(r, 30));
    });
    await comeBack();
    await waitFor(() => assert.equal(h.reloads, 1));
  });

  // The queue lives only in the ChatProvider, per thread, and the reload
  // read only the thread on screen's: queue two follow-ups in one thread,
  // press Stop (the queue waits for "Send now"), move to another thread,
  // and a deploy's reload dropped them (2026-09-27 review).
  it("never while messages wait in the queue of a thread Ellis is not looking at", async () => {
    installServer({ voice: false });
    const { h, deps } = harness("new-build");
    render(
      <Shell>
        <AppBuildReload deps={deps} />
        <Probe />
      </Shell>,
    );
    await settle();
    await act(async () => {
      void chat.sendMessage("check the calendar");
      await new Promise((r) => setTimeout(r, 20));
    });
    await act(async () => {
      streams[0].send({ type: "tool_start", conversation_id: "c-turn", tool_name: "list_events" });
      await new Promise((r) => setTimeout(r, 20));
    });
    act(() => chat.queueMessage("and book the dentist"));
    await act(async () => {
      chat.stopGeneration();
      await new Promise((r) => setTimeout(r, 30));
    });
    act(() => chat.startNewChat());
    await settle();
    assert.equal(chat.queuedMessages.length, 0, "the new chat's own queue is empty");
    h.t += 10 * 60_000;
    await comeBack();
    assert.equal(h.reloads, 0, "the queued message in the other thread would be lost");
  });

  it("never with live voice on; once it ends, it reloads", async () => {
    installServer({ voice: true });
    const { h, deps } = harness("new-build");
    render(
      <Shell>
        <AppBuildReload deps={deps} />
        <Probe />
      </Shell>,
    );
    await waitFor(() => assert.equal(chat.voiceEnabled, true));
    act(() => chat.enterVoiceMode());
    assert.equal(chat.isVoiceMode, true);
    h.t += 10 * 60_000; // talking, not tapping
    await comeBack();
    assert.equal(h.asked, 1);
    assert.equal(h.reloads, 0, "a reload would end the voice session");

    await act(async () => {
      await chat.exitVoiceMode();
    });
    await comeBack();
    await waitFor(() => assert.equal(h.reloads, 1));
  });
});

// ---- where it is mounted --------------------------------------------------------

describe("the layouts mount it", () => {
  it("display and hub routes: the (display) layout", () => {
    assert.match(read("app/(display)/layout.tsx"), /useBuildReload\(\s*["']display["']/);
  });

  it("the app: inside the ChatProvider, where the turn, voice and queue are readable", () => {
    const src = read("app/(app)/layout.tsx");
    assert.match(src, /<ChatProvider>[\s\S]*<AppBuildReload\s*\/>[\s\S]*<\/ChatProvider>/);
  });

  it("the watcher reads the chat's turn, voice and queue", () => {
    const src = read("components/layout/AppBuildReload.tsx");
    assert.match(src, /useChatPresence\(\)/);
    assert.match(src, /useComposerQueue\(\)/);
    assert.match(src, /isTurnLive\(/);
    assert.match(src, /isVoiceMode/);
  });

  it("the composer holds reloads while it has text or attachments", () => {
    assert.match(read("components/chat/InputBar.tsx"), /useBuildReloadHold\(/);
  });

  it("lib/buildReload stays tiny: react only, never framer-motion or the chat", () => {
    const imports = [...read("lib/buildReload.ts").matchAll(/^\s*import\s+(?!type\b)[^;]*?from\s*["']([^"']+)["']/gm)].map((m) => m[1]);
    assert.deepEqual(imports.filter((s) => s !== "react"), []);
  });
});

// ---- the build id: served by Next (not the backend), never cached ----------------

describe("the served build id", () => {
  it("lives outside /api (next.config rewrites /api/* to the backend)", () => {
    assert.equal(BUILD_ID_PATH, "/build-id");
    assert.doesNotMatch(BUILD_ID_PATH, /^\/api\//);
  });

  it("the route answers the baked id, dynamic and uncacheable", async () => {
    const src = read("app/build-id/route.ts");
    assert.match(src, /export const dynamic = ["']force-dynamic["']/);
    process.env.NEXT_PUBLIC_BUILD_ID = "route-test-build";
    const { GET } = await import(`../app/build-id/route.ts?${Date.now()}`);
    const res: Response = await GET();
    assert.equal(res.status, 200);
    assert.match(res.headers.get("cache-control") ?? "", /no-store/);
    assert.deepEqual(await res.json(), { build_id: "route-test-build" });
    delete process.env.NEXT_PUBLIC_BUILD_ID;
  });

  it("the service worker never answers it from a cache", () => {
    const { handlerFor } = require("../pwa-caching.js");
    assert.equal(handlerFor("/build-id"), "NetworkOnly");
    assert.equal(handlerFor("/build-id?t=123"), "NetworkOnly");
  });
});

describe("next.config bakes one build id into client and server", () => {
  function loaded(env: Record<string, string>) {
    const out = execFileSync(
      process.execPath,
      [
        "-e",
        "const c=require('./next.config.js');Promise.resolve(c.generateBuildId()).then(id=>process.stdout.write(JSON.stringify({id,env:(c.env||{}).NEXT_PUBLIC_BUILD_ID??null})))",
      ],
      { cwd: ROOT, env: { ...process.env, EDWARD_BUILD_ID: "", ...env }, encoding: "utf8" },
    );
    return JSON.parse(out) as { id: string; env: string | null };
  }

  it("generateBuildId and NEXT_PUBLIC_BUILD_ID are the same value: the commit and when it was built", () => {
    const { id, env } = loaded({});
    assert.equal(env, id);
    assert.match(id, /^([0-9a-f]{7,40}|nogit)-\d{8}T\d{6}$/);
  });

  it("an id handed in from outside (the deploy) wins, so a whole build shares it", () => {
    const { id, env } = loaded({ EDWARD_BUILD_ID: "deploy-supplied-1" });
    assert.equal(id, "deploy-supplied-1");
    assert.equal(env, "deploy-supplied-1");
  });
});

// Pre-deploy review (2026-09-27): a finished video show that stays up on
// /screen replayed with sound after a deploy's reload. The real overlay's
// <video autoPlay> holds the reload while it is there, playing or not.
describe("a video show holds the display's reload", () => {
  it("while it is up, finished or not; not once it is cleared", async (t) => {
    const { ScreenOverlay } = await import("../app/(display)/display/components/ScreenOverlay.tsx");
    // The overlay leans on Next's automatic JSX runtime; tsx compiles classic.
    const g = globalThis as { React?: typeof React };
    const hadReact = "React" in g;
    g.React = React;
    t.after(() => {
      if (!hadReact) delete g.React;
    });
    const { h, deps } = harness("new-build");
    const show = { kind: "video", source: "https://example.com/clip.mp4", title: "clip" } as never;
    const { rerender } = render(
      <>
        <Display deps={deps} />
        <ScreenOverlay show={show} onDismiss={() => {}} />
      </>,
    );
    const video = document.querySelector("video");
    assert.ok(video, "the overlay drew a <video>");
    Object.defineProperty(video, "ended", { configurable: true, value: true });
    await comeBack();
    assert.equal(h.reloads, 0, "a finished clip on screen would start over");
    rerender(
      <>
        <Display deps={deps} />
        <ScreenOverlay show={null} onDismiss={() => {}} />
      </>,
    );
    await comeBack();
    assert.equal(h.reloads, 1);
  });
});
