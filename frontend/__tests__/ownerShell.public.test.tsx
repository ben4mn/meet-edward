// The shell knows its owner only from the server (public release, 2026-10-12).
//
// GET /api/auth/status carries `owner_name` (EDWARD_OWNER_NAME, "" when
// unset). AuthContext hands it to lib/owner.ts; the login page greets the
// owner by it and the avatar shows its initial. With no name configured the
// copy still reads well: "Welcome back." and a person glyph. Copy elsewhere
// speaks to the owner as "you", and the held-send sentinel matches whatever
// name the backend rendered ("Waiting for <owner>'s approval").

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { AuthProvider } from "../lib/AuthContext.tsx";
import { authVerdict } from "../lib/authSession.ts";
import { getOwnerName, setOwnerName } from "../lib/owner.ts";
import { isHeldForApproval, isOutwardAct } from "../lib/turnModel.ts";
import { provenanceRows, type MemoryProvenance } from "../lib/provenance.ts";
import { ToastProvider } from "../components/ui/Toast.tsx";
import { ThemeProvider } from "../lib/theme.tsx";
import { AvatarMenu } from "../components/layout/AvatarMenu.tsx";
import { LoginForm } from "../app/(auth)/login/LoginForm.tsx";

const originalFetch = globalThis.fetch;
let status: Record<string, unknown> = { configured: true, authenticated: false };

const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value="/login">
        <SearchParamsContext.Provider value={new URLSearchParams("")}>
          <ToastProvider>
            <ThemeProvider>{children}</ThemeProvider>
          </ToastProvider>
        </SearchParamsContext.Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>
  );
}

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));
async function flush(ms = 40) {
  await act(async () => {
    await tick(ms);
  });
}

beforeEach(() => {
  setOwnerName("");
  status = { configured: true, authenticated: false };
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    const body = path.startsWith("/api/auth/status") ? status : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  }) as typeof fetch;
});

afterEach(async () => {
  cleanup();
  await tick(20);
  globalThis.fetch = originalFetch;
  setOwnerName("");
});

describe("authVerdict carries the owner's name", () => {
  it("passes a string owner_name through and leaves it out otherwise", () => {
    assert.deepEqual(authVerdict({ configured: true, authenticated: true, owner_name: "Ellis" }), {
      configured: true,
      authenticated: true,
      owner_name: "Ellis",
    });
    assert.deepEqual(authVerdict({ configured: true, authenticated: true, owner_name: "" }), {
      configured: true,
      authenticated: true,
      owner_name: "",
    });
    assert.deepEqual(authVerdict({ configured: true, authenticated: true, owner_name: 7 }), {
      configured: true,
      authenticated: true,
    });
  });
});

describe("the login page", () => {
  it("greets the owner by the name the server sent", async () => {
    status = { configured: true, authenticated: false, owner_name: "Ellis" };
    render(
      <Frame>
        <AuthProvider retryDelayMs={() => 60_000}>
          <LoginForm />
        </AuthProvider>
      </Frame>,
    );
    await flush(60);
    assert.equal(getOwnerName(), "Ellis");
    assert.ok(screen.getByText("Welcome back, Ellis."));
  });

  it("says a plain welcome when no name is configured, and fills no one's username", async () => {
    status = { configured: true, authenticated: false, owner_name: "" };
    render(
      <Frame>
        <AuthProvider retryDelayMs={() => 60_000}>
          <LoginForm />
        </AuthProvider>
      </Frame>,
    );
    await flush(60);
    assert.ok(screen.getByText("Welcome back."));
    const username = document.querySelector('input[name="username"]') as HTMLInputElement;
    assert.equal(username.value, "edward");
  });
});

describe("the avatar menu", () => {
  it("shows the owner's initial and name", async () => {
    setOwnerName("Ellis");
    render(
      <Frame>
        <AvatarMenu />
      </Frame>,
    );
    const button = screen.getByRole("button", { name: "Ellis — appearance, screen, sign out" });
    assert.equal(button.textContent, "E");
  });

  it("falls back to a person glyph and 'You' without a name", async () => {
    render(
      <Frame>
        <AvatarMenu />
      </Frame>,
    );
    const button = screen.getByRole("button", { name: "You — appearance, screen, sign out" });
    assert.equal(button.textContent, "");
    assert.ok(button.querySelector("svg"), "a glyph, not a letter");
  });
});

describe("a send held for approval", () => {
  it("is recognised whatever name the backend rendered", () => {
    assert.equal(isHeldForApproval("Waiting for Ellis's approval (#12): queued"), true);
    assert.equal(isHeldForApproval("Waiting for the owner's approval (#3)"), true);
    assert.equal(isHeldForApproval("  waiting for Sam O'Neil's approval (#4)"), true);
    assert.equal(isHeldForApproval("Sent to Parker"), false);
    assert.equal(isHeldForApproval(null), false);
    assert.equal(isOutwardAct("send_message", "Waiting for Ellis's approval (#12)"), false);
    assert.equal(isOutwardAct("send_message", "Sent to Parker"), true);
  });
});

describe("provenance speaks to the owner", () => {
  it("says 'You said it, about you' for the owner's own words", () => {
    const p: MemoryProvenance = {
      memory: {
        id: "m1",
        content: "x",
        origin: "heard",
        about: "owner",
        source_type: "chat",
        created_at: null,
        source_conversation_id: null,
      },
      conversation: null,
      chat: null,
      contact: null,
      fold: null,
      source_ref: {},
      summary: "",
    };
    assert.equal(provenanceRows(p)[0].value, "You said it, about you");
  });
});
