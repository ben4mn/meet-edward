// Smoke test: confirms the DOM harness (jsdom + tsx + testing-library)
// is wired so component tests can actually render React. If this fails,
// every later .test.tsx in the suite will fail too — fix this first.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render, screen } from "@testing-library/react";

describe("test harness", () => {
  it("renders a React element into jsdom", () => {
    render(<button>poke</button>);
    assert.ok(screen.getByRole("button", { name: "poke" }));
  });

  it("exposes document on globalThis", () => {
    assert.equal(typeof document, "object");
    assert.equal(typeof window, "object");
  });
});
