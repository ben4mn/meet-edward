// The heartbeat panel's "Show message text" privacy switch (2026-09-26).
// It used to live inside the digest only, so the Full log and the event
// detail printed other people's words no matter what it said. One store
// on one localStorage key now drives every place that shows a message.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";

import { SHOW_TEXT_KEY, useShowMessageText, readShowText } from "../lib/showMessageText.ts";

// Node 25 has its own inert global `localStorage`; give the page a real one.
class MemoryStorage {
  private m = new Map<string, string>();
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  setItem(k: string, v: string) { this.m.set(k, String(v)); }
  removeItem(k: string) { this.m.delete(k); }
  clear() { this.m.clear(); }
}
const storage = new MemoryStorage();
Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true, writable: true });
Object.defineProperty(window, "localStorage", { value: storage, configurable: true, writable: true });

function Probe({ id }: { id: string }) {
  const [show, setShow] = useShowMessageText();
  return (
    <button type="button" data-testid={id} onClick={() => setShow(!show)}>
      {show ? "shown" : "hidden"}
    </button>
  );
}

beforeEach(() => localStorage.clear());
afterEach(cleanup);

describe("useShowMessageText", () => {
  it("is off by default", () => {
    render(<Probe id="a" />);
    assert.equal(screen.getByTestId("a").textContent, "hidden");
    assert.equal(readShowText(), false);
  });

  it("reads the stored choice", () => {
    localStorage.setItem(SHOW_TEXT_KEY, "1");
    render(<Probe id="a" />);
    assert.equal(screen.getByTestId("a").textContent, "shown");
  });

  it("one switch flips every reader on the page and is remembered", () => {
    render(
      <>
        <Probe id="digest" />
        <Probe id="log" />
      </>,
    );
    act(() => screen.getByTestId("digest").click());
    assert.equal(screen.getByTestId("digest").textContent, "shown");
    assert.equal(screen.getByTestId("log").textContent, "shown", "the log follows the digest's switch");
    assert.equal(localStorage.getItem(SHOW_TEXT_KEY), "1");
    act(() => screen.getByTestId("log").click());
    assert.equal(screen.getByTestId("digest").textContent, "hidden");
    assert.equal(localStorage.getItem(SHOW_TEXT_KEY), "0");
  });

  it("keeps the key the digest always used", () => {
    assert.equal(SHOW_TEXT_KEY, "edward:heartbeatShowText");
  });
});
