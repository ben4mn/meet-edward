// Whose voice a live-voice session is (security fix, 2026-10-11).
//
// The chat page's live voice is Ellis. The household hub's /hub/ask is
// whoever stands at the pet-station panel — someone in Ellis's home, not
// necessarily Ellis. The backend can only tell them apart by what the page
// declares in its start_session message: "surface": "app" | "hub". The
// hub's turns then run as `hub_voice`, which never gets Ellis's rights
// (texting anyone from his number, lifting stand-downs, typing in his
// Chrome). Before this, the hub ran as Ellis.
//
// The view needs a socket, a mic and an AudioContext to render, so the
// wiring is pinned in the source; the message itself is a pure function.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { startSessionMessage } from "../lib/voice/voiceProtocol.ts";

const read = (rel: string) => readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");
// The JSX element up to its self-closing end (props may hold arrow functions).
const mountOf = (src: string) => src.match(/<ContinuousVoiceView\b[\s\S]*?\/>/)?.[0] ?? "";

describe("start_session carries the surface", () => {
  it("says hub when the hub asks", () => {
    assert.deepEqual(startSessionMessage({ mode: "auto", sensitivity: "normal", surface: "hub" }), {
      type: "start_session",
      mode: "auto",
      sensitivity: "normal",
      surface: "hub",
    });
  });

  it("is the app (Ellis) unless told otherwise", () => {
    const msg = startSessionMessage({ mode: "ptt", sensitivity: "low" });
    assert.equal(msg.surface, "app");
    assert.equal(msg.type, "start_session");
    assert.equal("conversation_id" in msg, false);
  });

  it("names the conversation only when there is one", () => {
    const msg = startSessionMessage({ mode: "auto", sensitivity: "high", surface: "app", conversationId: "c-1" });
    assert.equal(msg.conversation_id, "c-1");
  });
});

describe("the hub's live voice declares itself", () => {
  const hub = read("app/(display)/hub/ask/page.tsx");
  const view = read("components/chat/ContinuousVoiceView.tsx");
  const hook = read("hooks/useVoiceSession.ts");

  it("the hub ask page mounts the voice view with surface hub", () => {
    const mount = mountOf(hub);
    assert.notEqual(mount, "", "the page mounts ContinuousVoiceView");
    assert.match(mount, /surface="hub"/);
  });

  it("the hub page still imports primitives by leaf path, never the components/ui barrel", () => {
    assert.doesNotMatch(hub, /from\s+["']@\/components\/ui["']/);
  });

  it("the view defaults to the app and hands its surface to the session", () => {
    assert.match(view, /surface\?:\s*VoiceSurface/);
    assert.match(view, /surface\s*=\s*"app"/);
    assert.match(view, /useVoiceSession\(surface\)/);
  });

  it("every start_session the session sends is built with the surface", () => {
    assert.match(hook, /startSessionMessage\(\{[^}]*surface[^}]*\}\)/s);
    assert.doesNotMatch(hook, /type:\s*"start_session"/, "one place builds the message");
  });

  it("the chat page's live voice stays Ellis's (no surface prop, so app)", () => {
    const chat = read("components/chat/ChatWindow.tsx");
    const mount = mountOf(chat);
    assert.notEqual(mount, "", "the chat mounts ContinuousVoiceView");
    assert.doesNotMatch(mount, /surface=/);
  });
});
