// Adding photos to the hub album from the phone survives a deploy
// (2026-09-27 review of H3). The panel uploads one file per request over
// the tunnel; twenty photos can take minutes with no tap, so the app read
// as idle and a reload cut the batch off, and the rest of the picked files
// were gone. The panel holds reloads while it uploads.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import { restoreFetch, json } from "./panelTestKit.ts";
import { HubPhotosPanel } from "../components/settings/HubPhotosPanel.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";
import { reloadHeld } from "../lib/buildReload.ts";

afterEach(() => {
  cleanup();
  restoreFetch();
});

describe("HubPhotosPanel", () => {
  it("holds a new build's reload while photos are uploading", async () => {
    const pending: Array<() => void> = [];
    let posts = 0;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        posts++;
        return new Promise<Response>((resolve) =>
          pending.push(() => resolve(json({ saved: [], rejected: [], saved_count: 1, converted_count: 0 }))),
        );
      }
      if (String(input).includes("/api/hub/photos")) return json({ photos: [], skipped_unsupported: 0, dir: "~/edward-photos", dir_present: true });
      return json({ detail: "not mocked" }, 404);
    }) as typeof fetch;
    const { container } = render(
      <ToastProvider>
        <HubPhotosPanel />
      </ToastProvider>,
    );
    await screen.findByRole("button", { name: "Add photos" });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [new File(["a"], "a.jpg", { type: "image/jpeg" }), new File(["b"], "b.jpg", { type: "image/jpeg" })];
    await act(async () => {
      fireEvent.change(input, { target: { files } });
      await new Promise((r) => setTimeout(r, 10));
    });
    assert.equal(posts, 1, "the first photo is on its way");
    assert.equal(screen.queryByRole("button", { name: /Uploading 0 of 2/ }) !== null, true);
    assert.equal(reloadHeld(), true, "a reload would cut the batch off");
    for (let i = 0; i < 2; i++) {
      await act(async () => {
        pending.shift()?.();
        await new Promise((r) => setTimeout(r, 10));
      });
    }
    await act(async () => void (await new Promise((r) => setTimeout(r, 20))));
    assert.equal(reloadHeld(), false, "done: nothing left to lose");
  });
});
