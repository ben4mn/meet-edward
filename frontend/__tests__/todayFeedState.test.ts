// Today paints instantly from the last feed it saw, revalidates in the
// background, and fails calmly (UX pass 2026-09-26): a cold start shows
// skeletons, a failed first load one card in Edward's voice with a backoff
// retry, and a failed refresh keeps the old feed with a quiet note.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  NEW_FEED_STATE,
  TODAY_CACHE_KEY,
  TODAY_CACHE_MAX_AGE_MS,
  feedPhase,
  readTodayCache,
  reduceFeed,
  retryDelayMs,
  staleNote,
  writeTodayCache,
} from "../components/today/todayFeedState.ts";
import { normalizeTodayFeed } from "../components/today/todayModel.ts";

const feed = normalizeTodayFeed({ generated_at: "2026-09-26T23:00:00Z", needs: [{ id: "n1", title: "t" }] });
const now = 1_790_000_000_000;

function memoryStorage(init: Record<string, string> = {}) {
  const data = new Map(Object.entries(init));
  return {
    getItem: (k: string) => (data.has(k) ? (data.get(k) as string) : null),
    setItem: (k: string, v: string) => void data.set(k, v),
    data,
  };
}

describe("the cache", () => {
  it("round-trips the feed, when it was fetched and where Ellis had scrolled", () => {
    const s = memoryStorage();
    writeTodayCache(s, { feed, at: now - 60_000, scrollTop: 420 });
    const back = readTodayCache(s, now);
    assert.equal(back?.feed.needs[0].id, "n1");
    assert.equal(back?.at, now - 60_000);
    assert.equal(back?.scrollTop, 420);
    assert.ok(s.data.has(TODAY_CACHE_KEY));
  });
  it("ignores a feed too old to be honest, junk, and missing storage", () => {
    const s = memoryStorage();
    writeTodayCache(s, { feed, at: now - TODAY_CACHE_MAX_AGE_MS - 1, scrollTop: 0 });
    assert.equal(readTodayCache(s, now), null);
    assert.equal(readTodayCache(memoryStorage({ [TODAY_CACHE_KEY]: "{not json" }), now), null);
    assert.equal(readTodayCache(null, now), null);
  });
  it("never throws when storage does", () => {
    const angry = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("full");
      },
    };
    assert.equal(readTodayCache(angry, now), null);
    writeTodayCache(angry, { feed, at: now, scrollTop: 0 });
  });
});

describe("retryDelayMs", () => {
  it("backs off from 2 s to a minute", () => {
    assert.deepEqual([1, 2, 3, 4, 5, 6, 7, 12].map(retryDelayMs), [2000, 4000, 8000, 16000, 32000, 60000, 60000, 60000]);
    assert.equal(retryDelayMs(0), 2000);
  });
});

describe("staleNote", () => {
  it("says it could not refresh and how old what shows is", () => {
    assert.equal(staleNote(now - 20_000, now), "Couldn't refresh · showing just now");
    assert.equal(staleNote(now - 3 * 60_000, now), "Couldn't refresh · showing 3 min ago");
    assert.equal(staleNote(now - 2 * 3600_000, now), "Couldn't refresh · showing 2 h ago");
  });
});

describe("reduceFeed / feedPhase", () => {
  it("cold until the first answer", () => {
    assert.equal(feedPhase(NEW_FEED_STATE), "cold");
  });
  it("a cached feed paints at once and is replaced by the live one", () => {
    let s = reduceFeed(NEW_FEED_STATE, { type: "cached", feed, at: now - 60_000 });
    assert.equal(feedPhase(s), "ready");
    assert.equal(s.okAt, now - 60_000);
    s = reduceFeed(s, { type: "ok", feed: { ...feed, generated_at: "later" }, at: now });
    assert.equal(s.feed?.generated_at, "later");
    assert.equal(s.okAt, now);
  });
  it("a cached copy never replaces a live one", () => {
    let s = reduceFeed(NEW_FEED_STATE, { type: "ok", feed, at: now });
    s = reduceFeed(s, { type: "cached", feed: { ...feed, generated_at: "old" }, at: now - 1 });
    assert.equal(s.feed?.generated_at, feed.generated_at);
  });
  it("a failed first load is an error, a failed refresh is stale", () => {
    const failed = reduceFeed(NEW_FEED_STATE, { type: "fail" });
    assert.equal(feedPhase(failed), "error");
    assert.equal(failed.failures, 1);
    const stale = reduceFeed(reduceFeed(NEW_FEED_STATE, { type: "ok", feed, at: now }), { type: "fail" });
    assert.equal(feedPhase(stale), "stale");
    assert.equal(stale.feed?.needs.length, 1, "the old feed stays on screen");
  });
  it("success clears the failures", () => {
    const s = reduceFeed(reduceFeed(NEW_FEED_STATE, { type: "fail" }), { type: "ok", feed, at: now });
    assert.equal(s.failures, 0);
    assert.equal(feedPhase(s), "ready");
  });
  it("tracks a refresh in flight", () => {
    const s = reduceFeed(NEW_FEED_STATE, { type: "start" });
    assert.equal(s.refreshing, true);
    assert.equal(reduceFeed(s, { type: "fail" }).refreshing, false);
  });
});

// ---------------------------------------------------------------------------
// Review 2026-09-26: a card Ellis resolved must not come back from a refresh
// that was already running (today-inflight-refresh-resurrects-card), and a
// feed painted from the cache is not trusted for an action until the
// network has confirmed it (today-stale-cache-actionable).
// ---------------------------------------------------------------------------
import {
  RESOLVED_HOLD_MS,
  RESUME_STALE_MS,
  pickCache,
  shouldApply,
  stillHidden,
  unconfirmedAfterSleep,
} from "../components/today/todayFeedState.ts";

describe("confirmed", () => {
  it("a cached feed is unconfirmed until the network answers", () => {
    const cached = reduceFeed(NEW_FEED_STATE, { type: "cached", feed, at: now - 60_000 });
    assert.equal(cached.confirmed, false);
    assert.equal(feedPhase(cached), "ready", "it still paints as ready: the gate is on actions, not on the page");
    const ok = reduceFeed(cached, { type: "ok", feed, at: now });
    assert.equal(ok.confirmed, true);
    assert.equal(reduceFeed(ok, { type: "patch", feed }).confirmed, true);
  });
  it("coming back after a long sleep unconfirms what is on screen", () => {
    const ok = reduceFeed(NEW_FEED_STATE, { type: "ok", feed, at: now });
    const resumed = reduceFeed(ok, { type: "resume" });
    assert.equal(resumed.confirmed, false);
    assert.equal(resumed.feed, ok.feed, "the feed stays on screen");
    assert.equal(unconfirmedAfterSleep(now, now + RESUME_STALE_MS + 1), true);
    assert.equal(unconfirmedAfterSleep(now, now + 30_000), false);
    assert.equal(unconfirmedAfterSleep(null, now), false);
  });
});

describe("a superseded load", () => {
  it("applies only when nothing changed since it started", () => {
    assert.equal(shouldApply(3, 3), true);
    assert.equal(shouldApply(2, 3), false);
  });
  it("is dropped: the refresh ends and the feed stays as patched", () => {
    const patched = reduceFeed(reduceFeed(reduceFeed(NEW_FEED_STATE, { type: "ok", feed, at: now }), { type: "start" }), {
      type: "patch",
      feed: { ...feed, needs: [] },
    });
    const dropped = reduceFeed(patched, { type: "dropped" });
    assert.equal(dropped.refreshing, false);
    assert.equal(dropped.feed?.needs.length, 0);
    assert.equal(dropped.failures, 0);
  });
});

describe("pickCache", () => {
  const entry = (at: number) => ({ feed, at, scrollTop: 0 });
  it("prefers this tab's memory, but never one too old to be honest", () => {
    assert.equal(pickCache(entry(now - 1000), () => entry(now - 5000), now)?.at, now - 1000);
    assert.equal(pickCache(entry(now - TODAY_CACHE_MAX_AGE_MS - 1), () => entry(now - 5000), now)?.at, now - 5000);
    assert.equal(pickCache(entry(now - TODAY_CACHE_MAX_AGE_MS - 1), () => null, now), null);
    assert.equal(pickCache(null, () => null, now), null);
  });
});

describe("stillHidden", () => {
  const ids = (...x: string[]) => new Set(x);
  it("keeps a card hidden while its action is in flight or waiting out its Undo", () => {
    const h = new Map([["a", Infinity]]);
    assert.equal(stillHidden(h, ids("a"), now + 999_999, now + 999_999).has("a"), true);
  });
  it("keeps a resolved card hidden until a feed fetched after it no longer has it", () => {
    const h = new Map([["a", now]]);
    assert.equal(stillHidden(h, ids("a"), now - 1, now + 1000).has("a"), true, "a feed from before the action cannot bring it back");
    assert.equal(stillHidden(h, ids("a"), now + 500, now + 1000).has("a"), true, "the server still has it: wait (it may be catching up)");
    assert.equal(stillHidden(h, ids(), now + 500, now + 1000).has("a"), false, "gone from a later feed: done");
  });
  it("lets the server win after a while: a card it still has comes back", () => {
    const h = new Map([["a", now]]);
    assert.equal(stillHidden(h, ids("a"), now + 500, now + RESOLVED_HOLD_MS + 1).has("a"), false);
  });
  it("returns the same map when nothing changed (no re-render loop)", () => {
    const h = new Map([["a", now]]);
    assert.equal(stillHidden(h, ids("a"), now - 1, now + 1000), h);
  });
});
