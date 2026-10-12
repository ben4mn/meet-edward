// Unit G of the proactivity rebuild (2026-10-11): goals and ideas leave
// Today. In two weeks Ellis set 0 goals and Edward left 1 idea. The goal page,
// the Goals and Ideas sections, the palette's goals group and the goal and
// idea suggestions on an empty chat go; `/chat?prompt=` stays
// (lib/chatPrompt.ts). An older /api/today that still sends `goals` /
// `ideas` parses fine — the fields are dropped.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

import { normalizeTodayFeed } from "../components/today/todayModel.ts";
import { chatSuggestions } from "../lib/chatSuggestions.ts";
import { buildPalette } from "../components/layout/commandPaletteModel.ts";

const at = (p: string) => new URL(`../${p}`, import.meta.url);

describe("goals and ideas are gone from the app", () => {
  it("their files are deleted", () => {
    for (const f of [
      "components/today/GoalsSection.tsx",
      "components/today/GoalView.tsx",
      "components/today/GoalRing.tsx",
      "components/today/IdeasSection.tsx",
      "app/(app)/today/goals/[id]/page.tsx",
      "lib/goals.ts",
      "lib/ideas.ts",
      "lib/goalCounts.ts",
    ]) {
      assert.equal(existsSync(at(f)), false, f);
    }
  });

  it("Today neither imports nor renders them", () => {
    const view = readFileSync(at("components/today/TodayView.tsx"), "utf8");
    assert.doesNotMatch(view, /GoalsSection|IdeasSection|listGoals|goalCounts/);
    const api = readFileSync(at("lib/productApi.ts"), "utf8");
    assert.doesNotMatch(api, /\/api\/goals|\/api\/ideas/);
  });

  it("an older feed with goals and ideas parses without them", () => {
    const f = normalizeTodayFeed({
      generated_at: "2026-10-11T15:00:00Z",
      needs: [],
      goals: [{ id: "g1", title: "Run", status: "active" }],
      ideas: [{ id: "i1", title: "Try", prompt: "x" }],
    }) as unknown as Record<string, unknown>;
    assert.equal("goals" in f, false);
    assert.equal("ideas" in f, false);
  });

  it("an empty chat suggests the top need, never a goal or an idea", () => {
    const feed = {
      needs: [{ id: "n1", kind: "dose", title: "Biscuit's 6 pm dose", detail: "", actions: [] }],
      goals: [{ id: "g1", title: "Run the half", status: "active" }],
      ideas: [{ id: "i1", title: "A sitter sheet", prompt: "Make one" }],
    } as never;
    assert.deepEqual(chatSuggestions(feed).map((s) => s.kind), ["need"]);
  });

  it("the palette has no goals group", () => {
    const { groups } = buildPalette({ query: "run", actions: [], conversations: [], settings: [] });
    assert.equal(groups.some((g) => (g.id as string) === "goals"), false);
  });
});
