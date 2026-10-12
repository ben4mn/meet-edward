// The household photo door (2026-10-11): /photos, a small PWA with its own
// password for someone else in the household. It shows the hub's
// screensaver album, adds photos, turns or removes them, and sets the
// screensaver — and nothing of Ellis's: the page talks only to
// /api/photo-door/*. Off until the server has PHOTO_DOOR_PASSWORD_HASH.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createRequire } from "node:module";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import { json, mockFetch, restoreFetch, type Call } from "./panelTestKit.ts";
import { PhotoDoorApp } from "../app/photos/PhotoDoorApp.tsx";

const require = createRequire(import.meta.url);

afterEach(() => {
  cleanup();
  restoreFetch();
});

const SETTINGS = { slide_seconds: 30, transition: "kenburns", edward_presents: true };

function signedIn(extra?: (url: string, method: string, body: unknown) => unknown): Call[] {
  return mockFetch((url, method, body) => {
    const hit = extra?.(url, method, body);
    if (hit !== undefined) return hit;
    if (url.endsWith("/api/photo-door/auth/status")) return { authenticated: true, enabled: true };
    if (url.endsWith("/api/photo-door/photos") && method === "GET") return { photos: ["a.jpg", "b.png"], skipped_unsupported: 0, dir_present: true };
    if (url.endsWith("/api/photo-door/settings") && method === "GET") return SETTINGS;
    return undefined;
  });
}

const settle = () => act(async () => void (await new Promise((r) => setTimeout(r, 20))));

const PASSWORD = "plum-teapot-42";

describe("the photo door", () => {
  it("asks for the password when not signed in, and signs in with it", async () => {
    let authed = false;
    const calls = mockFetch((url, method) => {
      if (url.endsWith("/api/photo-door/auth/status")) return { authenticated: authed, enabled: true };
      if (url.endsWith("/api/photo-door/auth/login") && method === "POST") {
        authed = true;
        return { message: "ok" };
      }
      if (url.endsWith("/api/photo-door/photos")) return { photos: [], skipped_unsupported: 0, dir_present: true };
      if (url.endsWith("/api/photo-door/settings")) return SETTINGS;
      return undefined;
    });
    render(<PhotoDoorApp />);
    const field = await screen.findByLabelText("Password");
    fireEvent.change(field, { target: { value: PASSWORD } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Open" }));
    });
    await settle();
    const login = calls.find((c) => c.url.endsWith("/api/photo-door/auth/login"));
    assert.deepEqual(login?.body, { password: PASSWORD });
    await screen.findByText(/Nothing here yet/);
  });

  it("says so when the password is wrong", async () => {
    mockFetch((url) => {
      if (url.endsWith("/api/photo-door/auth/status")) return { authenticated: false, enabled: true };
      if (url.endsWith("/api/photo-door/auth/login")) return json({ detail: "Invalid password" }, 401);
      return undefined;
    });
    render(<PhotoDoorApp />);
    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "nope" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Open" }));
    });
    await screen.findByText(/That's not it/);
  });

  it("shows the album as thumbnails", async () => {
    signedIn();
    const { container } = render(<PhotoDoorApp />);
    await screen.findByRole("button", { name: "Rotate a.jpg right" });
    const srcs = Array.from(container.querySelectorAll("img")).map((i) => i.getAttribute("src"));
    assert.ok(srcs.includes("/api/photo-door/photos/a.jpg/thumb"));
    assert.ok(srcs.includes("/api/photo-door/photos/b.png/thumb"));
  });

  it("rotates a photo and shows it under its new name", async () => {
    let rotated = false;
    const calls = signedIn((url, method) => {
      if (url.endsWith("/api/photo-door/photos/a.jpg/rotate") && method === "POST") {
        rotated = true;
        return { name: "a-rotated.jpg", previous: "a.jpg" };
      }
      if (url.endsWith("/api/photo-door/photos") && method === "GET" && rotated)
        return { photos: ["a-rotated.jpg", "b.png"], skipped_unsupported: 0, dir_present: true };
      return undefined;
    });
    render(<PhotoDoorApp />);
    const target = await screen.findByRole("button", { name: "Rotate a.jpg right" });
    await act(async () => {
      fireEvent.click(target);
    });
    await settle();
    const rotate = calls.find((c) => c.url.endsWith("/rotate"));
    assert.deepEqual(rotate?.body, { direction: "right" });
    await screen.findByRole("button", { name: "Rotate a-rotated.jpg right" });
  });

  it("deletes a photo after a confirming tap", async () => {
    const calls = signedIn((url, method) => (method === "DELETE" ? { deleted: "b.png" } : undefined));
    render(<PhotoDoorApp />);
    const target = await screen.findByRole("button", { name: "Delete b.png" });
    await act(async () => {
      fireEvent.click(target);
    });
    assert.equal(calls.filter((c) => c.method === "DELETE").length, 0, "the first tap only asks");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Really delete b.png" }));
    });
    await settle();
    assert.ok(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/api/photo-door/photos/b.png")));
  });

  it("sets the screensaver through the door's settings route", async () => {
    const calls = signedIn((url, method, body) =>
      url.endsWith("/api/photo-door/settings") && method === "PATCH" ? { ...SETTINGS, ...(body as object) } : undefined,
    );
    render(<PhotoDoorApp />);
    const target = await screen.findByRole("radio", { name: "Crossfade" });
    await act(async () => {
      fireEvent.click(target);
    });
    await settle();
    const patch = calls.find((c) => c.method === "PATCH");
    assert.equal(patch?.url.endsWith("/api/photo-door/settings"), true);
    assert.deepEqual(patch?.body, { transition: "crossfade" });
  });

  it("never talks to anything but the door's own routes", async () => {
    const calls = signedIn();
    render(<PhotoDoorApp />);
    await screen.findByRole("button", { name: "Rotate a.jpg right" });
    await settle();
    for (const c of calls) assert.ok(c.url.includes("/api/photo-door/"), `${c.method} ${c.url}`);
  });

  it("says the door is not set up when the server has no password for it", async () => {
    const calls = mockFetch((url) => {
      if (url.endsWith("/api/photo-door/auth/status")) return { authenticated: false, enabled: false };
      return undefined;
    });
    render(<PhotoDoorApp />);
    await screen.findByText(/isn't set up yet/);
    assert.equal(screen.queryByLabelText("Password"), null, "no password box for a door with no password");
    await settle();
    assert.ok(!calls.some((c) => c.url.endsWith("/auth/login")));
  });

  it("a login the server refuses as closed shows the same", async () => {
    mockFetch((url) => {
      if (url.endsWith("/api/photo-door/auth/status")) return { authenticated: false, enabled: true };
      if (url.endsWith("/api/photo-door/auth/login")) return json({ detail: "The photo door is off." }, 403);
      return undefined;
    });
    render(<PhotoDoorApp />);
    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: PASSWORD } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Open" }));
    });
    await screen.findByText(/isn't set up yet/);
  });
});

describe("the photo door's PWA shell", () => {
  it("has its own manifest that opens on /photos and stays there", () => {
    const m = JSON.parse(readFileSync(join(process.cwd(), "public", "photo-door-manifest.json"), "utf8"));
    assert.equal(m.start_url, "/photos");
    assert.equal(m.scope, "/photos");
    assert.equal(m.id, "/photos");
    assert.equal(m.display, "standalone");
  });

  it("points the page at that manifest", () => {
    const layout = readFileSync(join(process.cwd(), "app", "photos", "layout.tsx"), "utf8");
    assert.match(layout, /manifest:\s*"\/photo-door-manifest\.json"/);
  });

  it("never serves the door's photos or its session from the worker's cache", () => {
    const { handlerFor } = require("../pwa-caching.js");
    assert.equal(handlerFor("/api/photo-door/photos/a.jpg"), "NetworkOnly");
    assert.equal(handlerFor("/api/photo-door/photos/a.jpg/thumb"), "NetworkOnly");
    assert.equal(handlerFor("/api/photo-door/auth/status"), "NetworkOnly");
  });
});
