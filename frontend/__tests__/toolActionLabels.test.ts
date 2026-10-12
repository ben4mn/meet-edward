// Every action of the toolset's action tools has a written label (2026-10-11).
//
// The backend's action tuples (MESSAGES_ACTIONS, SCHEDULE_ACTIONS,
// MEMORY_ACTIONS, SCREEN_ACTIONS in backend/services/toolset/*.py) are the
// source of truth: a new action — mail_search, an event write — that the
// work rail can't name reads as the tool's bare label ("Read messages"), so
// this fails until ACTION_SPECS has it.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import { hasActionLabel } from "../lib/toolLabels.ts";

const toolsetDir = path.resolve(import.meta.dirname, "../../backend/services/toolset");
const TUPLES: [string, string, string][] = [
  ["messages", "messaging.py", "MESSAGES_ACTIONS"],
  ["schedule", "schedule.py", "SCHEDULE_ACTIONS"],
  ["memory", "memory.py", "MEMORY_ACTIONS"],
  ["screen", "screen.py", "SCREEN_ACTIONS"],
];

function actionsOf(file: string, name: string): string[] {
  const src = fs.readFileSync(path.join(toolsetDir, file), "utf8");
  const m = new RegExp(`^${name}\\s*=\\s*\\(([^)]*)\\)`, "m").exec(src);
  assert.ok(m, `${name} not found in ${file}`);
  return [...m[1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]);
}

describe("every backend action has a work-rail label", () => {
  for (const [tool, file, tuple] of TUPLES) {
    it(`${tool} (${tuple})`, () => {
      const actions = actionsOf(file, tuple);
      assert.ok(actions.length > 0, `${tuple} is empty`);
      const missing = actions.filter((a) => !hasActionLabel(tool, a));
      assert.deepEqual(missing, [], `${tool} actions without a label: ${missing.join(", ")}`);
    });
  }
  it("hasActionLabel says no for what isn't there", () => {
    assert.equal(hasActionLabel("messages", "teleport"), false);
    assert.equal(hasActionLabel("nope", "read"), false);
  });
});
