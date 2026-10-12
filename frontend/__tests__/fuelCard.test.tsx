// The fuel card (Work room) and the Usage panel (2026-09-26): one story —
// a meter with a pace marker, where the week closes, his own work, who
// spent it — and, on the panel, the token counts under it. The footnote
// about how the gate works sits behind "How this works".

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

import { CodexUsageCard } from "../components/settings/CodexUsageCard.tsx";
import { UsagePanel } from "../components/settings/UsagePanel.tsx";

const originalFetch = globalThis.fetch;
const HOUR = 3600e3;

function usagePayload() {
  return {
    status: "ok",
    plan_type: "pro",
    meter: {
      windows: {
        primary: {
          used_percent: 30, window_minutes: 10080, reset_after_seconds: 5 * 3600,
          reset_at: new Date(Date.now() + 5 * HOUR + 30_000).toISOString(), elapsed_fraction: 0.97,
        },
      },
      limit_reached: false,
    },
    background_allowed: true,
    background_ceiling_percent: 90,
    surplus: { state: "spending", target_rate: 0.32, reserve: 3, projected_end: 41, planned_end: 85,
      last_job: "research", last_job_at: new Date(Date.now() - 18 * 60e3).toISOString(), last_job_outcome: "saved 1 document" },
    spend_top: [{ purpose: "graph.streaming.x", pct: 21.4, basis: "meter" }],
  };
}

let cardPayload: () => unknown = usagePayload;

// On the Claude API (2026-10-10) the same card tells the day's spend.
function claudeCardPayload() {
  return {
    status: "available", provider: "anthropic", plan_type: null,
    meter: { windows: {}, limit_reached: false },
    background_allowed: true, background_ceiling_percent: 60,
    surplus: { state: "off", reason: "Claude is paid per token" }, spend_top: [],
    claude: {
      provider: "anthropic", active: true, spent_today_usd: 3.4, cap_usd: 10, background_share: 0.6,
      background_stop_usd: 6, interactive_reserve_usd: 3, hard_stop_usd: 13, percent_of_cap: 34, state: "ok",
      background_allowed: true, by_purpose: { "turn:chat": { calls: 4, cost_usd: 1.9 } },
      credits: { remaining_usd: 87.6, days_left: 12.4, note: "tracked locally; check console.anthropic.com for the authoritative balance" },
    },
  };
}

beforeEach(() => {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    if (url.includes("/api/settings/codex-usage")) return json(cardPayload());
    if (url.includes("/api/usage")) {
      const stats = { input_tokens: 1_200_000, output_tokens: 80_000, requests: 412 };
      return json({
        session_start: new Date(Date.now() - 2 * HOUR).toISOString(),
        providers: { openai: { lifetime: stats, session: stats, by_model: { "gpt-6-astra": { lifetime: stats, session: stats } } } },
      });
    }
    return new Response("{}", { status: 404 });
  }) as typeof fetch;
});

afterEach(() => {
  cardPayload = usagePayload;
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("CodexUsageCard", () => {
  it("tells the week's fuel story", async () => {
    render(<CodexUsageCard />);
    await screen.findByText("Week closes in 5h at 41% — 44% unspent");
    const meter = screen.getByRole("meter");
    assert.equal(meter.getAttribute("aria-valuenow"), "30");
    assert.ok(meter.querySelector("[data-meter-pace]"), "the pace marker is drawn");
    assert.ok(screen.getByText("Chat keeps 3% in reserve"));
    assert.ok(screen.getByText("I'm spending spare fuel on my own work (0.32%/h)."));
    assert.ok(screen.getByText("Last: research · 18m ago — saved 1 document"));
    assert.ok(screen.getByText("our chats"));
    assert.ok(screen.getByText("How this works"));
  });
});

describe("CodexUsageCard on the Claude API", () => {
  it("tells the day's spend and the credits left", async () => {
    cardPayload = claudeCardPayload;
    render(<CodexUsageCard />);
    await screen.findByText("Spent today $3.40 of $10 · credits left ~$88 (~12 days)");
    const meter = screen.getByRole("meter");
    assert.equal(meter.getAttribute("aria-valuenow"), "34");
    assert.ok(screen.getByText("Who spent today"));
    assert.ok(screen.getByText("our chats"));
    assert.ok(screen.getByText("$1.90"));
    assert.ok(screen.getByText(/console\.anthropic\.com/));
    assert.equal(screen.queryByText(/Every Codex answer/), null);
  });
});

describe("UsagePanel", () => {
  it("puts the tokens under the fuel", async () => {
    render(<UsagePanel />);
    await screen.findByText("Week closes in 5h at 41% — 44% unspent");
    await screen.findByText("gpt-6-astra");
    assert.ok(screen.getAllByText("1.3M").length >= 1, "lifetime tokens in and out");
  });
});
