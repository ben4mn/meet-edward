import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { errorText } from "../lib/apiError.ts";

describe("errorText", () => {
  it("passes strings through", () => {
    assert.equal(errorText("Push not configured", "x"), "Push not configured");
  });
  it("renders FastAPI validation lists instead of [object Object]", () => {
    const detail = [{ loc: ["body"], msg: "Field required", type: "missing" }];
    assert.equal(errorText(detail, "x"), "body: Field required");
  });
  it("never yields [object Object]", () => {
    assert.notEqual(errorText({ foo: 1 }, "x"), "[object Object]");
    assert.equal(errorText({ message: "boom" }, "x"), "boom");
  });
  it("falls back when empty", () => {
    assert.equal(errorText(undefined, "HTTP 500"), "HTTP 500");
    assert.equal(errorText("", "HTTP 500"), "HTTP 500");
  });
});
