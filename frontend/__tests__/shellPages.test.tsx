// R/G red: the command palette, the login door and the You card.
//
//  - Palette: opens with the search field focused, ranks what Ellis types,
//    ↓ + ↵ opens the active result; an action (New chat) runs.
//  - Login: Edward looks away while a hidden password is typed and peeks
//    when it is shown; the field is a real password field for password
//    managers (hidden username "owner", current-password); a wrong password
//    is said in his voice and he winces; a right one goes to Today.
//  - You card: Light / Dark / System, and sign out behind a confirm.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { AuthProvider } from "../lib/AuthContext.tsx";
import { ChatProvider } from "../lib/ChatContext.tsx";
import { ThemeProvider } from "../lib/theme.tsx";
import { EdwardPresenceProvider } from "../lib/presence.tsx";
import { CommandPalette } from "../components/layout/CommandPalette.tsx";
import { SignOutSheet } from "../components/layout/SignOutSheet.tsx";
import { paletteStore, signOutStore } from "../components/layout/shellStores.ts";
import LoginPage from "../app/(auth)/login/page.tsx";
import YouCard from "../components/settings/YouCard.tsx";

const originalFetch = globalThis.fetch;
const posts: Array<{ path: string; body: unknown }> = [];
let loginOk = true;
let authed = true;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function installFetch() {
  posts.length = 0;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (init?.method === "POST") {
      posts.push({ path, body: init.body ? JSON.parse(String(init.body)) : null });
      if (path.startsWith("/api/auth/login")) {
        return loginOk ? json({ status: "ok" }) : json({ detail: "Invalid password" }, 401);
      }
      return json({ ok: true });
    }
    if (path.startsWith("/api/auth/status")) return json({ configured: true, authenticated: authed });
    if (path.startsWith("/api/mind/now")) return json({ recent: [] });
    if (path.startsWith("/api/voice/status")) return json({ enabled: true, status: "ok" });
    if (path.startsWith("/api/conversations/latest")) return json({ updated_at: null });
    if (path.startsWith("/api/conversations")) {
      return json({
        conversations: [
          { id: "c-cabin-weekend", title: "Cabin weekend", source: "user", channel: "web", notified_user: false, created_at: "2026-09-26T10:00:00Z", updated_at: "2026-09-26T16:00:00Z", message_count: 12 },
          { id: "c-biscuit-vet", title: "Biscuit's vet visit", source: "user", channel: "web", notified_user: false, created_at: "2026-09-25T10:00:00Z", updated_at: "2026-09-25T16:00:00Z", message_count: 6 },
        ],
        has_more: false,
      });
    }
    return json({});
  }) as typeof fetch;
}

const pushes: string[] = [];
const router = {
  push: (u: string) => void pushes.push(u),
  replace: () => {},
  back: () => {},
  forward: () => {},
  refresh: () => {},
  prefetch: () => {},
};

function Next({ path, children }: { path: string; children: React.ReactNode }) {
  const [pathname, query = ""] = path.split("?");
  return (
    <AppRouterContext.Provider value={router as never}>
      <PathnameContext.Provider value={pathname}>
        <SearchParamsContext.Provider value={new URLSearchParams(query)}>{children}</SearchParamsContext.Provider>
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
const present = (el: Element | null) => el !== null;

beforeEach(() => {
  installFetch();
  pushes.length = 0;
  loginOk = true;
  authed = true;
  try {
    window.localStorage.setItem("edward_auth_hint", "1");
  } catch {
    /* none */
  }
});
afterEach(() => {
  cleanup();
  act(() => {
    paletteStore.close();
    signOutStore.close();
  });
  globalThis.fetch = originalFetch;
});

// ---------------------------------------------------------------------------

describe("CommandPalette", () => {
  async function mountPalette() {
    render(
      <Next path="/today">
        <AuthProvider>
          <ChatProvider>
            <EdwardPresenceProvider timing={{ pollMs: 60_000, retryMs: 20 }}>
              <ThemeProvider>
                <CommandPalette />
              </ThemeProvider>
            </EdwardPresenceProvider>
          </ChatProvider>
        </AuthProvider>
      </Next>,
    );
    await flush();
    act(() => paletteStore.open());
    await flush();
  }

  it("opens on the search field with recent chats and actions", async () => {
    await mountPalette();
    const input = screen.getByRole("combobox", { name: "Search Edward" });
    assert.equal(document.activeElement === input, true, "focus is in the field");
    const text = screen.getByRole("listbox").textContent ?? "";
    assert.match(text, /Recent/);
    assert.match(text, /Cabin weekend/);
    assert.match(text, /New chat/);
  });

  it("its sticky search row and hint footer sit flush on the scroller's edges (no rows leak past them)", async () => {
    // 2026-09-26: the Sheet body's padding (pt-2, md:pb-4) left an 8px strip
    // above the sticky search field and a 16px one under the desktop footer
    // where scrolled rows showed through. The palette's body has no padding
    // at those edges; the rows carry their own.
    await mountPalette();
    const dialog = screen.getByRole("dialog");
    const scroller = dialog.querySelector(".overflow-y-auto") as HTMLElement;
    assert.ok(scroller, "the sheet's scrolling body");
    assert.match(scroller.className, /(^|\s)pt-0(\s|$)/);
    assert.match(scroller.className, /(^|\s)md:pb-0(\s|$)/);
    const search = screen.getByRole("combobox", { name: "Search Edward" }).closest(".sticky") as HTMLElement;
    assert.doesNotMatch(search.className, /-mt-2/, "no negative margin reaching into padding");
  });

  it("↓ and ↵ open the result Ellis picked", async () => {
    await mountPalette();
    const input = screen.getByRole("combobox", { name: "Search Edward" });
    fireEvent.change(input, { target: { value: "vet" } });
    await flush(260); // the server search is debounced
    assert.match(screen.getByRole("listbox").textContent ?? "", /Biscuit's vet visit/);
    fireEvent.keyDown(input, { key: "Enter" });
    assert.deepEqual(pushes, ["/chat?c=c-biscuit-vet"]);
    assert.equal(paletteStore.get(), false, "choosing closes the palette");
  });

  it("runs an action (goals left the palette with unit G, 2026-10-11)", async () => {
    await mountPalette();
    const again = screen.getByRole("combobox", { name: "Search Edward" });
    fireEvent.change(again, { target: { value: "new chat" } });
    await flush(260);
    fireEvent.keyDown(again, { key: "ArrowDown" });
    fireEvent.keyDown(again, { key: "ArrowUp" });
    fireEvent.keyDown(again, { key: "Enter" });
    assert.equal(pushes[pushes.length - 1], "/chat");
  });
});

// ---------------------------------------------------------------------------

describe("Login", () => {
  async function mountLogin() {
    authed = false;
    try {
      window.localStorage.removeItem("edward_auth_hint");
    } catch {
      /* none */
    }
    render(
      <Next path="/login">
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </Next>,
    );
    await flush();
  }
  const edward = () => document.querySelector(".pane-avatar") as HTMLElement;

  it("is a real password form for password managers", async () => {
    await mountLogin();
    const pw = screen.getByLabelText("Password") as HTMLInputElement;
    assert.equal(pw.type, "password");
    assert.equal(pw.getAttribute("autocomplete"), "current-password");
    assert.equal(pw.getAttribute("name"), "password");
    const user = document.querySelector('input[autocomplete="username"]') as HTMLInputElement;
    // One account per install: the saved password belongs to "edward", not to a person.
    assert.equal(user?.value, "edward");
    assert.equal(present(screen.queryByRole("button", { name: "Show password" })), true);
  });

  it("looks away while a hidden password is typed and peeks when it is shown", async () => {
    await mountLogin();
    const pw = screen.getByLabelText("Password") as HTMLInputElement;
    assert.equal(edward().getAttribute("data-gaze"), null);
    fireEvent.change(pw, { target: { value: "hunter2" } });
    assert.equal(edward().getAttribute("data-gaze"), "up");
    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    assert.equal(pw.type, "text");
    assert.equal(edward().getAttribute("data-gaze"), "down");
    assert.equal(present(screen.queryByRole("button", { name: "Hide password" })), true);
  });

  it("a wrong password is said in his voice", async () => {
    loginOk = false;
    await mountLogin();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await flush(60);
    assert.match(screen.getByRole("alert").textContent ?? "", /That's not it\. Try again\?/);
    assert.deepEqual(pushes, []);
  });

  it("the right password: he nods first, then fades into Today", async () => {
    await mountLogin();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "right" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await flush(120);
    const stage = document.querySelector("[data-login-stage]") as HTMLElement;
    assert.equal(stage.getAttribute("data-login-stage"), "nod", "the nod plays before anything fades");
    assert.doesNotMatch(stage.className, /opacity-0/);
    assert.deepEqual(pushes, [], "and nothing navigates mid-nod");
    await flush(600);
    assert.equal(stage.getAttribute("data-login-stage"), "leaving");
    await flush(700);
    assert.deepEqual(pushes, ["/today"]);
  });
});

// ---------------------------------------------------------------------------

describe("YouCard", () => {
  async function mountYou() {
    render(
      <Next path="/settings">
        <AuthProvider>
          <ThemeProvider>
            <YouCard />
            <SignOutSheet />
          </ThemeProvider>
        </AuthProvider>
      </Next>,
    );
    await flush();
  }

  it("switches between Light, Dark and System", async () => {
    await mountYou();
    const light = screen.getByRole("radio", { name: "Light" });
    fireEvent.click(light);
    assert.equal(light.getAttribute("aria-checked"), "true");
    assert.equal(document.documentElement.classList.contains("dark"), false);
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    assert.equal(document.documentElement.classList.contains("dark"), true);
    fireEvent.click(screen.getByRole("radio", { name: "System" }));
    assert.equal(screen.getByRole("radio", { name: "System" }).getAttribute("aria-checked"), "true");
  });

  it("signs out only after a confirm", async () => {
    await mountYou();
    fireEvent.click(screen.getByRole("button", { name: /Sign out/ }));
    const dialog = screen.getByRole("dialog");
    assert.match(dialog.textContent ?? "", /Sign out of Edward\?/);
    assert.equal(posts.some((p) => p.path.startsWith("/api/auth/logout")), false, "nothing yet");
    const confirm = Array.from(dialog.querySelectorAll("button")).find((b) => b.textContent === "Sign out")!;
    fireEvent.click(confirm);
    await flush(60);
    assert.equal(posts.some((p) => p.path.startsWith("/api/auth/logout")), true);
    assert.deepEqual(pushes, ["/login"]);
  });
});
