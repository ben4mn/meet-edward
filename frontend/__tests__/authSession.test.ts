import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  shouldLeaveLogin,
  loginUrlFor,
  readAuthHint,
  writeAuthHint,
  authVerdict,
  authRetryDelayMs,
  clearSessionContent,
  SIGNED_OUT_EVENT,
} from "../lib/authSession";
import { MIND_NOW_CACHE_KEY } from "../lib/presenceModel";

/**
 * 2026-09-21: the login page redirected as soon as `isAuthenticated` was
 * true — which the optimistic localStorage hint makes it before the server
 * has answered. With a dead cookie that is a ping-pong: /login → /chat →
 * 401 → /login → … The page now waits for the server's verdict, and a 401
 * clears the hint so the next mount does not start optimistic again.
 */
describe("shouldLeaveLogin", () => {
  it("waits for the server's verdict, not the optimistic hint", () => {
    assert.equal(shouldLeaveLogin({ isLoading: false, isAuthenticated: true, authChecked: false }), false);
    assert.equal(shouldLeaveLogin({ isLoading: false, isAuthenticated: true, authChecked: true }), true);
  });
  it("never leaves while loading or unauthenticated", () => {
    assert.equal(shouldLeaveLogin({ isLoading: true, isAuthenticated: true, authChecked: true }), false);
    assert.equal(shouldLeaveLogin({ isLoading: false, isAuthenticated: false, authChecked: true }), false);
  });
  it("never leaves while the password still needs setting (2026-09-26)", () => {
    // The settings row lost its password_hash (a restore) while the cookie
    // still verifies: {configured:false, authenticated:true}. AuthGuard sends
    // setup to /login; leaving again made /login ↔ /today loop forever and
    // the "Choose a password" form never stayed up.
    assert.equal(
      shouldLeaveLogin({ isLoading: false, isAuthenticated: true, authChecked: true, needsSetup: true }),
      false,
    );
    assert.equal(
      shouldLeaveLogin({ isLoading: false, isAuthenticated: true, authChecked: true, needsSetup: false }),
      true,
    );
  });
});

describe("authVerdict (2026-09-26: a backend blip is not a sign-out)", () => {
  it("is a verdict only for a real answer", () => {
    assert.deepEqual(authVerdict({ configured: true, authenticated: true }), { configured: true, authenticated: true });
    assert.deepEqual(authVerdict({ configured: true, authenticated: false }), { configured: true, authenticated: false });
    assert.deepEqual(authVerdict({ configured: false, authenticated: false }), { configured: false, authenticated: false });
  });
  it("anything else means the server was unreachable (null)", () => {
    assert.equal(authVerdict(null), null);
    assert.equal(authVerdict("Internal Server Error"), null);
    assert.equal(authVerdict({ detail: "boom" }), null);
    assert.equal(authVerdict({ authenticated: "yes" }), null);
  });
  it("an answer without `configured` is taken as configured", () => {
    assert.deepEqual(authVerdict({ authenticated: true }), { configured: true, authenticated: true });
  });
  it("carries setup_code_required when the server sends it", () => {
    assert.deepEqual(authVerdict({ configured: false, authenticated: false, setup_code_required: true }), {
      configured: false,
      authenticated: false,
      setup_code_required: true,
    });
    assert.deepEqual(authVerdict({ configured: true, authenticated: true, setup_code_required: "yes" }), {
      configured: true,
      authenticated: true,
    });
  });
});

describe("authRetryDelayMs", () => {
  it("backs off 1, 2, 4, 8 … seconds, never more than 30", () => {
    assert.deepEqual([0, 1, 2, 3, 4, 5, 9].map((n) => authRetryDelayMs(n)), [1000, 2000, 4000, 8000, 16000, 30000, 30000]);
  });
});

describe("clearSessionContent (sign-out leaves nothing of Ellis's behind)", () => {
  function store(entries: Record<string, string>) {
    const m = new Map(Object.entries(entries));
    return {
      get length() {
        return m.size;
      },
      key: (i: number) => Array.from(m.keys())[i] ?? null,
      getItem: (k: string) => m.get(k) ?? null,
      setItem: (k: string, v: string) => void m.set(k, v),
      removeItem: (k: string) => void m.delete(k),
      keys: () => Array.from(m.keys()),
    };
  }
  it("drops the Today feed, drafts and the cached mind/now; keeps the rest", () => {
    const session = store({ "edward:today:v1": "{feed}", "edward:draft:c1": "hi", "edward:draft:new": "yo", other: "x" });
    const local = store({ [MIND_NOW_CACHE_KEY]: "{mind}", "edward-theme": "dark", edward_auth_hint: "1" });
    clearSessionContent(session, local);
    assert.deepEqual(session.keys(), ["other"]);
    assert.deepEqual(local.keys().sort(), ["edward-theme", "edward_auth_hint"]);
  });
  it("names the window event the feed listens to", () => {
    assert.equal(SIGNED_OUT_EVENT, "edward:signed-out");
  });
});

describe("loginUrlFor", () => {
  it("carries the page back as a redirect", () => {
    assert.equal(loginUrlFor("/screen", ""), "/login?redirect=%2Fscreen");
    assert.equal(loginUrlFor("/chat", "?c=abc"), "/login?redirect=%2Fchat%3Fc%3Dabc");
  });
  it("does not point at itself or the landing page", () => {
    assert.equal(loginUrlFor("/login", "?redirect=%2Fchat"), "/login");
    assert.equal(loginUrlFor("/", ""), "/login");
  });
});

describe("auth hint", () => {
  function memoryStorage() {
    const store = new Map<string, string>();
    return {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => { store.set(k, v); },
      removeItem: (k: string) => { store.delete(k); },
    };
  }
  it("round-trips and clears", () => {
    const storage = memoryStorage();
    assert.equal(readAuthHint(storage), false);
    writeAuthHint(storage, true);
    assert.equal(readAuthHint(storage), true);
    writeAuthHint(storage, false);
    assert.equal(readAuthHint(storage), false);
  });
  it("tolerates a storage that throws (private mode, blocked site data)", () => {
    const bad = {
      getItem: () => { throw new Error("nope"); },
      setItem: () => { throw new Error("nope"); },
      removeItem: () => { throw new Error("nope"); },
    };
    assert.equal(readAuthHint(bad), false);
    assert.doesNotThrow(() => writeAuthHint(bad, true));
  });
  it("reads nothing when there is no storage", () => {
    assert.equal(readAuthHint(null), false);
    assert.doesNotThrow(() => writeAuthHint(null, true));
  });
});
