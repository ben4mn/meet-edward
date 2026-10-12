import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";

/**
 * 2026-09-14: the generated service worker precached
 * `/_next/app-build-manifest.json`, which `next start` never serves (404).
 * Workbox fails the whole install when one precached file fails, so no
 * device could activate a worker — or subscribe to push — since the
 * frontend moved to a production build on Sep 6.
 */
describe("service worker precache", () => {
  it("excludes the app build manifest next start does not serve", () => {
    const cfg = readFileSync(new URL("../next.config.js", import.meta.url), "utf8");
    assert.match(cfg, /buildExcludes:\s*\[[^\]]*app-build-manifest/);
  });
  it("the built worker (when present) does not precache it", () => {
    const sw = new URL("../public/sw.js", import.meta.url);
    if (!existsSync(sw)) return; // build artifact; absent in a clean checkout
    assert.doesNotMatch(readFileSync(sw, "utf8"), /app-build-manifest\.json/);
  });
});
