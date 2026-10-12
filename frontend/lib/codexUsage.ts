// The Codex usage card's view of /api/settings/codex-usage.
//
// Every Codex response reports the account's meter (used-percent, window
// length, reset) and the backend's rate gate paces Edward's background work
// against it. This module turns that payload into the lines the card shows;
// it is pure so the wording is pinned in __tests__/codexUsage.test.ts.

export interface CodexUsageWindow {
  used_percent: number;
  window_minutes: number;
  reset_after_seconds: number;
  reset_at: string;
  elapsed_fraction?: number;
}

export interface CodexUsagePayload {
  status: string;
  plan_type?: string | null;
  meter?: {
    windows: Record<string, CodexUsageWindow>;
    age_seconds?: number | null;
    limit_reached?: boolean;
  };
  background_allowed?: boolean;
  background_reason?: string;
  background_ceiling_percent?: number;
  cooldown_remaining?: number;
  calls_last_hour?: number;
  // The surplus queue (2026-09-24): spare quota over chat's reserve, spent
  // on work with an output, and the week's top spenders.
  surplus?: SurplusBlock | null;
  spend_top?: SpendTopEntry[];
  // Pay per token (2026-10-10): which provider Edward runs on, and the
  // day's Claude spend against its cap (backend services/llm_spend.py).
  provider?: string;
  claude?: ClaudeSpendBlock | null;
}

export interface ClaudeSpendBlock {
  provider?: string;
  active?: boolean;
  day?: string;
  timezone?: string;
  spent_today_usd?: number;
  cap_usd?: number;
  background_share?: number;
  background_stop_usd?: number;
  interactive_reserve_usd?: number;
  hard_stop_usd?: number;
  remaining_today_usd?: number;
  percent_of_cap?: number | null;
  state?: string; // ok | background_paused | own_work_paused | stopped
  background_allowed?: boolean;
  background_reason?: string;
  by_purpose?: Record<string, { calls?: number; cost_usd?: number }>;
  credits?: {
    start_usd?: number;
    remaining_usd?: number | null;
    days_left?: number | null;
    burn_per_day_usd?: number | null;
    /** "tracked" (EDWARD_CREDITS_START_USD and _DATE set) or "untracked". */
    source?: string;
    note?: string;
  } | null;
  error?: string;
}

export interface SurplusBlock {
  state: string; // off | running | waiting | on_pace | spending
  reason?: string | null;
  used?: number | null;
  reserve?: number | null;
  headroom?: number | null;
  target_rate?: number | null;
  r_int?: number | null;
  hours_to_reset?: number | null;
  projected_end?: number | null;
  planned_end?: number | null;
  last_job?: string | null;
  last_job_at?: string | null;
  last_job_outcome?: string | null;
}

export interface SpendTopEntry {
  purpose: string;
  pct: number;
  basis?: string; // meter | tokens
}

export type UsageTone = "ok" | "warn" | "bad" | "neutral";

export interface WindowLine {
  key: string;
  label: string;
  usedPercent: number;
  resets: string;
}

export interface CodexUsageDescription {
  tone: UsageTone;
  headline: string;
  windows: WindowLine[];
  background: string;
  plan: string | null;
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s >= 86400) return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.max(1, Math.floor(s / 60))}m`;
}

export function windowLabel(minutes: number): string {
  if (minutes >= 7 * 24 * 60) return "Weekly window";
  if (minutes >= 24 * 60) return `${Math.round(minutes / (24 * 60))}d window`;
  return `${Math.round(minutes / 60)}h window`;
}

function windowName(minutes: number): string {
  return minutes >= 7 * 24 * 60 ? "weekly" : `${Math.round(minutes / 60)}h`;
}

export function describeCodexUsage(payload: CodexUsagePayload, now: number = Date.now()): CodexUsageDescription {
  const entries = Object.entries(payload.meter?.windows ?? {});
  const windows: WindowLine[] = entries.map(([key, w]) => {
    const resetMs = Date.parse(w.reset_at);
    const remaining = Number.isFinite(resetMs) ? (resetMs - now) / 1000 : w.reset_after_seconds;
    return {
      key,
      label: windowLabel(w.window_minutes),
      usedPercent: Math.round(w.used_percent),
      resets: `resets in ${formatDuration(remaining)}`,
    };
  });
  const plan = payload.plan_type ?? null;
  const reason = payload.background_reason ?? "";
  const allowed = payload.background_allowed !== false;
  const background = `Background work: ${allowed ? "on pace" : "paused"}${reason ? ` — ${reason}` : ""}`;

  if (windows.length === 0) {
    return { tone: "neutral", headline: "No usage meter yet", windows, background, plan };
  }
  const primary = entries.find(([k]) => k === "primary")?.[1] ?? entries[0][1];
  const primaryLine = windows.find((w) => w.key === "primary") ?? windows[0];
  const capped = Boolean(payload.meter?.limit_reached) || primary.used_percent >= 100;
  if (capped) {
    return {
      tone: "bad",
      headline: `Usage cap reached · ${primaryLine.resets}`,
      windows,
      background,
      plan,
    };
  }
  return {
    tone: allowed ? "ok" : "warn",
    headline: `${primaryLine.usedPercent}% of the ${windowName(primary.window_minutes)} window used`,
    windows,
    background,
    plan,
  };
}

// ---- the surplus queue -------------------------------------------------------

export interface SurplusDescription {
  projected: string | null;
  state: string;
  tone: UsageTone;
  lastJob: string | null;
  spenders: { label: string; pct: string }[];
}

// Spend-ledger purposes are module paths (the caller that built the model),
// a surplus job's label, a mind wake's reason (mind:<reason>), the pulse, or
// — for a turn nothing else labelled — turn:<source>. The card reads "Who
// spent the week" in Edward's voice, so every one the ledger writes is plain
// words, never an internal name (L5, 2026-09-27 follow-ups: the card showed
// "mind:messages", "graphiti_adapters._generate_response" and
// "routers.chat._astream_fresh_greeting" as they were).
const TURN_LABELS: Record<string, string> = {
  chat: "our chats",
  voice: "talking with you",
  // The household hub's voice (2026-10-11): whoever is at the panel, not
  // necessarily the owner.
  hub_voice: "talking at the hub",
  heartbeat: "reading your messages",
  scheduled_event: "my scheduled jobs",
  worker: "my helpers",
  mind: "my wakes",
  pulse: "my screens",
  briefing: "my briefings",
  twilio: "texts to my number",
  widget: "your widget",
  // First person like the rest (2026-09-27 integration): a bare "Alexa" read
  // as if she spent the week, and "background work" as nobody's.
  alexa: "talking through Alexa",
  background: "my background work",
};

const SURPLUS_LABELS: Record<string, string> = {
  free_time: "my free time",
  research: "my research",
  reflect_lite: "nightly reflection",
  consolidation_deep: "deep memory tidy",
  contradictions: "sorting contradictions",
  dossiers: "notes on people",
  summaries: "summing up chats",
  meeting_prep: "meeting prep",
};

const SPENDER_LABELS: [string, string][] = [
  ["mind:", "my wakes"],
  ["routers.chat._astream_fresh_greeting", "my greetings"],
  ["routers.chat.greeting", "my greetings"],
  ["routers.chat", "our chats"],
  ["graph.", "our chats"],
  ["graphiti_adapters", "my memory graph"],
  ["memory_service", "remembering"],
  ["entity_service", "noting names"],
  ["expression_service", "my screens"],
  ["consolidation_service", "tidying my memory"],
  ["reflection_service", "reflecting after chats"],
  ["deep_retrieval", "recalling"],
  ["dream.", "recalling"],
  ["search_tag_service", "tagging conversations"],
  ["heartbeat.", "reading your messages"],
  ["attachment_vision", "looking at photos"],
  ["codex_cli_service", "coding jobs"],
];

const words = (s: string) => s.replace(/_/g, " ").trim();

export function spenderLabel(purpose: string): string {
  const p = (purpose || "").trim();
  if (!p || p === "unknown") return "unlabelled";
  if (p === "pulse") return "my screens";
  if (p.startsWith("surplus:")) {
    const job = p.slice("surplus:".length);
    return SURPLUS_LABELS[job] ?? `spare time · ${words(job)}`;
  }
  if (p.startsWith("turn:")) {
    const source = p.slice("turn:".length);
    return TURN_LABELS[source] ?? words(source);
  }
  for (const [prefix, label] of SPENDER_LABELS) {
    if (p.startsWith(prefix)) return label;
  }
  return p.length > 40 ? `${p.slice(0, 39)}…` : p;
}

/** The ledger's spenders by label: purposes that read the same (every
 *  mind:<reason> is "my wakes") are one row with their shares added —
 *  two "my wakes" rows would say less and share a React key. */
function spendersByLabel(entries: SpendTopEntry[]): { label: string; pct: number; basis?: string }[] {
  const rows = new Map<string, { label: string; pct: number; basis?: string }>();
  for (const e of entries) {
    const label = spenderLabel(e.purpose);
    const row = rows.get(label);
    if (row) row.pct = Math.round((row.pct + e.pct) * 100) / 100;
    else rows.set(label, { label, pct: e.pct, basis: e.basis });
  }
  return Array.from(rows.values()).sort((a, b) => b.pct - a.pct);
}

function ago(iso: string | null | undefined, now: number): string | null {
  if (!iso) return null;
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return null;
  const s = Math.max(0, Math.floor((now - at) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function rate(v: number | null | undefined): string {
  return `${(v ?? 0).toFixed(2)}%/h`;
}

export function describeSurplus(payload: CodexUsagePayload, now: number = Date.now()): SurplusDescription | null {
  const s = payload.surplus;
  if (!s) return null;
  let projected: string | null = null;
  if (typeof s.projected_end === "number") projected = `Projected ${Math.round(s.projected_end)}% at reset`;
  else if (typeof s.planned_end === "number") projected = `Plan: ${Math.round(s.planned_end)}% at reset`;

  let state: string;
  let tone: UsageTone = "ok";
  switch (s.state) {
    case "spending":
      state = `Surplus: spending ${rate(s.target_rate)} · chat keeps ${Math.round(s.reserve ?? 0)}%`;
      break;
    case "on_pace":
      state = `Surplus: on pace (target ${rate(s.target_rate)})`;
      break;
    case "running":
      state = "Surplus: a job is running now";
      break;
    case "off":
      state = "Surplus: off (EDWARD_SURPLUS=off)";
      tone = "neutral";
      break;
    default:
      state = `Surplus: waiting${s.reason ? ` — ${s.reason}` : ""}`;
      tone = "neutral";
  }

  let lastJob: string | null = null;
  if (s.last_job) {
    const when = ago(s.last_job_at, now);
    lastJob = `Last job: ${s.last_job.replace(/_/g, " ")}${when ? ` · ${when}` : ""}`;
    if (s.last_job_outcome === "failed") lastJob += " (failed)";
  }

  const spenders = spendersByLabel(payload.spend_top ?? []).slice(0, 3).map((e) => ({
    label: e.label,
    pct: e.basis === "tokens" ? `${Math.round(e.pct)}% of tokens` : `${e.pct.toFixed(1)}%`,
  }));
  return { projected, state, tone, lastJob, spenders };
}

// ---- the fuel story (2026-09-26) ----------------------------------------------
//
// The Work room's card and the Usage panel say one thing in Edward's words:
// how much of the week is used against an even pace, where the week will
// close and how much of it would go unspent, chat's reserve, what his own
// spare-time work is doing and did last, and who spent the week.

export interface FuelSpender {
  label: string;
  pct: number;
  text: string;
}

export interface FuelDescription {
  tone: UsageTone;
  headline: string;
  /** Used percent of the main window, or null with no meter. */
  usedPct: number | null;
  /** Where an even spend would be by now (the window's elapsed fraction). */
  pacePct: number | null;
  /** What the marker on the bar means; the card says "even pace" without one. */
  paceLabel?: string;
  resetsIn: string | null;
  reserve: string | null;
  surplus: string | null;
  surplusWhy: string | null;
  lastJob: string | null;
  background: string | null;
  spenders: FuelSpender[];
  /** The spenders' heading; the card says "Who spent the week" without one. */
  spendersTitle?: string;
  /** "How this works", when it is not the Codex week's story. */
  howItWorks?: string;
}

/** "5h", "2h 20m", "2d 5h", "40m" — a countdown without the noise. */
export function shortDuration(seconds: number): string {
  const s = Math.max(60, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return h ? `${d}d ${h}h` : `${d}d`;
  if (h > 0) return h < 3 && m ? `${h}h ${m}m` : `${h}h`;
  return `${m}m`;
}

function pctText(v: number): string {
  return v >= 10 ? `${Math.round(v)}%` : `${Number(v.toFixed(1))}%`;
}

const QUIET_OUTCOMES = new Set(["done", "ok", "completed", ""]);

export function describeFuel(payload: CodexUsagePayload, now: number = Date.now()): FuelDescription {
  if (payload.provider === "anthropic" && payload.claude) return describeClaudeFuel(payload.claude);
  const entries = Object.entries(payload.meter?.windows ?? {});
  const s = payload.surplus ?? null;
  const allowed = payload.background_allowed !== false;
  const background = allowed
    ? null
    : `Background work is paused${payload.background_reason ? ` — ${payload.background_reason}` : ""}`;

  let surplus: string | null = null;
  let surplusWhy: string | null = null;
  let lastJob: string | null = null;
  if (s) {
    switch (s.state) {
      case "spending":
        surplus = `I'm spending spare fuel on my own work (${rate(s.target_rate)}).`;
        break;
      case "on_pace":
        surplus = "Right on pace — nothing spare to spend.";
        break;
      case "running":
        surplus = "One of my own jobs is running now.";
        break;
      case "off":
        surplus = "My spare-time work is switched off.";
        break;
      default:
        surplus = "Holding my own work back for now.";
        surplusWhy = s.reason ?? null;
    }
    if (s.last_job) {
      const when = ago(s.last_job_at, now);
      const outcome = (s.last_job_outcome ?? "").trim();
      lastJob = `Last: ${s.last_job.replace(/_/g, " ")}${when ? ` · ${when}` : ""}`;
      if (!QUIET_OUTCOMES.has(outcome)) lastJob += ` — ${outcome}`;
    }
  }
  const reserve = s && typeof s.reserve === "number" ? `Chat keeps ${Math.round(s.reserve)}% in reserve` : null;
  const spenders = spendersByLabel(payload.spend_top ?? []).slice(0, 5).map((e) => ({
    label: e.label,
    pct: e.pct,
    text: e.basis === "tokens" ? `${Math.round(e.pct)}% of tokens` : pctText(e.pct),
  }));

  const base = { reserve, surplus, surplusWhy, lastJob, background, spenders };
  if (entries.length === 0) {
    return { ...base, tone: "neutral", headline: "No fuel reading yet", usedPct: null, pacePct: null, resetsIn: null };
  }
  const primary = entries.find(([k]) => k === "primary")?.[1] ?? entries[0][1];
  const resetMs = Date.parse(primary.reset_at);
  const remaining = Number.isFinite(resetMs) ? (resetMs - now) / 1000 : primary.reset_after_seconds;
  const resetsIn = shortDuration(remaining);
  const usedPct = Math.round(primary.used_percent);
  const pacePct = typeof primary.elapsed_fraction === "number" ? Math.round(primary.elapsed_fraction * 100) : null;
  const capped = Boolean(payload.meter?.limit_reached) || primary.used_percent >= 100;
  if (capped) {
    return { ...base, tone: "bad", headline: `The week's fuel is spent — it refills in ${resetsIn}`, usedPct, pacePct, resetsIn };
  }
  let headline = `${usedPct}% of the week used — closes in ${resetsIn}`;
  if (s && typeof s.projected_end === "number") {
    const projected = Math.round(s.projected_end);
    const plan = typeof s.planned_end === "number" ? s.planned_end : 100;
    const unspent = Math.round(plan - s.projected_end);
    headline =
      unspent > 3
        ? `Week closes in ${resetsIn} at ${projected}% — ${unspent}% unspent`
        : `Week closes in ${resetsIn} at ${projected}% — right on plan`;
  }
  return { ...base, tone: allowed ? "ok" : "warn", headline, usedPct, pacePct, resetsIn };
}

// ---- the Claude API: the day's spend (2026-10-10) -----------------------------
//
// With EDWARD_LLM_PROVIDER=anthropic every token is paid from prepaid
// credits and nothing expires at a reset, so the card tells the day: what
// it cost against the daily cap, where background work stops, the credits
// left and how long they last at the recent burn. The credits are tracked
// by Edward, not read from Anthropic — the card says where the real balance is
// — and only when the backend's .env sets EDWARD_CREDITS_START_USD (and
// _DATE); otherwise the card says they are not tracked.

/** Are prepaid credits tracked? The backend says so (``source``); an older
 *  one without it is tracked when it sent a balance. */
function creditsTracked(c: ClaudeSpendBlock["credits"]): boolean {
  if (!c) return false;
  if (c.source) return c.source === "tracked";
  return typeof c.remaining_usd === "number";
}

/** "$10", "$12.50", "$3.40": a cap reads whole when it is. */
function capUsd(v: number): string {
  return Number.isInteger(v) ? `$${v}` : `$${v.toFixed(2)}`;
}

function usd(v: number): string {
  return `$${v.toFixed(2)}`;
}

function creditsText(c: ClaudeSpendBlock["credits"]): string | null {
  if (!c || typeof c.remaining_usd !== "number") return null;
  if (c.remaining_usd <= 0) return "credits used up — check console.anthropic.com";
  const left = c.remaining_usd >= 10 ? `~$${Math.round(c.remaining_usd)}` : `~${usd(c.remaining_usd)}`;
  const d = c.days_left;
  if (typeof d !== "number" || !Number.isFinite(d)) return `credits left ${left}`;
  if (d < 1) return `credits left ${left} (under a day)`;
  const n = Math.round(d);
  return `credits left ${left} (~${n} day${n === 1 ? "" : "s"})`;
}

export function describeClaudeFuel(c: ClaudeSpendBlock): FuelDescription {
  const cap = typeof c.cap_usd === "number" ? c.cap_usd : 0;
  const base = {
    pacePct: null, paceLabel: "background stops at", resetsIn: null, reserve: null, surplus: null,
    surplusWhy: null, lastJob: null, background: null, spenders: [] as FuelSpender[],
    spendersTitle: "Who spent today",
  };
  if (c.error || typeof c.spent_today_usd !== "number") {
    return { ...base, tone: "neutral", headline: "I can't read today's spend right now", usedPct: null };
  }
  const spent = c.spent_today_usd;
  const credits = creditsText(c.credits);
  const headline = `Spent today ${usd(spent)} of ${capUsd(cap)}${credits ? ` · ${credits}` : ""}`;
  const pct = typeof c.percent_of_cap === "number" ? c.percent_of_cap : cap > 0 ? (spent / cap) * 100 : 0;
  const usedPct = Math.max(0, Math.min(100, Math.round(pct)));
  const share = typeof c.background_share === "number" ? c.background_share : null;
  const pacePct = share != null ? Math.round(share * 100) : null;
  const reserveUsd = typeof c.interactive_reserve_usd === "number" ? c.interactive_reserve_usd : null;
  const reserve = `Resets at midnight${reserveUsd != null ? ` · past the cap I keep ${capUsd(reserveUsd)} for you` : ""}`;
  const background = c.background_allowed === false
    ? `Background work is paused${c.background_reason ? ` — ${c.background_reason}` : ""}`
    : null;
  const costs = Object.entries(c.by_purpose ?? {}).map(([purpose, g]) => ({ purpose, pct: g.cost_usd ?? 0 }));
  const spenders = spendersByLabel(costs).slice(0, 5).map((e) => ({ label: e.label, pct: e.pct, text: usd(e.pct) }));
  const brokeOrStopped = c.state === "stopped" || (typeof c.credits?.remaining_usd === "number" && c.credits.remaining_usd <= 0);
  const tone: UsageTone = brokeOrStopped ? "bad" : c.background_allowed === false || pct >= 80 ? "warn" : "ok";
  const stop = typeof c.background_stop_usd === "number" ? capUsd(c.background_stop_usd) : "its share";
  const howItWorks =
    `I run on the Claude API now, paid per token from prepaid credits. Each day I can spend up to ${capUsd(cap)}: ` +
    `my background work (reflection, photo descriptions, remembering my own work) stops at ${stop}, my own ` +
    `wakes, screens and the follow-ups I schedule myself at ${capUsd(cap)}, and your messages keep ` +
    `${reserveUsd != null ? capUsd(reserveUsd) : "a reserve"} past that — what you tell me is still remembered ` +
    "until your messages stop too. " +
    (creditsTracked(c.credits)
      ? "The credits are tracked here from what each answer cost; console.anthropic.com has the real balance. "
      : "Your prepaid credits aren't tracked here: set EDWARD_CREDITS_START_USD and EDWARD_CREDITS_START_DATE " +
        "in the backend's .env to follow them on this card. console.anthropic.com has the balance. ") +
    "The tick on the bar is where background work stops.";
  return { ...base, tone, headline, usedPct, pacePct, reserve, background, spenders, howItWorks };
}
