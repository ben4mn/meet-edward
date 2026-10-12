import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

/**
 * 2026-09-21: `/api/auth/status` was StaleWhileRevalidate for an hour, so a
 * phone whose cookie had just died could be told "authenticated" from the
 * worker's cache, render the app, 401 on the first real call, bounce to
 * /login, be told "authenticated" again, and loop. The answer to "am I
 * logged in?" is never served from a cache.
 */
describe("PWA runtime caching", () => {
  const { handlerFor } = require("../pwa-caching.js");

  it("never answers the auth check from cache", () => {
    assert.equal(handlerFor("/api/auth/status"), "NetworkOnly");
  });

  it("never caches app data", () => {
    for (const path of ["/api/today", "/api/conversations", "/api/settings", "/api/mind/log", "/api/chat"]) {
      const handler = handlerFor(path);
      assert.ok(handler === undefined || handler === "NetworkOnly", `${path} is ${handler}`);
    }
  });

  it("still caches immutable bundles and media", () => {
    assert.equal(handlerFor("/_next/static/chunks/122-776406fa4419b4fe.js"), "CacheFirst");
    assert.equal(handlerFor("/icons/icon-192.png"), "CacheFirst");
  });

  it("screensaver photos bypass the worker", () => {
    assert.equal(handlerFor("/api/hub/photos/kitchen.jpg"), "NetworkOnly");
  });

  // H3 (2026-09-27): every tab asks /build-id which build the server runs
  // and reloads onto a new deploy; a cached answer would pin the old one.
  it("never answers the build id from cache", () => {
    assert.equal(handlerFor("/build-id"), "NetworkOnly");
    assert.equal(handlerFor("/build-id?t=1727440000000"), "NetworkOnly");
  });

  it("patterns match the way Workbox matches them: against the whole URL", () => {
    const { runtimeCaching } = require("../pwa-caching.js");
    for (const rule of runtimeCaching) {
      if (rule.urlPattern instanceof RegExp) {
        assert.doesNotMatch(rule.urlPattern.source, /^\^\\\//, `${rule.urlPattern} is anchored at the path and never matches an href`);
      }
    }
  });

  it("next.config.js uses this list", () => {
    const cfg = readFileSync(new URL("../next.config.js", import.meta.url), "utf8");
    assert.match(cfg, /require\(["']\.\/pwa-caching(\.js)?["']\)/);
  });
});
