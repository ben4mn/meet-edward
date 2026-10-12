// ?id= deep links in the settings panels (2026-09-26). Pure.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { readParam, withoutParams } from "../lib/deepLink.ts";

describe("readParam", () => {
  it("reads a trimmed value or null", () => {
    assert.equal(readParam(new URLSearchParams("id=m-01"), "id"), "m-01");
    assert.equal(readParam(new URLSearchParams("id=%20"), "id"), null);
    assert.equal(readParam(null, "id"), null);
    assert.equal(readParam(undefined, "id"), null);
  });
});

describe("withoutParams", () => {
  it("drops the named params and keeps the rest", () => {
    assert.equal(withoutParams("/settings/dreaming?flag=fl-1&ask=rewrite&tab=flags", ["flag", "ask"]), "/settings/dreaming?tab=flags");
    assert.equal(withoutParams("/settings/memories?id=m-01", ["id"]), "/settings/memories");
    assert.equal(withoutParams("/settings/memories", ["id"]), "/settings/memories");
    assert.equal(withoutParams("/settings/files?id=f#top", ["id"]), "/settings/files#top");
  });
});
