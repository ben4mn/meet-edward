// R/G for the small pure helpers added alongside the ComposeBar
// dismiss / open-in-inbox affordance. Keeping these in lib/ so the
// React component just renders.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  displayInboxHref,
  QUICK_ACTIONS,
} from "../app/(display)/display/lib/composeBarController.ts";

describe("displayInboxHref", () => {
  it("returns null when no conversation id is known", () => {
    assert.equal(displayInboxHref(null), null);
    assert.equal(displayInboxHref(undefined), null);
    assert.equal(displayInboxHref(""), null);
  });

  it("builds a /chat?c=<id> deep link when given an id", () => {
    assert.equal(
      displayInboxHref("abc-123"),
      "/chat?c=abc-123",
    );
  });

  it("URI-encodes the id (defense in depth)", () => {
    assert.equal(
      displayInboxHref("with space"),
      "/chat?c=with%20space",
    );
  });
});

describe("QUICK_ACTIONS", () => {
  it("ships exactly the three preset prompts", () => {
    assert.equal(QUICK_ACTIONS.length, 3);
  });

  it("each action has a short label and a prompt body", () => {
    for (const a of QUICK_ACTIONS) {
      assert.ok(a.label.length > 0 && a.label.length <= 24, `bad label: ${a.label}`);
      assert.ok(a.prompt.length > 0);
    }
  });
});
