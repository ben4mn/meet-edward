// First-load budgets, checked after every production build (2026-09-27, L15).
//
// /today's first load reached its 160 kB target and nothing guarded it:
// firstLoadBundle.test.ts pins imports, not bytes. scripts/bundle-budget.js
// reads the build's manifests, computes each key route's first-load JS the
// way `next build` prints it (gzip of the route's JS chunks in
// app-build-manifest.json, kB = 1000 bytes), and warns in the log when a
// route is over its budget. It never fails the build: ensure-build.sh falls
// back to `next dev` on a failed build, which would be far worse.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const budget = require("../scripts/bundle-budget.js");

describe("bundle budget", () => {
  it("has a budget for every key route", () => {
    assert.deepEqual(Object.keys(budget.BUDGETS_KB).sort(), ["/chat", "/hub", "/screen", "/settings", "/today"]);
    assert.equal(budget.BUDGETS_KB["/today"], 160);
    assert.equal(budget.BUDGETS_KB["/chat"], 260);
  });

  it("names a manifest key by the route it serves", () => {
    assert.equal(budget.routeOf("/(app)/today/page"), "/today");
    assert.equal(budget.routeOf("/(display)/hub/page"), "/hub");
    assert.equal(budget.routeOf("/(app)/today/goals/[id]/page"), "/today/goals/[id]");
    assert.equal(budget.routeOf("/(public)/page"), "/");
    assert.equal(budget.routeOf("/(app)/layout"), null);
  });

  it("counts a route's JS chunks once each, and no CSS", () => {
    const manifest = {
      pages: {
        "/(app)/layout": ["static/chunks/layout.js"],
        "/(app)/today/page": [
          "static/chunks/webpack.js",
          "static/chunks/main-app.js",
          "static/css/app.css",
          "static/chunks/webpack.js",
          "static/chunks/app/(app)/today/page.js",
        ],
      },
    };
    assert.deepEqual(budget.firstLoadFiles(manifest, "/today"), [
      "static/chunks/webpack.js",
      "static/chunks/main-app.js",
      "static/chunks/app/(app)/today/page.js",
    ]);
    assert.equal(budget.firstLoadFiles(manifest, "/nowhere"), null);
  });

  it("is over budget only past the budget", () => {
    const results = budget.checkBudgets(
      { "/today": 160_001, "/chat": 256_400, "/hub": 104_000, "/screen": null },
      { "/today": 160, "/chat": 260, "/hub": 104, "/screen": 145 },
    );
    const by = Object.fromEntries(results.map((r: { route: string }) => [r.route, r]));
    assert.equal(by["/today"].over, true);
    assert.equal(by["/chat"].over, false);
    assert.equal(by["/hub"].over, false, "exactly at the budget is within it");
    assert.equal(by["/screen"].over, false, "a route missing from the build is not over");
    assert.equal(by["/screen"].missing, true);
  });

  it("warns in the report for a route over budget, and only then", () => {
    const lines: string[] = budget.formatReport(
      budget.checkBudgets({ "/today": 163_200, "/chat": 256_400 }, { "/today": 160, "/chat": 260 }),
    );
    const today = lines.find((l) => l.includes("/today"))!;
    const chat = lines.find((l) => l.includes("/chat"))!;
    assert.match(today, /WARN/);
    assert.match(today, /163\.2 kB/);
    assert.match(today, /160 kB/);
    assert.doesNotMatch(chat, /WARN/);
    assert.match(chat, /256\.4 kB/);
  });

  it("measures a build directory as next build does (gzip, kB = 1000 bytes)", () => {
    const dist = mkdtempSync(path.join(os.tmpdir(), "bundle-budget-"));
    mkdirSync(path.join(dist, "static/chunks/app"), { recursive: true });
    const a = "console.log('a');".repeat(500);
    const b = "console.log('b');".repeat(900);
    writeFileSync(path.join(dist, "static/chunks/a.js"), a);
    writeFileSync(path.join(dist, "static/chunks/app/b.js"), b);
    writeFileSync(
      path.join(dist, "app-build-manifest.json"),
      JSON.stringify({ pages: { "/(app)/today/page": ["static/chunks/a.js", "static/chunks/app/b.js"] } }),
    );
    const expected = gzipSync(a, { level: 9 }).length + gzipSync(b, { level: 9 }).length;
    const sizes = budget.measure(dist, ["/today", "/chat"]);
    assert.equal(sizes["/today"], expected);
    assert.equal(sizes["/chat"], null);
  });

  it("reports and exits 0 on a directory with no build at all", () => {
    const empty = mkdtempSync(path.join(os.tmpdir(), "bundle-budget-empty-"));
    const lines: string[] = budget.report(empty);
    assert.ok(lines.length > 0);
    assert.ok(lines.every((l) => !/WARN .* over/.test(l)));
  });
});
