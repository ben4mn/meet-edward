// H3 (2026-09-27 follow-ups): always-on displays never picked up a frontend
// deploy. The /screen tab in Ellis's Chrome kept running the pre-deploy
// bundle (36,694 GET /api/display/signals at the old 2 s cadence the day
// after the deploy, the new code's /api/mind/now almost never), and so
// would the Pi kiosk. Every tab now compares the build it was built from
// with the one the server serves (/build-id) and reloads when they differ:
// on display and hub routes whenever nobody is touching the screen, on the
// app only when Ellis is not in the middle of something — never mid chat
// turn, never with live voice on, never with anything in the composer.
//
// This file pins the pure decision; buildReloadWiring.test.tsx the hook,
// the layouts, the route and the service worker rule.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  INPUT_QUIET_MS,
  RETRY_GUARD_MS,
  buildReloadDecision,
  pageHoldsWork,
  parseServedBuildId,
  type BuildReloadState,
} from "../lib/buildReload.ts";

const NOW = 1_800_000_000_000;
const base = (over: Partial<BuildReloadState> = {}): BuildReloadState => ({
  baked: "abc123-20260927T100000",
  served: "def456-20260927T120000",
  surface: "app",
  sinceInputMs: Infinity,
  now: NOW,
  ...over,
});

describe("buildReloadDecision", () => {
  it("a new build on the server and an idle page: reload", () => {
    assert.equal(buildReloadDecision(base()), "reload");
    assert.equal(buildReloadDecision(base({ surface: "display" })), "reload");
  });

  it("the same build: nothing to do", () => {
    assert.equal(buildReloadDecision(base({ served: "abc123-20260927T100000" })), "current");
  });

  it("no id on either side (a dev server, an unreachable server): never guess", () => {
    assert.equal(buildReloadDecision(base({ served: null })), "unknown");
    assert.equal(buildReloadDecision(base({ served: "" })), "unknown");
    assert.equal(buildReloadDecision(base({ baked: "" })), "unknown");
    assert.equal(buildReloadDecision(base({ baked: undefined })), "unknown");
  });

  it("the app waits: never mid chat turn, never with live voice on, never with the composer holding something", () => {
    assert.equal(buildReloadDecision(base({ busy: { turn: true } })), "busy");
    assert.equal(buildReloadDecision(base({ busy: { voice: true } })), "busy");
    assert.equal(buildReloadDecision(base({ busy: { composer: true } })), "busy");
    assert.equal(buildReloadDecision(base({ busy: { turn: false, voice: false, composer: false } })), "reload");
  });

  it("display and hub routes reload regardless of the chat's state", () => {
    assert.equal(buildReloadDecision(base({ surface: "display", busy: { turn: true, voice: true, composer: true } })), "reload");
  });

  it("a component's explicit hold is respected on every surface", () => {
    assert.equal(buildReloadDecision(base({ busy: { held: true } })), "busy");
    assert.equal(buildReloadDecision(base({ surface: "display", busy: { held: true } })), "busy");
  });

  it("never under someone's finger: a recent tap, key or scroll waits", () => {
    assert.equal(buildReloadDecision(base({ sinceInputMs: 5_000 })), "busy");
    assert.equal(buildReloadDecision(base({ sinceInputMs: INPUT_QUIET_MS.app + 1 })), "reload");
    assert.equal(buildReloadDecision(base({ surface: "display", sinceInputMs: 5_000 })), "busy");
    assert.equal(buildReloadDecision(base({ surface: "display", sinceInputMs: INPUT_QUIET_MS.display + 1 })), "reload");
    assert.ok(INPUT_QUIET_MS.display < INPUT_QUIET_MS.app, "a wall display is rarely touched; the app waits longer");
  });

  it("no reload loop: a reload toward this build that left the old bundle running waits", () => {
    const served = "def456-20260927T120000";
    assert.equal(buildReloadDecision(base({ lastAttempt: { to: served, at: NOW - 60_000 } })), "gave-up");
    assert.equal(buildReloadDecision(base({ lastAttempt: { to: served, at: NOW - RETRY_GUARD_MS - 1 } })), "reload");
    assert.equal(buildReloadDecision(base({ lastAttempt: { to: "older-build", at: NOW - 60_000 } })), "reload");
  });
});

// 2026-09-27 review: the composer was the only thing that held a reload,
// so text written anywhere else (a new document, a goal, a wish), a video
// on /screen or the hub, or a stream embedded there was lost or restarted
// once nobody had touched the page for a while. The page's own state now
// counts on every surface.
describe("the page's own work holds a reload", () => {
  it("on every surface", () => {
    assert.equal(buildReloadDecision(base({ busy: { page: true } })), "busy");
    assert.equal(buildReloadDecision(base({ surface: "display", busy: { page: true } })), "busy");
  });
});

describe("pageHoldsWork", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });
  const add = <K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string> = {}): HTMLElementTagNameMap[K] => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    document.body.appendChild(el);
    return el;
  };

  it("an empty page holds nothing", () => {
    add("p").textContent = "Today";
    assert.equal(pageHoldsWork(document), false);
  });

  it("a textarea with something written in it, focused or not (iOS drops focus when the PWA leaves)", () => {
    const t = add("textarea");
    t.value = "Biscuit — med protocol\n\n06:00 …";
    assert.equal(pageHoldsWork(document), true);
    t.value = "   ";
    assert.equal(pageHoldsWork(document), false, "only whitespace");
    t.value = "notes";
    t.readOnly = true;
    assert.equal(pageHoldsWork(document), false, "read-only is not being written");
    t.readOnly = false;
    t.disabled = true;
    assert.equal(pageHoldsWork(document), false, "disabled is not being written");
  });

  it("a one-line field only while it has focus: a filled-in settings field at rest is not unsaved work", () => {
    const i = add("input", { type: "text" });
    i.value = "Watch the garage camera";
    assert.equal(pageHoldsWork(document), false);
    i.focus();
    assert.equal(pageHoldsWork(document), true);
    i.value = "";
    assert.equal(pageHoldsWork(document), false);
    const box = add("input", { type: "checkbox" });
    box.focus();
    assert.equal(pageHoldsWork(document), false, "not a text field");
  });

  it("a focused contenteditable with text", () => {
    const d = add("div", { contenteditable: "true", tabindex: "0" });
    d.textContent = "draft";
    d.focus();
    // jsdom does not implement isContentEditable; the attribute decides.
    assert.equal(pageHoldsWork(document), true);
  });

  it("a video or audio that is playing; not one paused or finished", () => {
    const v = add("video");
    assert.equal(pageHoldsWork(document), false, "jsdom's media are paused");
    Object.defineProperty(v, "paused", { configurable: true, value: false });
    assert.equal(pageHoldsWork(document), true);
    Object.defineProperty(v, "ended", { configurable: true, value: true });
    assert.equal(pageHoldsWork(document), false);
    const a = add("audio");
    Object.defineProperty(a, "paused", { configurable: true, value: false });
    assert.equal(pageHoldsWork(document), true);
  });

  // Pre-deploy review (2026-09-27): a /screen or hub video show stays up
  // until cleared. Once it had ended (or someone paused it) it no longer
  // held a reload, the page reloaded after 30 s idle, GET /api/screen
  // returned the same show and autoPlay started the clip over, with sound
  // in Ellis's Chrome (--autoplay-policy=no-user-gesture-required). A media
  // element that plays by itself holds a reload for as long as it is there.
  it("an autoplay video or audio holds even paused or finished: a reload would start it again", () => {
    const v = add("video", { autoplay: "" });
    Object.defineProperty(v, "ended", { configurable: true, value: true });
    assert.equal(pageHoldsWork(document), true, "finished");
    Object.defineProperty(v, "ended", { configurable: true, value: false });
    assert.equal(pageHoldsWork(document), true, "paused");
    v.remove();
    add("audio", { autoplay: "" });
    assert.equal(pageHoldsWork(document), true);
  });

  it("a voice memo's player at rest is not work (it never plays by itself)", () => {
    add("audio", { preload: "metadata", class: "hidden" });
    assert.equal(pageHoldsWork(document), false);
  });

  it("an embedded player (an iframe from elsewhere) — never a tile drawn from srcdoc", () => {
    const tile = add("iframe", { srcdoc: "<p>scene</p>", sandbox: "allow-scripts" });
    assert.equal(pageHoldsWork(document), false);
    tile.remove();
    add("iframe", { src: "https://www.youtube.com/embed/abc?autoplay=1", allow: "autoplay; encrypted-media; fullscreen" });
    assert.equal(pageHoldsWork(document), true);
  });

  it("an empty embed src is not a player", () => {
    add("iframe", { src: "" });
    assert.equal(pageHoldsWork(document), false);
  });
});

describe("parseServedBuildId", () => {
  it("reads the route's body and nothing else", () => {
    assert.equal(parseServedBuildId({ build_id: "def456-20260927T120000" }), "def456-20260927T120000");
    assert.equal(parseServedBuildId({ build_id: "  " }), null);
    assert.equal(parseServedBuildId({ build_id: 42 }), null);
    assert.equal(parseServedBuildId(null), null);
    assert.equal(parseServedBuildId("<html>"), null);
  });
});
