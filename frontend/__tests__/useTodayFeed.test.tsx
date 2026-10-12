// The Today feed hook (review 2026-09-26, today-inflight-refresh-resurrects-card
// and today-stale-cache-actionable): a refresh that was already running when
// Ellis acted cannot put the card back, a load after a change is a new request,
// and module memory is held to the same 24 h as sessionStorage.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, renderHook } from "@testing-library/react";

import { TODAY_CACHE_MAX_AGE_MS } from "../components/today/todayFeedState.ts";
import { __setTodayMemoryForTests, useTodayFeed } from "../components/today/useTodayFeed.ts";
import { afterAction } from "../components/today/todayModel.ts";

afterEach(() => {
  cleanup();
  __setTodayMemoryForTests(null);
  try {
    window.sessionStorage.clear();
  } catch {
    /* none */
  }
});

const withNeeds = (...ids: string[]) => ({ generated_at: "g", needs: ids.map((id) => ({ id, kind: "dose", title: id })) });

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

const flush = () => act(async () => new Promise((r) => setTimeout(r, 0)));

describe("useTodayFeed", () => {
  it("a load already running when Ellis acts cannot bring the card back", async () => {
    const gets: ReturnType<typeof deferred<unknown>>[] = [];
    const fetchFeed = () => {
      const d = deferred<unknown>();
      gets.push(d);
      return d.promise;
    };
    const { result } = renderHook(() => useTodayFeed(fetchFeed));
    // First load answers.
    await act(async () => {
      gets[0].resolve(withNeeds("dose:a", "dose:b"));
    });
    assert.deepEqual(result.current.state.feed?.needs.map((n) => n.id), ["dose:a", "dose:b"]);
    // A poll starts (its snapshot still has dose:a) …
    act(() => void result.current.load());
    assert.equal(gets.length, 2);
    // … Ellis logs dose:a, the page patches it out …
    act(() => result.current.patch((f) => ({ ...f, needs: afterAction(f.needs, "dose:a") })));
    // … and the old poll lands.
    await act(async () => {
      gets[1].resolve(withNeeds("dose:a", "dose:b"));
    });
    await flush();
    assert.deepEqual(result.current.state.feed?.needs.map((n) => n.id), ["dose:b"], "the stale snapshot is dropped");
    assert.equal(result.current.state.refreshing, false);
    // A load after the change is a new request, not the stale one.
    act(() => void result.current.load());
    assert.equal(gets.length, 3);
    await act(async () => {
      gets[2].resolve(withNeeds("dose:b"));
    });
    assert.deepEqual(result.current.state.feed?.needs.map((n) => n.id), ["dose:b"]);
  });

  it("load() hands back the feed it applied", async () => {
    const { result } = renderHook(() => useTodayFeed(async () => withNeeds("x")));
    let got: unknown;
    await act(async () => {
      got = await result.current.load();
    });
    assert.deepEqual((got as { needs: { id: string }[] }).needs.map((n) => n.id), ["x"]);
    assert.equal(result.current.state.confirmed, true);
  });

  it("never paints a remembered feed older than a day", async () => {
    __setTodayMemoryForTests({
      feed: { ...(withNeeds("dose:yesterday") as never), overflow: { note: 0, heartbeat: 0 } } as never,
      at: Date.now() - TODAY_CACHE_MAX_AGE_MS - 60_000,
      scrollTop: 0,
    });
    const { result } = renderHook(() => useTodayFeed(() => new Promise(() => {})));
    assert.equal(result.current.state.feed, null, "a day-old feed does not paint");
  });
});

// Review 2026-09-26 (logout-keeps-today-cache): signing out forgets Today —
// module memory and sessionStorage — and neither a load still in flight nor
// the page unmounting on its way to /login writes it back.
import { SIGNED_OUT_EVENT } from "../lib/authSession.ts";
import { TODAY_CACHE_KEY } from "../components/today/todayFeedState.ts";

describe("signing out", () => {
  it("clears the cached feed, and nothing writes it back", async () => {
    const gets: ReturnType<typeof deferred<unknown>>[] = [];
    const fetchFeed = () => {
      const d = deferred<unknown>();
      gets.push(d);
      return d.promise;
    };
    const { result, unmount } = renderHook(() => useTodayFeed(fetchFeed));
    await act(async () => {
      gets[0].resolve(withNeeds("dose:biscuit"));
    });
    assert.notEqual(window.sessionStorage.getItem(TODAY_CACHE_KEY), null, "the feed was cached");
    // A poll is in flight when Ellis signs out.
    act(() => void result.current.load());
    act(() => {
      window.dispatchEvent(new window.Event(SIGNED_OUT_EVENT));
    });
    assert.equal(window.sessionStorage.getItem(TODAY_CACHE_KEY), null, "sign-out removed it");
    await act(async () => {
      gets[1].resolve(withNeeds("dose:biscuit", "email:jordan"));
    });
    await flush();
    assert.equal(window.sessionStorage.getItem(TODAY_CACHE_KEY), null, "the late load did not write it back");
    unmount();
    assert.equal(window.sessionStorage.getItem(TODAY_CACHE_KEY), null, "leaving the page did not write it back");
    // The next Today paints nothing from memory.
    const next = renderHook(() => useTodayFeed(() => new Promise(() => {})));
    assert.equal(next.result.current.state.feed, null);
  });
});
