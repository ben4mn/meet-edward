// R/G red (2026-09-26 review): the door holds up.
//
//  - A backend blip at app open (uvicorn restarting behind a live Next, a
//    500 from the tunnel) is "unreachable", not "signed out": the hint is
//    kept, the server's verdict is still awaited (authChecked stays false),
//    and the provider retries with backoff — on /login too, so the form
//    leaves by itself once the server says the session is fine.
//  - With a verified cookie but no password set, /login stays on the
//    setup form instead of bouncing to /today and back forever.
//  - Signing out that fails says so and keeps Ellis signed in; signing out
//    that works leaves none of his content in the tab.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { AuthProvider, useAuth } from "../lib/AuthContext.tsx";
import { AuthGuard } from "../components/auth/AuthGuard.tsx";
import { SIGNED_OUT_EVENT } from "../lib/authSession.ts";
import { MIND_NOW_CACHE_KEY } from "../lib/presenceModel.ts";
import { ToastProvider } from "../components/ui/Toast.tsx";
import { SignOutSheet } from "../components/layout/SignOutSheet.tsx";
import { signOutStore } from "../components/layout/shellStores.ts";
import LoginPage from "../app/(auth)/login/page.tsx";

const originalFetch = globalThis.fetch;
let statusReplies: Array<() => Response> = [];
let statusCalls = 0;
let logoutReply: () => Response = () => new Response(null, { status: 200 });

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
const down = () => new Response("Internal Server Error", { status: 500 });
const ok = (authenticated = true, configured = true) => () => json({ configured, authenticated });

const pushes: string[] = [];
const router = {
  push: (u: string) => void pushes.push(u),
  replace: () => {},
  back: () => {},
  forward: () => {},
  refresh: () => {},
  prefetch: () => {},
};

function Frame({ path = "/today", query = "", children }: { path?: string; query?: string; children: React.ReactNode }) {
  return (
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value={path}>
        <SearchParamsContext.Provider value={new URLSearchParams(query)}>
          <ToastProvider>{children}</ToastProvider>
        </SearchParamsContext.Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>
  );
}

let auth: ReturnType<typeof useAuth>;
function Probe() {
  auth = useAuth();
  return null;
}

const FAST = (n: number) => 30 * (n + 1);

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function flush(ms = 40) {
  await act(async () => {
    await tick(ms);
  });
}

function hint(on: boolean) {
  if (on) window.localStorage.setItem("edward_auth_hint", "1");
  else window.localStorage.removeItem("edward_auth_hint");
}

beforeEach(() => {
  statusCalls = 0;
  statusReplies = [];
  pushes.length = 0;
  logoutReply = () => new Response(null, { status: 200 });
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (path.startsWith("/api/auth/status")) {
      const reply = statusReplies[Math.min(statusCalls, statusReplies.length - 1)] ?? ok();
      statusCalls += 1;
      return reply();
    }
    if (path.startsWith("/api/auth/logout") && init?.method === "POST") return logoutReply();
    return json({});
  }) as typeof fetch;
});

afterEach(async () => {
  cleanup();
  act(() => signOutStore.close());
  await tick(20);
  globalThis.fetch = originalFetch;
  hint(false);
});

describe("a backend blip at app open", () => {
  it("keeps the hint, waits for a real verdict, and retries until the server answers", async () => {
    hint(true);
    statusReplies = [down, down, ok()];
    render(
      <Frame>
        <AuthProvider retryDelayMs={FAST}>
          <Probe />
        </AuthProvider>
      </Frame>,
    );
    await flush(10);
    assert.equal(auth.isAuthenticated, true, "the hint stands");
    assert.equal(auth.authChecked, false, "no verdict yet");
    assert.equal(auth.unreachable, true);
    assert.equal(window.localStorage.getItem("edward_auth_hint"), "1", "a 500 never clears the hint");
    await flush(200);
    assert.equal(statusCalls, 3, "retried with backoff");
    assert.equal(auth.authChecked, true);
    assert.equal(auth.unreachable, false);
    assert.equal(auth.isAuthenticated, true);
  });

  it("with the hint, the app renders (presence will say Offline) instead of sending Ellis to sign in", async () => {
    hint(true);
    statusReplies = [down];
    render(
      <Frame>
        <AuthProvider retryDelayMs={() => 60_000}>
          <AuthGuard>
            <p>the app</p>
          </AuthGuard>
        </AuthProvider>
      </Frame>,
    );
    await flush();
    assert.ok(screen.getByText("the app"));
    assert.deepEqual(pushes, []);
  });

  it("without the hint, it waits and says it can't reach Edward — never /login", async () => {
    hint(false);
    statusReplies = [down];
    render(
      <Frame>
        <AuthProvider retryDelayMs={() => 60_000}>
          <AuthGuard>
            <p>the app</p>
          </AuthGuard>
        </AuthProvider>
      </Frame>,
    );
    await flush();
    assert.deepEqual(pushes, []);
    assert.match(screen.getByRole("status").textContent ?? "", /can.t reach/i);
  });

  it("a real 'signed out' still goes to /login", async () => {
    hint(true);
    statusReplies = [ok(false)];
    render(
      <Frame>
        <AuthProvider retryDelayMs={FAST}>
          <AuthGuard>
            <p>the app</p>
          </AuthGuard>
        </AuthProvider>
      </Frame>,
    );
    await flush();
    assert.equal(pushes.length, 1);
    assert.match(pushes[0], /^\/login/);
  });
});

describe("the login page", () => {
  it("stays on the setup form when the cookie verifies but no password is set", async () => {
    statusReplies = [ok(true, false)];
    render(
      <Frame path="/login" query="redirect=%2Ftoday">
        <AuthProvider retryDelayMs={FAST}>
          <LoginPage />
        </AuthProvider>
      </Frame>,
    );
    await flush(80);
    assert.deepEqual(pushes, [], "no bounce back to /today");
    assert.ok(screen.getByText(/Choose a password/));
  });

  it("while the Mac is unreachable it says so, then leaves by itself once the session checks out", async () => {
    statusReplies = [down, ok()];
    render(
      <Frame path="/login" query="redirect=%2Fchat">
        <AuthProvider retryDelayMs={FAST}>
          <LoginPage />
        </AuthProvider>
      </Frame>,
    );
    await flush(10);
    assert.match(document.body.textContent ?? "", /can.t reach the Mac/i);
    await flush(150);
    assert.deepEqual(pushes, ["/chat"]);
  });
});

describe("signing out", () => {
  function mountSignOut() {
    hint(true);
    render(
      <Frame>
        <AuthProvider retryDelayMs={FAST}>
          <Probe />
          <SignOutSheet />
        </AuthProvider>
      </Frame>,
    );
  }

  it("when the Mac doesn't answer, says so and keeps the sheet (and the session) as they were", async () => {
    logoutReply = down;
    mountSignOut();
    await flush();
    act(() => signOutStore.open());
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await flush(60);
    assert.deepEqual(pushes, [], "not sent to /login");
    assert.equal(signOutStore.get(), true, "the sheet stays up");
    assert.match(document.body.textContent ?? "", /couldn.t sign you out/i);
    assert.equal(window.localStorage.getItem("edward_auth_hint"), "1");
  });

  it("when it works, leaves none of Ellis's content in the tab", async () => {
    window.sessionStorage.setItem("edward:today:v1", "{feed}");
    window.sessionStorage.setItem("edward:draft:c1", "half a thought");
    window.localStorage.setItem(MIND_NOW_CACHE_KEY, "{mind}");
    let signedOut = 0;
    const on = () => signedOut++;
    window.addEventListener(SIGNED_OUT_EVENT, on);
    mountSignOut();
    await flush();
    act(() => signOutStore.open());
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await flush(60);
    window.removeEventListener(SIGNED_OUT_EVENT, on);
    assert.deepEqual(pushes, ["/login"]);
    assert.equal(window.sessionStorage.getItem("edward:today:v1"), null);
    assert.equal(window.sessionStorage.getItem("edward:draft:c1"), null);
    assert.equal(window.localStorage.getItem(MIND_NOW_CACHE_KEY), null);
    assert.equal(signedOut, 1, "the Today feed's module cache hears about it");
    assert.equal(auth.isAuthenticated, false);
  });
});
