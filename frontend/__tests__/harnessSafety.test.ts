// The fixture harness must never touch the production build (2026-09-26).
//
// Run from /Users/me/edward/frontend, `next dev` would write into the
// same .next that `next start` serves on :3000, and next-pwa would rewrite
// public/sw.js with a dev worker that every phone then installs. The harness
// builds into its own directory, turns next-pwa off, and refuses to start in
// the checkout that is serving :3000.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";

const ROOT = path.resolve(__dirname, "..");

// next-pwa is stubbed in the child so its options (where it writes sw.js)
// can be read: the real plugin closes over them.
const READ_CONFIG = `
const Module = require("module");
const load = Module._load;
let pwa = null;
Module._load = function (req, ...rest) {
  if (req === "next-pwa") return (opts) => { pwa = opts; return (c) => c; };
  return load.call(this, req, ...rest);
};
const c = require("./next.config.js");
process.stdout.write(JSON.stringify({ distDir: c.distDir ?? null, pwaDest: pwa && pwa.dest }));
`;

function configWith(env: Record<string, string>): { distDir?: string; pwaDest?: string } {
  const out = execFileSync(process.execPath, ["-e", READ_CONFIG], {
    cwd: ROOT,
    env: { ...process.env, EDWARD_BUILD_ID: "test-build", ...env },
    encoding: "utf8",
  });
  return JSON.parse(out);
}

describe("harness safety", () => {
  it("next.config builds into NEXT_DIST_DIR when the harness sets it", () => {
    assert.equal(configWith({ NEXT_DIST_DIR: ".next-harness" }).distDir, ".next-harness");
  });

  it("production keeps the default .next", () => {
    const d = configWith({ NEXT_DIST_DIR: "" }).distDir;
    assert.ok(d === null || d === ".next", `distDir was ${d}`);
  });

  it("next-pwa is disabled under the harness, so public/sw.js is never rewritten by next dev", () => {
    const src = readFileSync(path.join(ROOT, "next.config.js"), "utf8");
    assert.match(src, /disable:\s*process\.env\.EDWARD_HARNESS\s*===\s*["']1["']/);
  });

  it("run-harness.sh sets its own build dir, disables the PWA, and refuses the :3000 checkout", () => {
    const sh = readFileSync(path.join(ROOT, "scripts/mock-api/run-harness.sh"), "utf8");
    assert.match(sh, /NEXT_DIST_DIR=\.next-harness/);
    assert.match(sh, /EDWARD_HARNESS=1/);
    assert.match(sh, /serving :3000|refus/i);
  });
});

// A staged deploy build (deploy-build.sh, NEXT_DIST_DIR=.next-staging) runs
// while the old `next start` keeps serving public/ from disk. next-pwa wrote
// its sw.js and workbox runtime into public/ at the client compile, so for
// the minutes the build ran the old server handed out a worker that
// precaches manifests it cannot serve, and the install failed (batch D,
// 2026-09-27). Staged, it writes into the build's own EDWARD_PWA/, which
// ensure-build.sh's swap installs into public/.
describe("staged deploy build", () => {
  const sh = readFileSync(path.join(ROOT, "ensure-build.sh"), "utf8");
  const staging = /^STAGING_DIR="([^"]+)"/m.exec(sh)?.[1];
  const pwaDir = /^PWA_DIR="([^"]+)"/m.exec(sh)?.[1];

  it("next-pwa writes a staged build's worker into the build's own EDWARD_PWA/, never public/", () => {
    assert.equal(staging, ".next-staging");
    assert.equal(pwaDir, "EDWARD_PWA");
    const c = configWith({ NEXT_DIST_DIR: staging! });
    assert.equal(c.distDir, staging);
    assert.equal(c.pwaDest, `${staging}/${pwaDir}`);
  });

  it("every other build (in place, the harness, a scratch dist) keeps public/", () => {
    assert.equal(configWith({ NEXT_DIST_DIR: "" }).pwaDest, "public");
    assert.equal(configWith({ NEXT_DIST_DIR: ".next-harness" }).pwaDest, "public");
    assert.equal(configWith({ NEXT_DIST_DIR: ".next-integ" }).pwaDest, "public");
  });

  // next build adds "<distDir>/types/**/*.ts" to tsconfig.json when the
  // include is missing, rewriting a tracked file; deploy-build.sh has to
  // put it back. With the entry already there, nothing is rewritten.
  it("tsconfig.json already includes the staged build's route types", () => {
    const ts = JSON.parse(readFileSync(path.join(ROOT, "tsconfig.json"), "utf8")) as { include: string[] };
    assert.ok(ts.include.includes(".next-harness/types/**/*.ts"));
    assert.ok(ts.include.includes(`${staging}/types/**/*.ts`), JSON.stringify(ts.include));
  });
});
