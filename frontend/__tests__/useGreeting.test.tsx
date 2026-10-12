// Edward's greeting on Today (UX pass 2026-09-26): loading from the first
// render (never a flash of "Hey."), and fetched again when Ellis comes back
// to the app and the line is over 30 minutes old or the part of the day
// has changed ("Morning, Ellis" at 7 pm is a lie).

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";

import { GREETING_MAX_AGE_MS, partOfDay, shouldRefetchGreeting, useGreeting } from "../lib/useGreeting.ts";

afterEach(cleanup);

describe("partOfDay", () => {
  it("morning, afternoon, evening, night", () => {
    assert.deepEqual([5, 11, 12, 16, 17, 21, 22, 2].map(partOfDay), [
      "morning",
      "morning",
      "afternoon",
      "afternoon",
      "evening",
      "evening",
      "night",
      "night",
    ]);
  });
});

describe("shouldRefetchGreeting", () => {
  const tz = "America/Chicago";
  const at = new Date("2026-09-26T14:00:00Z").getTime(); // 9:00 am Springfield
  it("never fetched → fetch", () => {
    assert.equal(shouldRefetchGreeting(null, at, tz), true);
  });
  it("fresh and the same part of the day → keep", () => {
    assert.equal(shouldRefetchGreeting(at, at + 10 * 60_000, tz), false);
  });
  it("older than half an hour → fetch", () => {
    assert.equal(GREETING_MAX_AGE_MS, 30 * 60_000);
    assert.equal(shouldRefetchGreeting(at, at + GREETING_MAX_AGE_MS + 1, tz), true);
  });
  it("the part of the day changed → fetch, however fresh", () => {
    const lateMorning = new Date("2026-09-26T16:55:00Z").getTime(); // 11:55
    assert.equal(shouldRefetchGreeting(lateMorning, lateMorning + 10 * 60_000, tz), true);
  });
});

function Probe(props: Parameters<typeof useGreeting>[1] & { enabled?: boolean }) {
  const { enabled = true, ...opts } = props;
  const { text, loading } = useGreeting(enabled, opts);
  return (
    <p>
      <span data-testid="text">{text}</span>
      <span data-testid="loading">{String(loading)}</span>
    </p>
  );
}

async function* chunks(parts: string[]) {
  for (const p of parts) yield p;
}

describe("useGreeting", () => {
  it("is loading from the first render", () => {
    let calls = 0;
    render(
      <Probe
        stream={() => {
          calls += 1;
          return chunks([]);
        }}
        once={() => new Promise(() => {})}
      />,
    );
    assert.equal(screen.getByTestId("loading").textContent, "true");
    assert.equal(calls, 1);
  });

  it("is not loading when disabled", () => {
    render(<Probe enabled={false} stream={() => chunks([])} once={async () => ""} />);
    assert.equal(screen.getByTestId("loading").textContent, "false");
  });

  it("streams the line in, then refetches on return once it is stale", async () => {
    let clock = new Date("2026-09-26T14:00:00Z").getTime();
    const lines = [["Morning, ", "Ellis."], ["Still morning."]];
    let n = 0;
    render(<Probe stream={() => chunks(lines[n++] ?? [])} once={async () => ""} now={() => clock} />);
    await screen.findByText("Morning, Ellis.");
    assert.equal(screen.getByTestId("loading").textContent, "false");

    // Back soon: nothing new.
    clock += 5 * 60_000;
    await act(async () => {
      document.dispatchEvent(new window.Event("visibilitychange"));
    });
    assert.equal(n, 1);

    // Back after 40 minutes: a new line replaces the old one (the old stays until then).
    clock += 40 * 60_000;
    await act(async () => {
      document.dispatchEvent(new window.Event("visibilitychange"));
    });
    await screen.findByText("Still morning.");
    assert.equal(n, 2);
  });
});

// Review 2026-09-26 (greeting-stale-after-action): after Ellis resolves what
// the greeting names, Today asks for a fresh line at once, however young
// the one on screen is.
import { renderHook } from "@testing-library/react";

describe("useGreeting refresh", () => {
  it("fetches again on demand, asking the server for a fresh line", async () => {
    const asks: (boolean | undefined)[] = [];
    let line = "Evening, Ellis. Biscuit's meds are still waiting.";
    const { result } = renderHook(() =>
      useGreeting(true, {
        stream: (o?: { fresh?: boolean }) => {
          asks.push(o?.fresh);
          return chunks([line]);
        },
        once: async () => "",
      }),
    );
    await act(async () => new Promise((r) => setTimeout(r, 0)));
    assert.equal(result.current.text, "Evening, Ellis. Biscuit's meds are still waiting.");
    line = "Evening, Ellis. Quiet night.";
    await act(async () => {
      await result.current.refresh();
    });
    assert.equal(result.current.text, "Evening, Ellis. Quiet night.");
    assert.deepEqual(asks, [undefined, true]);
  });
});
