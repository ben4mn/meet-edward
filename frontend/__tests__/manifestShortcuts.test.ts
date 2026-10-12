// R/G red: the home-screen "Talk live" shortcut (2026-09-26): a long press
// on the Edward icon goes straight into live voice.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const manifest = JSON.parse(readFileSync(join(process.cwd(), "public", "manifest.json"), "utf8"));

describe("manifest shortcuts", () => {
  it("offers Talk live, pointing at /chat?voice=1", () => {
    const talk = manifest.shortcuts.find((s: { name: string }) => s.name === "Talk live");
    assert.ok(talk, "a Talk live shortcut");
    assert.equal(talk.url, "/chat?voice=1");
    assert.ok(talk.short_name && talk.short_name.length <= 12);
  });
  it("keeps Today and New chat", () => {
    const urls = manifest.shortcuts.map((s: { url: string }) => s.url);
    assert.ok(urls.includes("/today"));
    assert.ok(urls.includes("/chat"));
  });
});
