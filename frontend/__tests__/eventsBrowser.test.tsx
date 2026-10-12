// Settings → Work → Scheduled (2026-09-26, Jarvis polish).
//
// - Three groups: "Your reminders", "His own wake-ups" (mind wakes and the
//   morning/evening/reflect ticks), and "Housekeeping", folded.
// - Repeats are said in words ("Every day at 7 am"), never a raw cron.
// - A row opens its details; what already happened is under "Past".

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { mockFetch, restoreFetch } from "./panelTestKit.ts";
import { EventsBrowser } from "../components/settings/EventsBrowser.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";
import { pendingDeletes } from "../lib/pendingDeletes.ts";
import type { Call } from "./panelTestKit.ts";

const at = (msFromNow: number) => new Date(Date.now() + msFromNow).toISOString();

function ev(id: string, over: Record<string, unknown>) {
  return {
    id, conversation_id: null, description: id, scheduled_at: at(3600_000), next_fire_at: at(3600_000),
    recurrence_pattern: null, status: "pending", created_by: "edward", delivery_channel: null, last_fired_at: null,
    fire_count: 0, last_result: null, created_at: at(-86400_000), updated_at: at(-86400_000), ...over,
  };
}

const EVENTS = [
  ev("ev-vet", { description: "Remind Ellis to call the vet", created_by: "user", conversation_id: "c-biscuit-vet", next_fire_at: at(3 * 3600_000) }),
  ev("ev-morning", { description: "Morning wake — the mind decides", recurrence_pattern: "0 7 * * *", fire_count: 41 }),
  ev("ev-parker", { description: "Check whether Parker confirmed", conversation_id: "edward-mind" }),
  ev("ev-pulse", { description: "expression_pulse — Edward decides what his widget and the household screen show", recurrence_pattern: "*/15 * * * *" }),
  ev("ev-failed", { description: "Text Jordan the cabin address", status: "failed", last_fired_at: at(-2 * 86400_000), last_result: "Tool error: blocked — stood down in that chat" }),
];

beforeEach(() => {
  mockFetch((url) => {
    if (url.includes("/api/events")) return EVENTS;
    return undefined;
  });
});

afterEach(() => {
  cleanup();
  restoreFetch();
});

describe("EventsBrowser", () => {
  it("splits reminders, his wake-ups and folded housekeeping", async () => {
    render(<EventsBrowser />);
    const yours = await screen.findByRole("region", { name: "Your reminders" });
    assert.ok(within(yours).getByText("Remind Ellis to call the vet"));
    const wakes = screen.getByRole("region", { name: "His own wake-ups" });
    assert.ok(within(wakes).getByText("Morning wake"));
    assert.ok(within(wakes).getByText("Check whether Parker confirmed"));
    assert.match(wakes.textContent ?? "", /Every day at 7 am/);
    assert.doesNotMatch(document.body.textContent ?? "", /0 7 \* \* \*|expression_pulse/);

    const fold = screen.getByRole("button", { name: /Housekeeping/ });
    assert.equal(fold.getAttribute("aria-expanded"), "false");
    assert.equal(screen.queryByText("Decide what my screens show") === null, true);
    fireEvent.click(fold);
    assert.ok(await screen.findByText("Decide what my screens show"));
  });

  it("opens a reminder's details with a way back to its thread", async () => {
    render(<EventsBrowser />);
    fireEvent.click((await screen.findByText("Remind Ellis to call the vet")).closest("button") as HTMLElement);
    const dialog = await screen.findByRole("dialog");
    assert.ok(within(dialog).getByRole("link", { name: /Open the conversation/ }).getAttribute("href")?.includes("/chat?c=c-biscuit-vet"));
    assert.ok(within(dialog).getByRole("button", { name: "Cancel it" }));
  });

  it("keeps what already happened under Past, failures said plainly", async () => {
    render(<EventsBrowser />);
    await screen.findByText("Remind Ellis to call the vet");
    fireEvent.click(screen.getByRole("radio", { name: /Past/ }));
    const row = (await screen.findByText("Text Jordan the cabin address")).closest("button") as HTMLElement;
    assert.ok(within(row).getByText("Failed"));
  });
});

// The list API filters by status, as the backend does: Upcoming asks for
// pending and processing rows, Past for everything newest first.
const OLD = ev("ev-old", { description: "Water the plants", status: "completed", last_fired_at: at(-40 * 86400_000) });
function statusAwareFetch(): Call[] {
  return mockFetch((url, method) => {
    if (method === "PATCH" || method === "DELETE") return { status: "ok" };
    const one = url.match(/\/api\/events\/([^?/]+)$/);
    if (one) return [...EVENTS, OLD].find((e) => e.id === one[1]) ?? new Response("{}", { status: 404 });
    if (url.includes("/api/events")) {
      const status = new URL(url, "http://x").searchParams.get("status");
      return status ? EVENTS.filter((e) => e.status === status) : EVENTS;
    }
    return undefined;
  });
}

describe("EventsBrowser deep links (?id= from the Work room)", () => {
  it("opens a finished event under Past", async () => {
    statusAwareFetch();
    render(<EventsBrowser initialId="ev-failed" />);
    const dialog = await screen.findByRole("dialog");
    assert.ok(within(dialog).getByText("Text Jordan the cabin address"));
    assert.equal(screen.getByRole("radio", { name: /Past/ }).getAttribute("aria-checked"), "true");
  });

  it("opens an upcoming reminder where it is", async () => {
    statusAwareFetch();
    render(<EventsBrowser initialId="ev-vet" />);
    const dialog = await screen.findByRole("dialog");
    assert.ok(within(dialog).getByText("Remind Ellis to call the vet"));
    assert.equal(screen.getByRole("radio", { name: /Upcoming/ }).getAttribute("aria-checked"), "true");
  });

  it("fetches one that is on neither page", async () => {
    const calls = statusAwareFetch();
    render(<EventsBrowser initialId="ev-old" />);
    const dialog = await screen.findByRole("dialog");
    assert.ok(within(dialog).getByText("Water the plants"));
    assert.ok(calls.some((c) => c.url.endsWith("/api/events/ev-old")));
  });
});

describe("EventsBrowser cancel and delete with Undo", () => {
  it("keeps a cancelled reminder gone once it commits, and says when Undo is too late", async () => {
    const calls = statusAwareFetch();
    render(
      <ToastProvider>
        <EventsBrowser />
      </ToastProvider>,
    );
    const yours = await screen.findByRole("region", { name: "Your reminders" });
    fireEvent.click(within(yours).getByText("Remind Ellis to call the vet").closest("button") as HTMLElement);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel it" }));
    const undo = await screen.findByRole("button", { name: /Undo/ });
    await act(async () => {
      pendingDeletes.flush();
      await new Promise((r) => setTimeout(r, 20));
    });
    assert.ok(calls.some((c) => c.method === "PATCH" && c.url.includes("/api/events/ev-vet")));
    // (a boolean, not the element: the runner would inspect a jsdom node for minutes)
    const back = within(screen.getByRole("region", { name: "Your reminders" })).queryByText("Remind Ellis to call the vet") !== null;
    assert.equal(back, false, "the row does not come back when the cancel commits");
    fireEvent.click(undo);
    assert.ok(await screen.findByText(/Too late/));
  });
});
