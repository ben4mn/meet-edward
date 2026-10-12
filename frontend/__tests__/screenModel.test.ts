import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  countdownLabel,
  listItems,
  showRenderer,
  type ShowContent,
} from "../app/(display)/display/lib/screenModel.ts";

const show = (over: Partial<ShowContent> = {}): ShowContent => ({
  kind: "text", title: null, source: null, body: "hi", embed_url: null,
  shown_at: "2026-09-07T15:00:00+00:00", expires_at: null, shown_by: "edward", ...over,
});

describe("showRenderer", () => {
  it("uses the household renderers", () => {
    assert.equal(showRenderer(show({ kind: "alert", body: "x" })), "alert");
    assert.equal(showRenderer(show({ kind: "image", source: "https://x/y.jpg" })), "img");
  });
});

describe("countdownLabel", () => {
  const now = Date.parse("2026-09-07T15:00:00Z");
  it("reads in days, hours, minutes and says now once passed", () => {
    assert.equal(countdownLabel("2026-09-09T17:30:00Z", now), "2d 2h");
    assert.equal(countdownLabel("2026-09-07T18:12:00Z", now), "3h 12m");
    assert.equal(countdownLabel("2026-09-07T15:05:00Z", now), "5m");
    assert.equal(countdownLabel("2026-09-07T14:00:00Z", now), "now");
    assert.equal(countdownLabel("garbage", now), "—");
  });
});

describe("listItems", () => {
  it("tolerates strings and objects", () => {
    assert.deepEqual(listItems(["a", { label: "b", detail: "c" }, { label: "" }]), [
      { label: "a", detail: null }, { label: "b", detail: "c" },
    ]);
    assert.deepEqual(listItems(undefined), []);
  });
});
