// R/G red: the Codex usage card reads the real meter.
//
// The card used to render an inference ("throttled until", parsed from the
// last error string) and a disclaimer that no meter exists. It does: every
// Codex response reports used-percent / window / reset. This pure module
// turns the /api/settings/codex-usage payload into the lines the card shows.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { describeCodexUsage, describeSurplus, spenderLabel } from "../lib/codexUsage.ts";

const now = Date.parse("2026-09-11T18:00:00Z");
const week = 7 * 24 * 3600;

function payload(extra: Record<string, unknown> = {}) {
  return {
    status: "available",
    plan_type: "pro",
    meter: {
      windows: {
        primary: {
          used_percent: 22,
          window_minutes: 10080,
          reset_after_seconds: 438000,
          reset_at: new Date(now + 438000 * 1000).toISOString(),
          elapsed_fraction: 0.28,
        },
      },
      age_seconds: 30,
    },
    background_allowed: true,
    background_reason: "primary 22% used (pace line 43%)",
    ...extra,
  };
}

describe("describeCodexUsage", () => {
  it("summarises a healthy weekly window", () => {
    const d = describeCodexUsage(payload(), now);
    assert.equal(d.tone, "ok");
    assert.equal(d.headline, "22% of the weekly window used");
    assert.equal(d.windows.length, 1);
    assert.equal(d.windows[0].label, "Weekly window");
    assert.equal(d.windows[0].usedPercent, 22);
    assert.match(d.windows[0].resets, /^resets in 5d/);
    assert.equal(d.background, "Background work: on pace — primary 22% used (pace line 43%)");
    assert.equal(d.plan, "pro");
  });

  it("names shorter windows by their length", () => {
    const d = describeCodexUsage(
      payload({
        meter: {
          windows: {
            primary: { used_percent: 60, window_minutes: 300, reset_after_seconds: 3600, reset_at: new Date(now + 3600e3).toISOString(), elapsed_fraction: 0.8 },
            secondary: { used_percent: 10, window_minutes: 10080, reset_after_seconds: week, reset_at: new Date(now + week * 1000).toISOString(), elapsed_fraction: 0 },
          },
        },
      }),
      now,
    );
    assert.deepEqual(d.windows.map((w) => w.label), ["5h window", "Weekly window"]);
    assert.equal(d.headline, "60% of the 5h window used");
  });

  it("is a warning when background work is paused by pace", () => {
    const d = describeCodexUsage(
      payload({ background_allowed: false, background_reason: "primary window at 60% is ahead of pace (35% line)" }),
      now,
    );
    assert.equal(d.tone, "warn");
    assert.equal(d.background, "Background work: paused — primary window at 60% is ahead of pace (35% line)");
  });

  it("is bad when the cap is reached, and says when it lifts", () => {
    const d = describeCodexUsage(
      payload({
        status: "limit_reached",
        meter: { windows: { primary: { used_percent: 100, window_minutes: 10080, reset_after_seconds: 2 * 86400 + 3600, reset_at: new Date(now + (2 * 86400 + 3600) * 1000).toISOString(), elapsed_fraction: 0.7 } }, limit_reached: true },
        background_allowed: false,
        background_reason: "primary window exhausted (100% used); resets in 2d 1h",
      }),
      now,
    );
    assert.equal(d.tone, "bad");
    assert.equal(d.headline, "Usage cap reached · resets in 2d 1h");
  });

  it("is bad when the meter says the limit is reached even below 100%", () => {
    const d = describeCodexUsage(
      payload({ meter: { windows: { primary: { used_percent: 97, window_minutes: 10080, reset_after_seconds: 3600, reset_at: new Date(now + 3600e3).toISOString() } }, limit_reached: true } }),
      now,
    );
    assert.equal(d.tone, "bad");
    assert.match(d.headline, /^Usage cap reached/);
  });

  it("copes with no meter", () => {
    const d = describeCodexUsage(payload({ meter: { windows: {} }, plan_type: null, background_reason: "no meter yet" }), now);
    assert.equal(d.tone, "neutral");
    assert.equal(d.headline, "No usage meter yet");
    assert.equal(d.windows.length, 0);
    assert.equal(d.plan, null);
  });
});

// The surplus queue (2026-09-24): Edward spends the week's spare quota on
// work with an output, and the card says where the week is heading, what
// the queue is doing, the last job it ran and who spent the week.

function surplus(extra: Record<string, unknown> = {}) {
  return {
    state: "spending",
    reason: "40% used, 30.0% headroom over a 20.0% chat reserve",
    used: 40,
    reserve: 20,
    headroom: 30,
    target_rate: 0.4,
    r_int: 0.25,
    hours_to_reset: 84,
    projected_end: 88.4,
    planned_end: 97,
    last_job: "research",
    last_job_at: new Date(now - 20 * 60 * 1000).toISOString(),
    last_job_outcome: "done",
    ...extra,
  };
}

describe("describeSurplus", () => {
  it("is null without a surplus block", () => {
    assert.equal(describeSurplus(payload(), now), null);
  });

  it("says where the week lands, what the queue does and what ran last", () => {
    const s = describeSurplus(payload({ surplus: surplus() }), now)!;
    assert.equal(s.projected, "Projected 88% at reset");
    assert.equal(s.state, "Surplus: spending 0.40%/h · chat keeps 20%");
    assert.equal(s.tone, "ok");
    assert.equal(s.lastJob, "Last job: research · 20m ago");
  });

  it("falls back to the plan when there is no burn to project", () => {
    const s = describeSurplus(payload({ surplus: surplus({ projected_end: null }) }), now)!;
    assert.equal(s.projected, "Plan: 97% at reset");
  });

  it("names each state", () => {
    const on = describeSurplus(payload({ surplus: surplus({ state: "on_pace" }) }), now)!;
    assert.equal(on.state, "Surplus: on pace (target 0.40%/h)");
    const waiting = describeSurplus(
      payload({ surplus: surplus({ state: "waiting", reason: "no headroom under the 97% target" }) }),
      now,
    )!;
    assert.equal(waiting.state, "Surplus: waiting — no headroom under the 97% target");
    assert.equal(waiting.tone, "neutral");
    const off = describeSurplus(payload({ surplus: surplus({ state: "off" }) }), now)!;
    assert.equal(off.state, "Surplus: off (EDWARD_SURPLUS=off)");
    const running = describeSurplus(payload({ surplus: surplus({ state: "running" }) }), now)!;
    assert.equal(running.state, "Surplus: a job is running now");
  });

  it("marks a failed last job", () => {
    const s = describeSurplus(
      payload({ surplus: surplus({ last_job: "dossiers", last_job_outcome: "failed", last_job_at: new Date(now - 3 * 3600e3).toISOString() }) }),
      now,
    )!;
    assert.equal(s.lastJob, "Last job: dossiers · 3h ago (failed)");
  });

  it("lists the top three spenders with friendly names", () => {
    const s = describeSurplus(
      payload({
        surplus: surplus(),
        spend_top: [
          { purpose: "graph.streaming._stream_llm_with_events", pct: 31.24, basis: "meter" },
          { purpose: "surplus:research", pct: 6.5, basis: "meter" },
          { purpose: "memory_service.extract_memories", pct: 4.0, basis: "meter" },
          { purpose: "expression_service.run_expression_pulse", pct: 1.0, basis: "meter" },
        ],
      }),
      now,
    )!;
    assert.deepEqual(s.spenders, [
      { label: "our chats", pct: "31.2%" },
      { label: "my research", pct: "6.5%" },
      { label: "remembering", pct: "4.0%" },
    ]);
  });

  it("says when the shares are of tokens, not of the meter", () => {
    const s = describeSurplus(
      payload({ surplus: surplus(), spend_top: [{ purpose: "surplus:dossiers", pct: 12, basis: "tokens" }] }),
      now,
    )!;
    assert.deepEqual(s.spenders, [{ label: "notes on people", pct: "12% of tokens" }]);
  });
});

describe("spenderLabel", () => {
  it("maps known purposes and keeps the rest", () => {
    assert.equal(spenderLabel("surplus:consolidation_deep"), "deep memory tidy");
    assert.equal(spenderLabel("consolidation_service._run_consolidation_cycle"), "tidying my memory");
    assert.equal(spenderLabel("unknown"), "unlabelled");
    assert.equal(spenderLabel("some_new_service.do_it"), "some_new_service.do_it");
  });

  // L5 (2026-09-27 follow-ups): the Fuel card showed raw purposes — the
  // mind's wakes (mind:<reason>), the pulse, the graph worker, the greeting —
  // and batch B's labels for unlabelled turns (turn:<source>). Every one the
  // spend ledger writes now reads as plain words in Edward's voice, and none
  // says an internal name (pulse, heartbeat, surplus, consolidation).
  it("names the mind, the pulse, turns, the graph worker and the greeting in plain words", () => {
    const cases: [string, string][] = [
      ["mind:messages", "my wakes"],
      ["mind:reflect", "my wakes"],
      ["pulse", "my screens"],
      ["expression_service.run_expression_pulse", "my screens"],
      ["turn:chat", "our chats"],
      ["turn:voice", "talking with you"],
      // The household hub's voice (2026-10-11): not necessarily Ellis.
      ["turn:hub_voice", "talking at the hub"],
      ["turn:heartbeat", "reading your messages"],
      ["turn:scheduled_event", "my scheduled jobs"],
      ["turn:worker", "my helpers"],
      ["turn:mind", "my wakes"],
      ["turn:pulse", "my screens"],
      ["turn:some_new_source", "some new source"],
      ["graphiti_adapters._generate_response", "my memory graph"],
      ["routers.chat._astream_fresh_greeting", "my greetings"],
      ["routers.chat._stream", "our chats"],
      ["heartbeat.heartbeat_service._synthesize_briefing_narrative", "reading your messages"],
      ["entity_service.extract_entities", "noting names"],
      ["attachment_vision.describe_image", "looking at photos"],
      ["dream.context._generate", "recalling"],
      ["surplus:free_time", "my free time"],
      ["surplus:reflect_lite", "nightly reflection"],
      ["surplus:some_new_job", "spare time · some new job"],
    ];
    for (const [purpose, label] of cases) assert.equal(spenderLabel(purpose), label, purpose);
    for (const [purpose] of cases) {
      assert.doesNotMatch(spenderLabel(purpose), /pulse|heartbeat|surplus|consolidation|graphiti|routers|turn:|mind:|_/, purpose);
    }
  });

  // Every turn_source the backend's thread and ledger code labels a Codex
  // call with (batch B: codex_calls.purpose = "turn:<source>" for a turn
  // nothing else labelled), plus the mind's wakes and the pulse, in words
  // Edward would say about his own week (2026-09-27 integration).
  it("says every turn:<source> the backend writes, the mind and the pulse in Edward's words", () => {
    const cases: [string, string][] = [
      ["turn:chat", "our chats"],
      ["turn:voice", "talking with you"],
      ["turn:scheduled_event", "my scheduled jobs"],
      ["turn:heartbeat", "reading your messages"],
      ["turn:worker", "my helpers"],
      ["turn:widget", "your widget"],
      ["turn:alexa", "talking through Alexa"],
      ["turn:twilio", "texts to my number"],
      ["turn:briefing", "my briefings"],
      ["turn:background", "my background work"],
      ["mind:morning", "my wakes"],
      ["mind:free", "my wakes"],
      ["pulse", "my screens"],
    ];
    for (const [purpose, label] of cases) assert.equal(spenderLabel(purpose), label, purpose);
  });

  it("purposes that read the same are one row, their shares added", () => {
    const f = describeFuel(payload({
      spend_top: [
        { purpose: "mind:messages", pct: 10, basis: "meter" },
        { purpose: "consolidation_service._consolidation_cycle", pct: 8, basis: "meter" },
        { purpose: "mind:reflect", pct: 5, basis: "meter" },
      ],
    }), now);
    assert.deepEqual(f.spenders, [
      { label: "my wakes", pct: 15, text: "15%" },
      { label: "tidying my memory", pct: 8, text: "8%" },
    ]);
  });
});

// 2026-09-26 (Jarvis polish): the card and the Usage panel tell one "fuel"
// story in Edward's words — a meter with a pace marker, where the week is
// heading and how much of it would go unspent, chat's reserve, what his
// own spare-time work is doing and did last, and who spent the week.
import { describeFuel, shortDuration } from "../lib/codexUsage.ts";

describe("shortDuration", () => {
  it("drops the noise", () => {
    assert.equal(shortDuration(5 * 3600), "5h");
    assert.equal(shortDuration(2 * 3600 + 20 * 60), "2h 20m");
    assert.equal(shortDuration(2 * 86400 + 5 * 3600 + 40 * 60), "2d 5h");
    assert.equal(shortDuration(3 * 86400), "3d");
    assert.equal(shortDuration(40 * 60), "40m");
    assert.equal(shortDuration(-5), "1m");
  });
});

describe("describeFuel", () => {
  const inFive = { used_percent: 30, window_minutes: 10080, reset_after_seconds: 5 * 3600,
    reset_at: new Date(now + 5 * 3600e3).toISOString(), elapsed_fraction: 0.97 };

  it("says where the week closes and how much would go unspent", () => {
    const f = describeFuel(payload({ meter: { windows: { primary: inFive } }, surplus: surplus({ projected_end: 41, planned_end: 85, reserve: 3.2 }) }), now);
    assert.equal(f.headline, "Week closes in 5h at 41% — 44% unspent");
    assert.equal(f.usedPct, 30);
    assert.equal(f.pacePct, 97);
    assert.equal(f.reserve, "Chat keeps 3% in reserve");
    assert.equal(f.tone, "ok");
  });

  it("calls a week that lands on its plan on plan", () => {
    const f = describeFuel(payload({ surplus: surplus({ projected_end: 83.1, planned_end: 85 }) }), now);
    assert.equal(f.headline, "Week closes in 5d 1h at 83% — right on plan");
  });

  it("falls back to what is used when there is no projection", () => {
    const f = describeFuel(payload(), now);
    assert.equal(f.headline, "22% of the week used — closes in 5d 1h");
    assert.equal(f.pacePct, 28);
    assert.equal(f.surplus, null);
  });

  it("is honest when the week is spent", () => {
    const f = describeFuel(payload({ meter: { windows: { primary: { ...inFive, used_percent: 100 } }, limit_reached: true } }), now);
    assert.equal(f.tone, "bad");
    assert.equal(f.headline, "The week's fuel is spent — it refills in 5h");
  });

  it("has nothing to say without a meter", () => {
    const f = describeFuel(payload({ meter: { windows: {} } }), now);
    assert.equal(f.headline, "No fuel reading yet");
    assert.equal(f.usedPct, null);
    assert.equal(f.tone, "neutral");
  });

  it("says what his own work is doing, in his words", () => {
    assert.equal(describeFuel(payload({ surplus: surplus() }), now).surplus, "I'm spending spare fuel on my own work (0.40%/h).");
    assert.equal(describeFuel(payload({ surplus: surplus({ state: "on_pace" }) }), now).surplus, "Right on pace — nothing spare to spend.");
    assert.equal(describeFuel(payload({ surplus: surplus({ state: "running" }) }), now).surplus, "One of my own jobs is running now.");
    assert.equal(describeFuel(payload({ surplus: surplus({ state: "off" }) }), now).surplus, "My spare-time work is switched off.");
    const waiting = describeFuel(payload({ surplus: surplus({ state: "waiting", reason: "no headroom" }) }), now);
    assert.equal(waiting.surplus, "Holding my own work back for now.");
    assert.equal(waiting.surplusWhy, "no headroom");
  });

  it("names the last job and what it produced", () => {
    const f = describeFuel(payload({ surplus: surplus({ last_job_outcome: "saved 1 document" }) }), now);
    assert.equal(f.lastJob, "Last: research · 20m ago — saved 1 document");
    const failed = describeFuel(payload({ surplus: surplus({ last_job: "dossiers", last_job_outcome: "failed" }) }), now);
    assert.equal(failed.lastJob, "Last: dossiers · 20m ago — failed");
    const done = describeFuel(payload({ surplus: surplus({ last_job_outcome: "done" }) }), now);
    assert.equal(done.lastJob, "Last: research · 20m ago");
  });

  it("warns when background work is paused, and only then", () => {
    assert.equal(describeFuel(payload(), now).background, null);
    const paused = describeFuel(payload({ background_allowed: false, background_reason: "ahead of pace" }), now);
    assert.equal(paused.background, "Background work is paused — ahead of pace");
    assert.equal(paused.tone, "warn");
  });

  it("lists up to five spenders with numeric shares", () => {
    const f = describeFuel(payload({
      spend_top: [
        { purpose: "graph.streaming.x", pct: 31.24, basis: "meter" },
        { purpose: "surplus:research", pct: 6.5, basis: "meter" },
        { purpose: "memory_service.extract", pct: 4, basis: "tokens" },
      ],
    }), now);
    assert.deepEqual(f.spenders, [
      { label: "our chats", pct: 31.24, text: "31%" },
      { label: "my research", pct: 6.5, text: "6.5%" },
      { label: "remembering", pct: 4, text: "4% of tokens" },
    ]);
  });
});

// 2026-10-10: on the Claude API (EDWARD_LLM_PROVIDER=anthropic) Edward pays
// per token from prepaid credits, so the card tells the day's spend against
// its cap and how long the credits last — not a weekly meter. The backend
// sends it as the payload's `claude` block (services/llm_spend.py).
function claudePayload(claude: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) {
  return {
    status: "available",
    provider: "anthropic",
    plan_type: null,
    meter: { windows: {}, limit_reached: false },
    background_allowed: true,
    background_ceiling_percent: 60,
    surplus: { state: "off", reason: "Claude is paid per token" },
    spend_top: [],
    claude: {
      provider: "anthropic", active: true, day: "2026-10-12", timezone: "America/Chicago",
      spent_today_usd: 3.4, cap_usd: 10, background_share: 0.6, background_stop_usd: 6,
      interactive_reserve_usd: 3, hard_stop_usd: 13, remaining_today_usd: 6.6, percent_of_cap: 34,
      state: "ok", background_allowed: true, background_reason: "$3.40 of $6.00 for background work today",
      by_purpose: {
        "turn:chat": { calls: 4, cost_usd: 1.9 },
        "mind:morning": { calls: 2, cost_usd: 1.0 },
        "mind:messages": { calls: 1, cost_usd: 0.25 },
        "memory_service.extract": { calls: 10, cost_usd: 0.25 },
      },
      credits: { start_usd: 100, remaining_usd: 87.6, days_left: 12.4, burn_per_day_usd: 7.06,
        note: "tracked locally; check console.anthropic.com for the authoritative balance" },
      ...claude,
    },
    ...extra,
  };
}

describe("describeFuel on the Claude API", () => {
  it("says what today cost against the cap and how long the credits last", () => {
    const f = describeFuel(claudePayload(), now);
    assert.equal(f.headline, "Spent today $3.40 of $10 · credits left ~$88 (~12 days)");
    assert.equal(f.usedPct, 34);
    assert.equal(f.pacePct, 60);
    assert.equal(f.paceLabel, "background stops at");
    assert.equal(f.resetsIn, null);
    assert.equal(f.tone, "ok");
    assert.equal(f.surplus, null);
    assert.equal(f.background, null);
    assert.equal(f.reserve, "Resets at midnight · past the cap I keep $3 for you");
    assert.equal(f.spendersTitle, "Who spent today");
    assert.deepEqual(f.spenders.map((s) => [s.label, s.text]), [
      ["our chats", "$1.90"], ["my wakes", "$1.25"], ["remembering", "$0.25"],
    ]);
    assert.match(f.howItWorks ?? "", /console\.anthropic\.com/);
    assert.match(f.howItWorks ?? "", /\$6/);
    // Review 2026-10-11: what Ellis tells Edward is still learned past the
    // background line (his own turn's memory work runs on his limit).
    assert.match(f.howItWorks ?? "", /what you tell me is still remembered/);
  });

  it("leaves the days out until there is a burn to measure", () => {
    const f = describeFuel(claudePayload({ spent_today_usd: 0, percent_of_cap: 0,
      credits: { remaining_usd: 100, days_left: null } }), now);
    assert.equal(f.headline, "Spent today $0.00 of $10 · credits left ~$100");
  });

  it("writes cents where they matter and a short runway plainly", () => {
    const f = describeFuel(claudePayload({ cap_usd: 12.5, credits: { remaining_usd: 7.4, days_left: 0.6 } }), now);
    assert.equal(f.headline, "Spent today $3.40 of $12.50 · credits left ~$7.40 (under a day)");
    const one = describeFuel(claudePayload({ credits: { remaining_usd: 9, days_left: 1.2 } }), now);
    assert.equal(one.headline, "Spent today $3.40 of $10 · credits left ~$9.00 (~1 day)");
  });

  it("warns near the cap and once background work has stopped", () => {
    const near = describeFuel(claudePayload({ spent_today_usd: 8.2, percent_of_cap: 82, state: "background_paused",
      background_allowed: false, background_reason: "my background work stops at $6.00 a day" }), now);
    assert.equal(near.tone, "warn");
    assert.equal(near.usedPct, 82);
    assert.equal(near.background, "Background work is paused — my background work stops at $6.00 a day");
  });

  it("is honest when the day is spent or the credits are gone", () => {
    const stopped = describeFuel(claudePayload({ spent_today_usd: 13.1, percent_of_cap: 131, state: "stopped" }), now);
    assert.equal(stopped.tone, "bad");
    assert.equal(stopped.usedPct, 100);
    const broke = describeFuel(claudePayload({ credits: { remaining_usd: -1.5, days_left: 0 } }), now);
    assert.equal(broke.headline, "Spent today $3.40 of $10 · credits used up — check console.anthropic.com");
    assert.equal(broke.tone, "bad");
  });

  it("says so when it cannot read the spend", () => {
    const f = describeFuel(claudePayload({ error: "OperationalError: database is down", spent_today_usd: undefined }), now);
    assert.equal(f.headline, "I can't read today's spend right now");
    assert.equal(f.tone, "neutral");
    assert.equal(f.usedPct, null);
  });

  it("keeps the Codex story while the provider is Codex", () => {
    const f = describeFuel({ ...payload(), provider: "codex", claude: claudePayload().claude }, now);
    assert.equal(f.headline, "22% of the week used — closes in 5d 1h");
    assert.equal(f.paceLabel ?? null, null);
  });
});
