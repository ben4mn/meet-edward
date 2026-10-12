// R/G red for sharing "who is logging" across components.
//
// The bug: useWho was a plain useState per component, so tapping Rowan
// in the toggle updated the toggle's copy but not HubRoot's. The panel
// only changed colour after navigating to another screen — which is worse
// than no theming, because the header claimed one person while the pill
// claimed the other.
//
// The household comes from the server (GET /api/hub/who → `people`,
// EDWARD_HOUSEHOLD, owner first); each case starts from a known one.

import { describe, it, beforeEach, after } from "node:test";
import assert from "node:assert/strict";

import {
  getPeople,
  getWho,
  setPeople,
  setWho,
  subscribeWho,
  resetWhoForTests,
} from "../app/(display)/hub/lib/whoStore.ts";
import { postHubWho } from "../app/(display)/hub/lib/hubApi.ts";

function clearStorage(): void {
  try {
    window.localStorage.clear();
  } catch {
    /* no storage in this environment */
  }
}

beforeEach(() => {
  // The store remembers the choice on the panel; a previous case's pick
  // must not leak into this one.
  clearStorage();
  resetWhoForTests();
  setPeople(["Ellis", "Rowan"]);
});

describe("whoStore", () => {
  it("starts on the first person", () => {
    assert.deepEqual([...getPeople()], ["Ellis", "Rowan"]);
    assert.equal(getWho(), getPeople()[0]);
    assert.equal(getWho(), "Ellis");
  });

  it("setWho changes the value", () => {
    setWho("Rowan");
    assert.equal(getWho(), "Rowan");
  });

  it("notifies every subscriber, which is the whole point", () => {
    let a = 0;
    let b = 0;
    subscribeWho(() => a++);
    subscribeWho(() => b++);

    setWho("Rowan");

    assert.equal(a, 1, "first subscriber (e.g. the toggle) missed the change");
    assert.equal(b, 1, "second subscriber (e.g. HubRoot) missed the change");
  });

  it("stops notifying after unsubscribe", () => {
    let calls = 0;
    const off = subscribeWho(() => calls++);
    setWho("Rowan");
    off();
    setWho("Ellis");
    assert.equal(calls, 1);
  });

  it("ignores an unknown person rather than theming to nothing", () => {
    setWho("Rowan");
    setWho("Nobody");
    assert.equal(getWho(), "Rowan");
  });

  it("does not notify when the value is unchanged", () => {
    let calls = 0;
    subscribeWho(() => calls++);
    setWho("Ellis"); // already Ellis
    assert.equal(calls, 0, "a no-op set should not churn every subscriber");
  });

  it("getWho is a stable reference between reads, for useSyncExternalStore", () => {
    // getSnapshot must not return a fresh object each call or React loops.
    assert.equal(getWho(), getWho());
    assert.equal(getPeople(), getPeople());
  });

  // 2026-09-24 (plan Phase 7): the hub tells Edward who is at it, but only
  // when the choice actually changes — a no-op tap must not post.
  it("setWho reports whether the choice changed", () => {
    assert.equal(setWho("Rowan"), true);
    assert.equal(setWho("Rowan"), false, "same person again is not a change");
    assert.equal(setWho("Nobody"), false, "an unknown person is not a change");
    assert.equal(setWho("Ellis"), true);
  });

  it("before the household is known there is nobody to pick", () => {
    resetWhoForTests();
    assert.deepEqual([...getPeople()], []);
    assert.equal(getWho(), "");
    assert.equal(setWho("Ellis"), false, "nobody lives here yet");
    assert.equal(getWho(), "");
  });

  it("a bad answer never empties a known household", () => {
    assert.equal(setPeople([]), false);
    assert.equal(setPeople("Ellis"), false);
    assert.equal(setPeople([42, "  "]), false);
    assert.deepEqual([...getPeople()], ["Ellis", "Rowan"]);
    assert.equal(getWho(), "Ellis");
  });
});

describe("postHubWho", () => {
  const origFetch = globalThis.fetch;

  after(() => {
    globalThis.fetch = origFetch;
  });

  it("posts the person to /api/hub/who", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response("{}", { status: 200 });
    }) as typeof fetch;

    await postHubWho("Rowan");

    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/api/hub/who");
    assert.equal(calls[0].init?.method, "POST");
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { person: "Rowan" });
  });

  it("never throws: an offline kiosk still changes theme", async () => {
    globalThis.fetch = (async () => {
      throw new Error("network down");
    }) as typeof fetch;
    await postHubWho("Ellis");

    globalThis.fetch = (async () => new Response("nope", { status: 500 })) as typeof fetch;
    await postHubWho("Ellis");
  });
});
