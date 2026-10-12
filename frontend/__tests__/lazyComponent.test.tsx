// lib/lazyComponent (2026-09-26 perf pass): a component in its own chunk
// that renders at once when the chunk is already here (no fallback frame, so
// nothing on screen changes), stands in with a same-shape fallback when it is
// not, draws the fallback on the server (hydration-safe), and retries a
// failed load on the next mount.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToString } from "react-dom/server";
import { act, cleanup, render } from "@testing-library/react";

import { lazyComponent } from "../lib/lazyComponent.tsx";

afterEach(() => cleanup());

function Real({ name }: { name: string }) {
  return <p data-real="">hello {name}</p>;
}
function Stand({ name }: { name: string }) {
  return <p data-fallback="">waiting for {name}</p>;
}

const has = (sel: string) => document.querySelector(sel) !== null;

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("lazyComponent", () => {
  it("stands in with the fallback (given the props) until the chunk lands, then renders the component", async () => {
    const chunk = deferred<typeof Real>();
    const Lazy = lazyComponent(() => chunk.promise, Stand);
    render(<Lazy name="Ellis" />);
    assert.equal(has("[data-fallback]"), true);
    assert.equal(document.body.textContent, "waiting for Ellis");
    await act(async () => {
      chunk.resolve(Real);
      await chunk.promise;
    });
    assert.equal(has("[data-real]"), true);
    assert.equal(document.body.textContent, "hello Ellis");
  });

  it("once preloaded, the first render is the component itself — no fallback frame", async () => {
    let loads = 0;
    const Lazy = lazyComponent(async () => {
      loads += 1;
      return Real;
    }, Stand);
    await Lazy.preload();
    const seen: string[] = [];
    function Probe() {
      React.useLayoutEffect(() => {
        seen.push(document.body.textContent ?? "");
      });
      return null;
    }
    render(
      <>
        <Lazy name="Rowan" />
        <Probe />
      </>,
    );
    assert.deepEqual(seen, ["hello Rowan"], "the very first commit shows the component");
    assert.equal(loads, 1, "one fetch, shared");
  });

  it("draws the fallback on the server, whatever has loaded (hydration matches)", async () => {
    const Lazy = lazyComponent(async () => Real, Stand);
    await Lazy.preload();
    const html = renderToString(<Lazy name="Ellis" />);
    assert.match(html, /waiting for/);
    assert.doesNotMatch(html, /hello/);
  });

  it("a failed load keeps the fallback, and the next mount tries again", async () => {
    let attempt = 0;
    const Lazy = lazyComponent(async () => {
      attempt += 1;
      if (attempt === 1) throw new Error("chunk failed");
      return Real;
    }, Stand);
    const first = render(<Lazy name="Ellis" />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5));
    });
    assert.equal(has("[data-fallback]"), true, "still standing in");
    first.unmount();
    render(<Lazy name="Ellis" />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5));
    });
    assert.equal(has("[data-real]"), true, "the retry landed");
    assert.equal(attempt, 2);
  });

  it("without a fallback it renders nothing until the chunk lands", async () => {
    const chunk = deferred<typeof Real>();
    const Lazy = lazyComponent(() => chunk.promise);
    const { container } = render(<Lazy name="Ellis" />);
    assert.equal(container.innerHTML, "");
    await act(async () => {
      chunk.resolve(Real);
      await chunk.promise;
    });
    assert.equal(container.textContent, "hello Ellis");
  });
});
