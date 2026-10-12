// R/G red for lib/loginFlow.ts — Edward's arrival at the door.
//
// - Signed in, the front door is Today (not chat); a redirect is honoured
//   only when it is a safe same-site path that is not the login page itself.
// - He looks away while a hidden password is typed, peeks when it is shown.
// - Errors in his voice: what happened and what to do.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  loginRedirectTarget,
  loginGaze,
  loginErrorCopy,
  FRONT_DOOR,
  SETUP_CODE_HINT,
  asksForSetupCode,
  isSetupCodeRefusal,
} from "../lib/loginFlow.ts";

describe("loginRedirectTarget", () => {
  it("defaults to the front door, Today", () => {
    assert.equal(FRONT_DOOR, "/today");
    assert.equal(loginRedirectTarget(null), "/today");
    assert.equal(loginRedirectTarget(""), "/today");
  });
  it("honours a same-site path", () => {
    assert.equal(loginRedirectTarget("/chat?c=abc"), "/chat?c=abc");
    assert.equal(loginRedirectTarget("/settings/thoughts"), "/settings/thoughts");
  });
  it("refuses open redirects and loops", () => {
    assert.equal(loginRedirectTarget("//evil.example"), "/today");
    assert.equal(loginRedirectTarget("https://evil.example"), "/today");
    assert.equal(loginRedirectTarget("/login"), "/today");
    assert.equal(loginRedirectTarget("/login?redirect=/x"), "/today");
    assert.equal(loginRedirectTarget("/"), "/today");
    assert.equal(loginRedirectTarget("/\\evil.example"), "/today");
  });

  it("refuses control characters and backslashes the URL parser drops or folds (2026-09-26)", () => {
    // new URL("/\t/evil.com", "https://edward.example.com/login").href === "https://evil.com/"
    for (const r of ["/\t/evil.example", "/\n/evil.example", "/\r/evil.example", "/\t\\evil.example", "/x\\y", "/./login", "/%2e/login"]) {
      assert.equal(loginRedirectTarget(r), "/today", JSON.stringify(r));
    }
  });

  it("keeps an ordinary same-site page, query and hash", () => {
    assert.equal(loginRedirectTarget("/chat?c=abc"), "/chat?c=abc");
    assert.equal(loginRedirectTarget("/settings/thoughts"), "/settings/thoughts");
    assert.equal(loginRedirectTarget("/today#needs"), "/today#needs");
  });
});

describe("loginGaze", () => {
  it("looks away while a hidden password is being typed", () => {
    assert.equal(loginGaze({ reveal: false, hasText: true }), "up");
  });
  it("peeks down at it when it is shown", () => {
    assert.equal(loginGaze({ reveal: true, hasText: true }), "down");
  });
  it("looks where he likes when the field is empty", () => {
    assert.equal(loginGaze({ reveal: false, hasText: false }), null);
    assert.equal(loginGaze({ reveal: true, hasText: false }), null);
  });
});

describe("loginErrorCopy", () => {
  it("a wrong password", () => {
    assert.equal(loginErrorCopy("Invalid password"), "That's not it. Try again?");
    assert.equal(loginErrorCopy("Login failed"), "That's not it. Try again?");
  });
  it("the Mac is unreachable", () => {
    assert.equal(loginErrorCopy("Backend unavailable — is the server running?"), "I can't reach the Mac right now. Try again in a moment.");
    assert.equal(loginErrorCopy("Failed to fetch"), "I can't reach the Mac right now. Try again in a moment.");
  });
  it("too many tries", () => {
    assert.equal(loginErrorCopy("Too many login attempts"), "Too many tries. Give it a minute.");
  });
});

// 2026-10-12 review: a refused first password read "That's not it. Try
// again?" — as if the password were wrong — when the server said why.
const SETUP_CODE_WRONG = "That setup code doesn't match. On the Mac Edward runs on: cat backend/.setup_code";
const SETUP_LOCAL_ONLY = "The first password can only be set on the Mac Edward runs on. Open http://localhost:3000 there.";
const SETUP_ALREADY_DONE = "A password is already set. Sign in with it.";

describe("loginErrorCopy while setting the first password", () => {
  it("shows the server's refusal as it is", () => {
    for (const refusal of [SETUP_CODE_WRONG, SETUP_LOCAL_ONLY, SETUP_ALREADY_DONE]) {
      assert.equal(loginErrorCopy(refusal, { setup: true }), refusal);
    }
  });
  it("never says the password is wrong", () => {
    assert.notEqual(loginErrorCopy("Failed to setup password", { setup: true }), "That's not it. Try again?");
    assert.notEqual(loginErrorCopy("", { setup: true }), "That's not it. Try again?");
  });
  it("still says when the Mac can't be reached or there were too many tries", () => {
    assert.equal(
      loginErrorCopy("Backend unavailable — is the server running?", { setup: true }),
      "I can't reach the Mac right now. Try again in a moment.",
    );
    assert.equal(loginErrorCopy("Too many attempts. Try again later.", { setup: true }), "Too many tries. Give it a minute.");
  });
  it("signing in is unchanged", () => {
    assert.equal(loginErrorCopy(SETUP_CODE_WRONG), "That's not it. Try again?");
    assert.equal(loginErrorCopy("Invalid password", { setup: false }), "That's not it. Try again?");
  });
});

describe("the setup code", () => {
  it("is asked for only while setting up, when the server requires it", () => {
    assert.equal(asksForSetupCode({ needsSetup: true, setupCodeRequired: true }), true);
    assert.equal(asksForSetupCode({ needsSetup: true, setupCodeRequired: false }), false);
    assert.equal(asksForSetupCode({ needsSetup: false, setupCodeRequired: true }), false);
  });
  it("says where it is", () => {
    assert.equal(SETUP_CODE_HINT, "It's in backend/.setup_code on the Mac Edward runs on.");
  });
  it("knows a code refusal from the rest", () => {
    assert.equal(isSetupCodeRefusal(SETUP_CODE_WRONG), true);
    assert.equal(isSetupCodeRefusal(SETUP_LOCAL_ONLY), false);
    assert.equal(isSetupCodeRefusal(null), false);
  });
});
