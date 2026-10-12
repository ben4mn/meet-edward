// lib/utils.cn must know the type scale (text-body, text-title1 …) and the
// elevation/motion tokens, or `cn("text-body", "text-text-primary")` drops the
// size — every component imports cn from here, not from components/ui.
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { cn } from "../lib/utils";

describe("lib/utils cn", () => {
  it("keeps a type-scale size beside a text colour", () => {
    assert.equal(cn("text-body", "text-text-primary"), "text-body text-text-primary");
  });
  it("keeps an elevation shadow beside a shadow colour", () => {
    assert.equal(cn("shadow-e1", "shadow-black/20"), "shadow-e1 shadow-black/20");
  });
  it("still lets a later size win over an earlier one", () => {
    assert.equal(cn("text-body", "text-title1"), "text-title1");
  });
});
