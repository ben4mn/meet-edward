import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { buildTodayActionSignal } from "../lib/signals.ts";

const need = { id: "need-1", kind: "dose", tone: "warn", priority: 1, title: "Biscuit 18:00", detail: "", href: "/hub/pets", actions: [] } as const;
const action = { id: "a1", label: "Given", method: "POST", url: "/api/hub/care/c1/log", primary: true };

describe("buildTodayActionSignal", () => {
  it("records a successful action with the need kind and label", () => {
    const s = buildTodayActionSignal(need, action, true);
    assert.equal(s.kind, "today_action");
    assert.equal(s.ref_id, "need-1");
    assert.deepEqual(s.detail, { need_kind: "dose", action: "Given", url: "/api/hub/care/c1/log" });
  });
  it("records a failure with the error text, trimmed", () => {
    const s = buildTodayActionSignal(need, action, false, "x".repeat(500));
    assert.equal(s.kind, "today_action_failed");
    assert.equal((s.detail.error as string).length, 200);
  });
});
