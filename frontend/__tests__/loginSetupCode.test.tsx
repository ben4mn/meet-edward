// R/G red (2026-10-12 review): the first password needs the one-time setup
// code from the Mac.
//
//  - Before any password exists the server says setup_code_required, and
//    the setup form asks for the code under its own label, saying where it
//    is ("It's in backend/.setup_code on the Mac Edward runs on.").
//  - The code goes to POST /api/auth/setup as JSON with the password.
//  - A refusal shows the server's own words — a wrong code, "only on the
//    Mac Edward runs on", "already set" — never "That's not it", which reads
//    as a wrong password.
//  - "Already set" asks the server again, so the stale setup form turns
//    into the sign-in form.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { AuthProvider } from "../lib/AuthContext.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";
import { LoginForm } from "../app/(auth)/login/LoginForm.tsx";
import { SETUP_CODE_HINT } from "../lib/loginFlow.ts";

const SETUP_CODE_WRONG = "That setup code doesn't match. On the Mac Edward runs on: cat backend/.setup_code";
const SETUP_LOCAL_ONLY = "The first password can only be set on the Mac Edward runs on. Open http://localhost:3000 there.";
const SETUP_ALREADY_DONE = "A password is already set. Sign in with it.";

const originalFetch = globalThis.fetch;
let status: () => Record<string, unknown>;
let setupReply: () => Response;
const setupCalls: Array<{ body: unknown; contentType: string | null }> = [];
const pushes: string[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const router = {
  push: (u: string) => void pushes.push(u),
  replace: () => {},
  back: () => {},
  forward: () => {},
  refresh: () => {},
  prefetch: () => {},
};

function mount() {
  render(
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value="/login">
        <SearchParamsContext.Provider value={new URLSearchParams("")}>
          <ToastProvider>
            <AuthProvider retryDelayMs={() => 60_000}>
              <LoginForm />
            </AuthProvider>
          </ToastProvider>
        </SearchParamsContext.Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>,
  );
}

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function flush(ms = 40) {
  await act(async () => {
    await tick(ms);
  });
}

const fresh = () => ({ configured: false, authenticated: false, owner_name: "", setup_code_required: true });

beforeEach(() => {
  setupCalls.length = 0;
  pushes.length = 0;
  status = fresh;
  setupReply = () => json({ message: "Password configured successfully" });
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (path.startsWith("/api/auth/status")) return json(status());
    if (path.startsWith("/api/auth/setup") && init?.method === "POST") {
      const headers = new Headers(init.headers);
      setupCalls.push({ body: JSON.parse(String(init.body)), contentType: headers.get("content-type") });
      return setupReply();
    }
    return json({});
  }) as typeof fetch;
});

afterEach(async () => {
  cleanup();
  await tick(20);
  globalThis.fetch = originalFetch;
  window.localStorage.removeItem("edward_auth_hint");
});

function fill(code: string, password = "a long enough one") {
  fireEvent.change(screen.getByLabelText("Setup code"), { target: { value: code } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
}

function submit() {
  fireEvent.click(screen.getByRole("button", { name: "Set password" }));
}

describe("the setup form", () => {
  it("asks for the setup code and says where it is", async () => {
    mount();
    await flush();
    assert.ok(screen.getByText(/Choose a password/));
    assert.ok(screen.getByLabelText("Setup code"));
    assert.ok(screen.getByText(SETUP_CODE_HINT));
    assert.equal(SETUP_CODE_HINT, "It's in backend/.setup_code on the Mac Edward runs on.");
  });

  it("waits for both the code and the password", async () => {
    mount();
    await flush();
    const button = screen.getByRole("button", { name: "Set password" }) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a long enough one" } });
    assert.equal(button.disabled, true, "no code yet");
    fireEvent.change(screen.getByLabelText("Setup code"), { target: { value: "K7QH-M3TX-9PWF" } });
    assert.equal(button.disabled, false);
  });

  it("sends the code with the password, as JSON", async () => {
    mount();
    await flush();
    fill(" K7QH-M3TX-9PWF ");
    submit();
    await flush(80);
    assert.deepEqual(setupCalls, [
      { body: { password: "a long enough one", setup_code: "K7QH-M3TX-9PWF" }, contentType: "application/json" },
    ]);
    assert.equal(screen.queryByRole("alert"), null);
  });

  for (const [name, refusal] of [
    ["a wrong code", SETUP_CODE_WRONG],
    ["a device that is not the Mac", SETUP_LOCAL_ONLY],
  ] as const) {
    it(`shows the server's words for ${name}, never "That's not it"`, async () => {
      setupReply = () => json({ detail: refusal }, 403);
      mount();
      await flush();
      fill("AAAA-BBBB-CCCC");
      submit();
      await flush(80);
      assert.equal(screen.getByRole("alert").textContent, refusal);
      assert.doesNotMatch(document.body.textContent ?? "", /That's not it/);
    });
  }

  it("a password set meanwhile turns the form into the sign-in form", async () => {
    setupReply = () => {
      status = () => ({ configured: true, authenticated: false, owner_name: "", setup_code_required: false });
      return json({ detail: SETUP_ALREADY_DONE }, 400);
    };
    mount();
    await flush();
    fill("K7QH-M3TX-9PWF");
    submit();
    await flush(120);
    assert.equal(screen.getByRole("alert").textContent, SETUP_ALREADY_DONE);
    assert.equal(screen.queryByLabelText("Setup code"), null);
    assert.ok(screen.getByRole("button", { name: "Sign in" }));
  });
});

describe("signing in", () => {
  it("never asks for a setup code once a password is set", async () => {
    status = () => ({ configured: true, authenticated: false, owner_name: "", setup_code_required: false });
    mount();
    await flush();
    assert.equal(screen.queryByLabelText("Setup code"), null);
    assert.ok(screen.getByRole("button", { name: "Sign in" }));
  });
});
