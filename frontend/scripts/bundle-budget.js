#!/usr/bin/env node
// First-load JS budgets for the key routes, reported after every build.
//
// Why (2026-09-27): /today's first load reached its 160 kB
// target and nothing guarded it — firstLoadBundle.test.ts pins imports, not
// bytes, and the number only ever appeared in `next build`'s route table in
// /tmp/edward-frontend.log. ensure-build.sh (and deploy-build.sh) run this
// after a successful build; a route over its budget prints a WARN line.
//
// It NEVER fails anything: a failed build puts production on `next dev`,
// so a size regression must be a line in the log, not an outage. Always
// exits 0.
//
// The number is the one `next build` prints as "First Load JS" for an app
// route (next 14.1 build/utils.js getJsPageSizeInKb): the gzipped (level 9)
// size of every .js file listed for the route in app-build-manifest.json,
// each counted once, in kB of 1000 bytes. Checked against the 2026-09-26
// production build: /today 159.6, /chat 256.4, /settings 174.3,
// /screen 137.9, /hub 98.8 — exactly the table.
//
// Usage: node scripts/bundle-budget.js [distDir]   (default .next)

const fs = require("fs");
const path = require("path");
const zlib = require("zlib");

// kB of 1000 bytes, as `next build` prints. /today and /chat are the
// targets set in the 2026-09-26 UX pass; the rest are that build's size
// plus 5 %, so they catch a regression without being a new target.
const BUDGETS_KB = {
  "/today": 160,
  "/chat": 260,
  "/settings": 183,
  "/screen": 145,
  "/hub": 104,
};

const TAG = "[bundle-budget]";

/** The route an app-build-manifest key serves, or null for a layout. */
function routeOf(key) {
  const segments = key.split("/").filter(Boolean);
  if (segments[segments.length - 1] !== "page") return null;
  const kept = segments
    .slice(0, -1)
    .filter((s) => !(s.startsWith("(") && s.endsWith(")")) && !s.startsWith("@"));
  return "/" + kept.join("/");
}

/** The route's first-load JS files, each once, in manifest order; null if absent. */
function firstLoadFiles(appBuildManifest, route) {
  const pages = (appBuildManifest && appBuildManifest.pages) || {};
  const key = Object.keys(pages).find((k) => routeOf(k) === route);
  if (!key) return null;
  return [...new Set(pages[key].filter((f) => f.endsWith(".js")))];
}

function gzipBytes(file) {
  return zlib.gzipSync(fs.readFileSync(file), { level: 9 }).length;
}

/** {route: bytes | null} for each route, measured in distDir. */
function measure(distDir, routes) {
  const manifest = JSON.parse(fs.readFileSync(path.join(distDir, "app-build-manifest.json"), "utf8"));
  const sizes = {};
  for (const route of routes) {
    const files = firstLoadFiles(manifest, route);
    sizes[route] = files ? files.reduce((sum, f) => sum + gzipBytes(path.join(distDir, f)), 0) : null;
  }
  return sizes;
}

/** Pure: compare measured bytes against budgets in kB. */
function checkBudgets(sizes, budgetsKb) {
  return Object.keys(budgetsKb).map((route) => {
    const bytes = sizes[route] ?? null;
    const budgetBytes = budgetsKb[route] * 1000;
    return {
      route,
      bytes,
      budgetKb: budgetsKb[route],
      missing: bytes === null,
      over: bytes !== null && bytes > budgetBytes,
    };
  });
}

const kb = (bytes) => `${(bytes / 1000).toFixed(1)} kB`;

/** Pure: one log line per route. */
function formatReport(results) {
  return results.map((r) => {
    if (r.missing) return `${TAG} ${r.route} is not in this build (budget ${r.budgetKb} kB)`;
    if (r.over) {
      return `${TAG} WARN ${r.route} first load ${kb(r.bytes)} is over its ${r.budgetKb} kB budget`;
    }
    return `${TAG} ${r.route} first load ${kb(r.bytes)} (budget ${r.budgetKb} kB)`;
  });
}

/** The report for distDir as lines; never throws. */
function report(distDir, budgetsKb = BUDGETS_KB) {
  try {
    if (!fs.existsSync(path.join(distDir, "app-build-manifest.json"))) {
      return [`${TAG} no app-build-manifest.json in ${distDir}; nothing to measure`];
    }
    return formatReport(checkBudgets(measure(distDir, Object.keys(budgetsKb)), budgetsKb));
  } catch (err) {
    return [`${TAG} could not measure ${distDir}: ${err && err.message ? err.message : err}`];
  }
}

module.exports = { BUDGETS_KB, routeOf, firstLoadFiles, measure, checkBudgets, formatReport, report };

if (require.main === module) {
  for (const line of report(process.argv[2] || ".next")) console.log(line);
  process.exit(0);
}
