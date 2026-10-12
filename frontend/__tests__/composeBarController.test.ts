// R/G red for the display compose bar's key handling.
//
// The controller is pure logic: given a keyboard event it returns one of
// "submit" / "clear" / "newline" / "ignore". The React component decides
// what to actually do with each (POST, clear textarea, etc).

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  classifyComposeKey,
  type ComposeKeyResult,
} from "../app/(display)/display/lib/composeBarController.ts";

type FakeKeyEvent = Partial<{
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  preventDefault: () => void;
}> & { key: string };

function ev(props: FakeKeyEvent): any {
  return {
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    preventDefault: () => {},
    ...props,
  };
}

describe("classifyComposeKey", () => {
  it("plain Enter submits", () => {
    let prevented = false;
    const r: ComposeKeyResult = classifyComposeKey(
      ev({ key: "Enter", preventDefault: () => (prevented = true) }),
    );
    assert.equal(r, "submit");
    assert.equal(prevented, true);
  });

  it("Cmd+Enter also submits", () => {
    let prevented = false;
    const r = classifyComposeKey(
      ev({ key: "Enter", metaKey: true, preventDefault: () => (prevented = true) }),
    );
    assert.equal(r, "submit");
    assert.equal(prevented, true);
  });

  it("Ctrl+Enter submits (Linux/Win parity)", () => {
    const r = classifyComposeKey(ev({ key: "Enter", ctrlKey: true }));
    assert.equal(r, "submit");
  });

  it("Shift+Enter inserts a newline (does not preventDefault)", () => {
    let prevented = false;
    const r = classifyComposeKey(
      ev({
        key: "Enter",
        shiftKey: true,
        preventDefault: () => (prevented = true),
      }),
    );
    assert.equal(r, "newline");
    assert.equal(prevented, false, "newline must let the textarea handle the key");
  });

  it("Escape clears and prevents default", () => {
    let prevented = false;
    const r = classifyComposeKey(
      ev({ key: "Escape", preventDefault: () => (prevented = true) }),
    );
    assert.equal(r, "clear");
    assert.equal(prevented, true);
  });

  it("ordinary keys are ignored", () => {
    let prevented = false;
    const r = classifyComposeKey(
      ev({ key: "a", preventDefault: () => (prevented = true) }),
    );
    assert.equal(r, "ignore");
    assert.equal(prevented, false);
  });
});
