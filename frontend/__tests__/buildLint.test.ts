/**
 * `next build` lints, and a lint ERROR fails the build — the frontend then
 * falls back to `next dev` (11.5 MB cold loads, stale PWA chunks). Neither tsc
 * nor the unit tests catch an unescaped apostrophe in JSX (MindPanel's "I'll
 * keep trying" did exactly that, review 2026-09-26), so the directories the
 * build lints (next lint's defaults: app, components, lib) are linted here
 * with the same config.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { ESLint } from "eslint";

const root = path.resolve(__dirname, "..");

test("what next build lints has no lint errors", async () => {
  const eslint = new ESLint({ cwd: root });
  const results = await eslint.lintFiles(["app", "components", "lib"]);
  const errors = results.flatMap((r) =>
    r.messages
      .filter((m) => m.severity === 2)
      .map((m) => `${path.relative(root, r.filePath)}:${m.line}:${m.column} ${m.ruleId} ${m.message}`),
  );
  assert.deepEqual(errors, []);
});
