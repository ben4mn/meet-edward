// Pure helpers for the Today page. The backend (services/today_service.py)
// composes and ranks the feed; this file decides how the page lays it out,
// what each card says in plain words, and what the page does after the owner acts.
//
// UX pass 2026-09-26 (docs/design-system-2026-09-25.md): Today is a briefing
// that shows Edward has been working for the owner. Emerald means Edward;
// the owner's own primary buttons are neutral. No internal words ("triage",
// "escalated", tool names) reach a card. Pinned in __tests__/todayModel.test.ts.

import { getOwnerName, ownerNamePattern } from "@/lib/owner";
import { formatRelative } from "@/lib/time";
import { shortClock } from "./heroModel";

export type Tone = "warn" | "info" | "edward" | "ok" | "bad" | "neutral";

const TONES: ReadonlySet<string> = new Set(["warn", "info", "edward", "ok", "bad", "neutral"]);

export interface TodayAction {
  id: string;
  label: string;
  method: string; // GET means "navigate", anything else is a fetch
  url: string;
  body?: Record<string, unknown>;
  primary: boolean;
}

export type NeedKind = "dose" | "calendar" | "heartbeat" | "email" | "open_loop" | "event" | "worker" | "note" | "approval";

/** A contradiction the owner can answer in place: two memories, each resolving the flag. */
export interface TodayChoiceOption {
  id: string;
  label: string;
  text: string;
  /** The id of the need's action that picks this option (keep_a / keep_b). */
  action_id: string;
}

export interface TodayChoice {
  flag_id: string;
  question: string;
  options: TodayChoiceOption[];
}

export interface TodayNeed {
  id: string;
  kind: NeedKind | string;
  tone: Tone;
  priority: number;
  title: string;
  detail: string;
  href: string;
  at?: string;
  /** Rows the card stands for (a thread's messages, notes sharing a title). */
  count?: number;
  /** Note cards: every note the card's Done clears. */
  note_ids?: string[];
  choice?: TodayChoice;
  /** A held send's parts, when the backend sends them (`send`, review
   *  2026-09-26); `detail` keeps the one-line summary for older backends. */
  held?: { channel?: string; recipient?: string; text?: string; subject?: string };
  actions: TodayAction[];
}

export interface TodayDid {
  id: string;
  /** "action" (2026-09-25) is a wake of his mind that acted; `href` is its
   *  thought entry (`/chat?c=<id>`). "wakes" is every wake folded into one
   *  row. Others: note, triage, event_fired, wish, … */
  kind: string;
  title: string;
  detail: string;
  at: string;
  href: string;
  count?: number;
}

export interface TodayConversation {
  id: string;
  title: string;
  source: string;
  updated_at: string;
  href: string;
}

export interface TodayMindPresence {
  last_wake_at: string | null;
  last_reason: string | null;
  last_outcome: string | null;
  last_line: string | null;
  next_wake_at: string | null;
  next_wake_reason: string | null;
}

/** What Edward is listening to and when his mind last woke / wakes next. */
export interface TodayPresence {
  /** imessage / calendar / email / gmail; [] = stopped; null = unknown. */
  listening: string[] | null;
  last_look_at: string | null;
  mind: TodayMindPresence;
}

export interface TodayOverflow {
  note: number;
  heartbeat: number;
}

export interface TodayFeed {
  generated_at: string;
  needs: TodayNeed[];
  overflow: TodayOverflow;
  did: TodayDid[];
  did_since: string | null;
  conversations: TodayConversation[];
  thoughts_today: number;
  status: Record<string, unknown>;
  presence: TodayPresence | null;
  // Goals and ideas left Today with unit G (2026-10-11); an older feed's
  // `goals` / `ideas` are dropped by parseToday.
}

// ------------------------------------------------------------ normalise

type Obj = Record<string, unknown>;

function isObj(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function strOrNull(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}

function num(v: unknown, fallback = 0): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function arr<T>(v: unknown, each: (x: Obj) => T): T[] {
  return Array.isArray(v) ? v.filter(isObj).map(each) : [];
}

function action(a: Obj): TodayAction {
  return {
    id: str(a.id),
    label: str(a.label),
    method: str(a.method, "GET"),
    url: str(a.url),
    ...(isObj(a.body) ? { body: a.body } : {}),
    primary: a.primary === true,
  };
}

function need(n: Obj): TodayNeed {
  const tone = str(n.tone, "neutral");
  const out: TodayNeed = {
    id: str(n.id),
    kind: str(n.kind),
    tone: (TONES.has(tone) ? tone : "neutral") as Tone,
    priority: num(n.priority),
    title: str(n.title),
    detail: str(n.detail),
    href: str(n.href),
    actions: arr(n.actions, action),
  };
  if (typeof n.at === "string") out.at = n.at;
  if (typeof n.count === "number") out.count = n.count;
  if (Array.isArray(n.note_ids)) out.note_ids = n.note_ids.filter((x): x is string => typeof x === "string");
  const heldSrc = isObj(n.send) ? n.send : isObj(n.approval) ? n.approval : n;
  const held: NonNullable<TodayNeed["held"]> = {};
  for (const k of ["channel", "recipient", "text", "subject"] as const) {
    if (typeof heldSrc[k] === "string" && (heldSrc[k] as string).trim()) held[k] = heldSrc[k] as string;
  }
  if (held.recipient || held.text) out.held = held;
  if (isObj(n.choice) && Array.isArray(n.choice.options)) {
    out.choice = {
      flag_id: str(n.choice.flag_id),
      question: str(n.choice.question),
      options: arr(n.choice.options, (o) => ({
        id: str(o.id),
        label: str(o.label),
        text: str(o.text),
        action_id: str(o.action_id),
      })),
    };
  }
  return out;
}

function presence(p: unknown): TodayPresence | null {
  if (!isObj(p)) return null;
  const m = isObj(p.mind) ? p.mind : {};
  return {
    listening: Array.isArray(p.listening) ? p.listening.filter((x): x is string => typeof x === "string") : null,
    last_look_at: strOrNull(p.last_look_at),
    mind: {
      last_wake_at: strOrNull(m.last_wake_at),
      last_reason: strOrNull(m.last_reason),
      last_outcome: strOrNull(m.last_outcome),
      last_line: strOrNull(m.last_line),
      next_wake_at: strOrNull(m.next_wake_at),
      next_wake_reason: strOrNull(m.next_wake_reason),
    },
  };
}

/** Whatever /api/today (or a cached copy, or an older backend) sent, as a
 *  feed the page can render without optional chaining everywhere. */
export function normalizeTodayFeed(raw: unknown): TodayFeed {
  const r = isObj(raw) ? raw : {};
  const overflow = isObj(r.overflow) ? r.overflow : {};
  return {
    generated_at: str(r.generated_at),
    needs: arr(r.needs, need),
    overflow: { note: num(overflow.note), heartbeat: num(overflow.heartbeat) },
    did: arr(r.did, (d) => ({
      id: str(d.id),
      kind: str(d.kind),
      title: str(d.title),
      detail: str(d.detail),
      at: str(d.at),
      href: str(d.href),
      ...(typeof d.count === "number" ? { count: d.count } : {}),
    })),
    did_since: strOrNull(r.did_since),
    conversations: arr(r.conversations, (c) => ({
      id: str(c.id),
      title: str(c.title, "Untitled"),
      source: str(c.source, "user"),
      updated_at: str(c.updated_at),
      href: str(c.href) || `/chat?c=${encodeURIComponent(str(c.id))}`,
    })),
    thoughts_today: num(r.thoughts_today),
    status: isObj(r.status) ? r.status : {},
    presence: presence(r.presence),
  };
}

// ------------------------------------------------------------ actions

export function partitionActions(actions: TodayAction[]) {
  const links = actions.filter((a) => a.method === "GET");
  const mutating = actions.filter((a) => a.method !== "GET");
  const primary = mutating.find((a) => a.primary) ?? null;
  const secondary = mutating.filter((a) => a !== primary);
  return { primary, secondary, links };
}

/** After a mutating action succeeds the need is gone from the list; the
 *  next poll will say if the backend disagrees. */
export function afterAction(needs: TodayNeed[], needId: string): TodayNeed[] {
  return needs.filter((n) => n.id !== needId);
}

/** Where a need's title goes. Never undefined: Next's <Link> throws on an
 *  undefined href in a production build, and on 2026-09-22 a note need
 *  without one turned the PWA's start page into "Application error". */
export function needHref(need: Pick<TodayNeed, "href" | "actions">): string {
  if (typeof need.href === "string" && need.href) return need.href;
  const link = (need.actions ?? []).find((a) => a.method === "GET" && typeof a.url === "string" && a.url);
  return link ? link.url : "/today";
}

/** The buttons a card shows. The whole card already opens `needHref`, so a
 *  link to the same place is not repeated; a choice's own actions are
 *  drawn as the choice, not as buttons. */
export function cardActions(need: TodayNeed) {
  const choiceIds = new Set((need.choice?.options ?? []).map((o) => o.action_id));
  const href = needHref(need);
  const { primary, secondary, links } = partitionActions((need.actions ?? []).filter((a) => !choiceIds.has(a.id)));
  return { primary, secondary, links: links.filter((l) => l.url !== href) };
}

/** A button's words. The backend's labels, except where plain words say it
 *  better: an approval sends or does not; Done beside a question skips it. */
export function actionLabel(need: TodayNeed, a: TodayAction): string {
  if (need.kind === "approval") {
    if (a.id === "approve") return "Send";
    if (a.id === "deny") return "Don't send";
  }
  if (need.choice && a.id === "done") return "Skip";
  return a.label;
}

/** The action `x` runs: a Done that clears the card. Never a send, a deny
 *  or a dose (those are decisions, not dismissals). */
export function dismissAction(need: TodayNeed): TodayAction | null {
  return (need.actions ?? []).find((a) => a.method !== "GET" && (a.id === "done" || a.id === "dismiss")) ?? null;
}

/** "Both fine": the contradiction is not one. Resolves the flag without
 *  retiring either memory (the Dreaming panel's own PATCH). */
export function bothFineAction(choice: TodayChoice): TodayAction {
  return {
    id: "both",
    label: "Both fine",
    method: "PATCH",
    url: `/api/consolidation/flags/${encodeURIComponent(choice.flag_id)}`,
    body: { resolved: true },
    primary: false,
  };
}

const CARE_LOG = /^\/api\/hub\/care\/[^/]+\/log$/;

/** The request that takes an action back, where one exists. Only a logged
 *  dose has one (DELETE the same log); nothing else pretends to. */
export function undoFor(need: TodayNeed, a: TodayAction): TodayAction | null {
  if (need.kind === "dose" && a.method === "POST" && CARE_LOG.test(a.url)) {
    return { id: "undo", label: "Undo", method: "DELETE", url: a.url, primary: false };
  }
  return null;
}

// ------------------------------------------------------------ cards

/** "Remind <owner> to …" / "Remind me about …", the way a scheduled
 *  reminder's title names the owner: group 1 is to|about, group 2 the rest.
 *  Built at call time: the name arrives with /api/auth/status (lib/owner.ts). */
function remindOwner(): RegExp {
  return new RegExp(`^Remind ${ownerNamePattern(getOwnerName())} (to|about) (.+)$`, "i");
}

/** A card's title in plain words: a held send asks the question itself;
 *  an email names who sent it, not their address. */
export function needTitle(need: TodayNeed): string {
  if (need.kind === "approval") {
    const preview = approvalFor(need);
    if (preview.kind === "codex") return "Let Edward start Codex?";
    if (preview.recipient) return `Send this to ${preview.recipient}?`;
  }
  if (need.kind === "email") {
    const m = EMAIL_LEAD.exec(need.title ?? "");
    if (m) return `${senderName(m[1])}${m[2]}`;
  }
  if (need.kind === "event") {
    // "Remind Sam to call the vet" (or "Remind me to …") is Edward's note to
    // himself; to the owner it is a reminder.
    const r = remindOwner().exec(need.title ?? "");
    if (r) return `Reminder: ${r[2]}`;
  }
  return need.title;
}

/** The line under a card's title. An email whose title led with a bare
 *  address carries the address here instead. */
export function needDetail(need: TodayNeed): string {
  if (need.kind === "email") {
    const m = EMAIL_LEAD.exec(need.title ?? "");
    if (m) return need.detail ? `${m[1]} · ${need.detail}` : m[1];
  }
  return need.detail ?? "";
}

const EMAIL_LEAD = /^([^\s@<>"]+@[^\s@<>"]+\.[^\s@<>"]+)(\s+emailed you\b.*)$/i;

/** Mailbox providers: the person is the address's local part. */
const MAILBOX_HOSTS: ReadonlySet<string> = new Set(
  "gmail googlemail yahoo ymail icloud me mac outlook hotmail live msn aol proton protonmail pm fastmail hey comcast att".split(" "),
);
/** Domain labels that name a machine or a department, not who sent it. */
const GENERIC_LABELS: ReadonlySet<string> = new Set(
  ("mail email e em smtp mx www info news newsletter notify notifications notification alerts alert accounts " +
    "account noreply no-reply reply send bounce bounces mailer example co m app").split(" "),
);

function titleWords(s: string): string {
  return s
    .split(/[-_.+]+/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

/** "frontdesk@patel-vet.example.com" → "Patel Vet"; "jane.doe@gmail.com" →
 *  "Jane Doe"; "no-reply@accounts.google.com" → "Google". */
export function senderName(address: string): string {
  const [local = "", domain = ""] = (address ?? "").split("@");
  const labels = domain.toLowerCase().split(".").filter(Boolean);
  const named = labels.slice(0, -1); // not the TLD
  if (named.some((l) => MAILBOX_HOSTS.has(l))) return titleWords(local.replace(/\d+$/, "")) || address;
  const pick = named.find((l) => !GENERIC_LABELS.has(l));
  return (pick ? titleWords(pick) : titleWords(local)) || address;
}

/** The quiet time on a card's right: when an upcoming thing is, or how
 *  long ago something arrived. A dose says its lateness in its detail. */
export function needMeta(need: TodayNeed, opts: { now?: Date; tz?: string } = {}): string | null {
  if (!need.at || need.kind === "dose" || need.kind === "calendar") return null;
  if (need.kind === "event") return shortClock(need.at, opts) || null;
  return formatRelative(need.at, { now: opts.now });
}

export type NeedIcon =
  | "paw"
  | "shield"
  | "calendar"
  | "message"
  | "loop"
  | "clock"
  | "worker"
  | "note"
  | "question"
  | "mail"
  | "activity";

const KIND_ICON: Record<string, NeedIcon> = {
  approval: "shield",
  dose: "paw",
  calendar: "calendar",
  heartbeat: "message",
  email: "mail",
  open_loop: "loop",
  event: "clock",
  worker: "worker",
  note: "note",
};

export function needVisual(need: TodayNeed): { icon: NeedIcon; tone: Tone } {
  const tone = (TONES.has(need.tone) ? need.tone : "neutral") as Tone;
  const isQuestion = need.kind === "note" && (need.choice != null || /[?&]flag=/.test(need.href ?? ""));
  return { icon: isQuestion ? "question" : KIND_ICON[need.kind] ?? "activity", tone };
}

/** Past this many cards the rest wait behind "+N more". */
export const NEEDS_CAP = 5;

/** The cards to draw: needs being resolved are left out (so a refresh
 *  cannot bring one back mid-flight), then the cap, unless expanded. One
 *  past the cap just shows — "+1 more" costs as much as the card. */
export function visibleNeeds(
  needs: TodayNeed[],
  resolving: ReadonlySet<string>,
  expanded: boolean,
  cap = NEEDS_CAP,
): { shown: TodayNeed[]; more: number } {
  const live = needs.filter((n) => !resolving.has(n.id));
  if (expanded || live.length <= cap + 1) return { shown: live, more: 0 };
  return { shown: live.slice(0, cap), more: live.length - cap };
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** What the backend held back past its own caps: "3 more notes and 2 more threads". */
export function overflowLabel(overflow: Partial<TodayOverflow> | null | undefined): string | null {
  const parts: string[] = [];
  if (overflow?.note) parts.push(plural(overflow.note, "more note", "more notes"));
  if (overflow?.heartbeat) parts.push(plural(overflow.heartbeat, "more thread", "more threads"));
  return parts.length ? parts.join(" and ") : null;
}

/** A collapsed note card says how many notes it stands for. (A thread's
 *  count is already in its detail: "3 messages · …".) */
export function needCountLabel(need: TodayNeed): string | null {
  if (need.kind === "note" && (need.count ?? 1) > 1) return `${need.count} notes`;
  return null;
}

export interface ApprovalPreview {
  channel: string | null;
  recipient: string | null;
  text: string | null;
  /** The backend cut the quote at 120 characters. */
  truncated: boolean;
  /** A held email's subject. */
  subject?: string;
  /** "Codex in a scratch directory", not "… to …" (a Codex session). */
  connector?: "in";
  /** A Codex session Edward asked to start, not a message (review 2026-09-27). */
  kind?: "codex";
}

/** A held Codex session's parts, from the backend's `send` or a pending
 *  approval's stored args: where it runs and the whole task. Anything
 *  else: null. */
export function codexHold(channelOrTool: string | null | undefined, task: unknown, where?: unknown): ApprovalPreview | null {
  const c = (channelOrTool ?? "").trim().toLowerCase();
  if (c !== "codex" && c !== "spawn_cc_worker") return null;
  if (typeof task !== "string" || !task.trim()) return null;
  const place = typeof where === "string" && where.trim() ? where.trim() : "a scratch directory";
  return { channel: "Codex", recipient: place, text: task, truncated: false, connector: "in", kind: "codex" };
}

/** The backend's `send.channel` values that are a message to someone. */
const MESSAGE_CHANNELS: ReadonlySet<string> = new Set(["imessage", "sms", "text", "whatsapp", "email", "mail", "message"]);

function channelFor(tool: string): string {
  const t = tool.trim().toLowerCase();
  if (t.includes("imessage")) return "iMessage";
  if (t.includes("whatsapp")) return "WhatsApp";
  if (t === "send_sms" || t === "sms" || t === "text") return "Text";
  if (t === "mail" || t.includes("email")) return "Email";
  return "Message";
}

const HELD_SEND = /^\s*([\w .-]+?) to (.+?):\s*["“]([\s\S]*)["”]\s*$/;

/** A held send's summary (`send_imessage to Parker: "On my way"`) as who and
 *  what, so the card can quote it. Anything else: all null. */
export function approvalPreview(detail: string): ApprovalPreview {
  const m = HELD_SEND.exec(detail ?? "");
  if (!m) return { channel: null, recipient: null, text: null, truncated: false };
  const text = m[3];
  return {
    channel: channelFor(m[1]),
    recipient: m[2].trim(),
    text,
    truncated: /(…|\.\.\.)$/.test(text),
  };
}

/** A held send's who and what: the backend's fields when it sends them
 *  (the full text, never cut), else the summary line. */
export function approvalFor(need: Pick<TodayNeed, "detail" | "held">): ApprovalPreview {
  const h = need.held;
  const codex = codexHold(h?.channel, h?.text, h?.recipient);
  if (codex) return codex;
  const isMessage = !h?.channel || MESSAGE_CHANNELS.has(h.channel.trim().toLowerCase());
  if (h && isMessage && (h.recipient || h.text)) {
    const parsed = approvalPreview(need.detail);
    return {
      channel: h.channel ? channelFor(h.channel) : parsed.channel,
      recipient: h.recipient?.trim() || parsed.recipient,
      text: h.text ?? parsed.text,
      truncated: h.text ? false : parsed.truncated,
      ...(h.subject ? { subject: h.subject } : {}),
    };
  }
  return approvalPreview(need.detail);
}

export interface Receipt {
  title: string;
  body?: string;
  tone: "ok" | "neutral" | "edward" | "bad";
  /** Where "Open" goes, when the receipt needs the owner to look. */
  open?: string;
}

/** A send tool's own words for a delivery (services/graph/tools.py looks_delivered). */
const SENT_TEXT = /^(iMessage sent|SMS sent|WhatsApp sent|Sent via iMessage|Email sent|Mail sent|Message sent)\b/i;
const FAILURE_PREFIX = /^(Tool error:|Unknown tool:|BLOCKED:?|Error:)\s*/i;
const FAILURE_TEXT = /^(Tool error|Unknown tool|BLOCKED|Error|Failed|Could not|Couldn't|Cannot|Can't)\b/i;

function firstLine(text: string, max = 140): string {
  let line = (text.split(/\r?\n/)[0] ?? "").replace(FAILURE_PREFIX, "").trim();
  if (line.length > max) line = line.slice(0, max - 1).trimEnd() + "…";
  return line ? line[0].toUpperCase() + line.slice(1) : "";
}

/** What POST /api/approvals/{id} said about the send it ran: delivered,
 *  not delivered, or unknown (null). `delivered` when the backend sends it;
 *  otherwise the tool's own words decide, and only a send's "… sent" is a
 *  delivery. */
function approvedOutcome(result: unknown, isMessage: boolean): { delivered: boolean | null; text: string } {
  if (!isObj(result)) return { delivered: null, text: "" };
  const text = typeof result.result_text === "string" ? result.result_text.trim() : "";
  if (typeof result.delivered === "boolean") return { delivered: result.delivered, text };
  if (!text) return { delivered: null, text };
  if (SENT_TEXT.test(text)) return { delivered: true, text };
  if (isMessage || FAILURE_TEXT.test(text)) return { delivered: false, text };
  return { delivered: true, text };
}

/** A held Codex session answered: "Started Codex" only on the server's word. */
function codexReceipt(need: TodayNeed, a: TodayAction, result?: unknown): Receipt {
  if (a.id === "deny") return { title: "Not started", tone: "neutral" };
  const open = needHref(need);
  const { delivered, text } = approvedOutcome(result, false);
  if (delivered === true) return { title: "Started Codex", tone: "ok" };
  if (delivered === false) {
    const why = firstLine(text);
    return { title: "Codex didn't start", ...(why ? { body: why } : {}), tone: "bad", open };
  }
  return { title: "I couldn't confirm it started", body: "Open the thread to check.", tone: "neutral", open };
}

/** The toast after an action went through: what happened, in plain words.
 *  A held send is "Sent" only when the server says it was delivered
 *  (review 2026-09-26: the approve POST is a 200 even when the send failed). */
export function receiptFor(need: TodayNeed, a: TodayAction, result?: unknown): Receipt {
  if (need.kind === "approval") {
    const preview = approvalFor(need);
    const who = preview.recipient;
    if (preview.kind === "codex") return codexReceipt(need, a, result);
    if (a.id === "approve") {
      const open = needHref(need);
      const { delivered, text } = approvedOutcome(result, preview.channel !== null);
      if (delivered === true) return { title: who ? `Sent to ${who}` : "Sent", tone: "ok" };
      if (delivered === false) {
        const why = firstLine(text);
        return {
          title: who ? `Couldn't send to ${who}` : "That didn't go through",
          ...(why ? { body: why } : {}),
          tone: "bad",
          open,
        };
      }
      return { title: "I couldn't confirm it went", body: "Open the thread to check.", tone: "neutral", open };
    }
    if (a.id === "deny") return who ? { title: "Not sent", body: `${who} won't get it.`, tone: "neutral" } : { title: "Not sent", tone: "neutral" };
  }
  if (need.choice) {
    if (a.id === "both") return { title: "Got it", body: "Both stay.", tone: "edward" };
    const picked = need.choice.options.find((o) => o.action_id === a.id);
    if (picked) return { title: "Got it", body: `I'll remember: ${picked.text}`, tone: "edward" };
  }
  if (need.kind === "dose" && CARE_LOG.test(a.url)) return { title: "Logged", body: need.title, tone: "ok" };
  if (a.id === "snooze") return { title: "Snoozed until morning", body: need.title, tone: "neutral" };
  if (a.id === "done" || a.id === "dismiss") return { title: "Done", body: needTitle(need), tone: "neutral" };
  return { title: a.label || "Done", body: needTitle(need), tone: "neutral" };
}

// ------------------------------------------------------------ what I did

export interface DidRowModel {
  href: string | undefined;
  /** Tap toggles the detail (digest rows whose detail is the point). */
  expandable: boolean;
  /** "Edward did" on an action — his choice, not a count of what he saw. */
  badge: string | null;
  /** Show the detail under the title from the start. */
  inlineDetail: boolean;
}

/** How a "What I did" row behaves. An action (2026-09-25: "it must be
 *  obvious when he is choosing to take action") always opens its thought
 *  entry and shows what it did; other rows keep tap-to-expand. */
export function didRowModel(d: TodayDid): DidRowModel {
  const href = typeof d.href === "string" && d.href ? d.href : undefined;
  if (d.kind === "action") return { href, expandable: false, badge: "Edward did", inlineDetail: true };
  if (d.detail) return { href: undefined, expandable: true, badge: null, inlineDetail: false };
  return { href, expandable: false, badge: null, inlineDetail: false };
}

/** The opening verbs of a scheduled task, as done. */
const PAST_TENSE: Readonly<Record<string, string>> = {
  check: "Checked",
  look: "Looked",
  send: "Sent",
  text: "Texted",
  call: "Called",
  ask: "Asked",
  tell: "Told",
  email: "Emailed",
  message: "Messaged",
  ping: "Pinged",
  follow: "Followed",
  book: "Booked",
  review: "Reviewed",
  find: "Found",
  update: "Updated",
  research: "Researched",
  confirm: "Confirmed",
  order: "Ordered",
  draft: "Drafted",
  write: "Wrote",
  read: "Read",
  search: "Searched",
  summarize: "Summarized",
  summarise: "Summarised",
  prepare: "Prepared",
  prep: "Prepped",
  plan: "Planned",
  remember: "Remembered",
  note: "Noted",
  gather: "Gathered",
  compare: "Compared",
  track: "Tracked",
  watch: "Watched",
};

const TRIAGE_TITLE = /^Triaged (\d+) messages?, noted (\d+), escalated (\d+)(?:, you handled (\d+))?/;

/** A row's title without internal words: the message count ("Triaged 47
 *  messages, noted 6, escalated 4") reads as what it means to the owner. */
export function didTitle(d: TodayDid): string {
  const m = d.kind === "triage" ? TRIAGE_TITLE.exec(d.title) : null;
  if (!m) {
    // A reminder he fired was one he gave the owner; a reply in a chat named
    // after its one person says the name once.
    const reminded = d.kind === "event_fired" ? remindOwner().exec(d.title) : null;
    if (reminded) return `Reminded you ${reminded[1].toLowerCase()} ${reminded[2]}`;
    // A task he ran reads as done, not as a to-do (review 2026-09-26).
    if (d.kind === "event_fired") {
      const verb = /^([A-Za-z]+)(\s.*)$/.exec(d.title);
      const past = verb ? PAST_TENSE[verb[1].toLowerCase()] : undefined;
      if (verb && past) return `${past}${verb[2]}`;
    }
    const twice = /^(.* to )(.+?) in \2$/.exec(d.title);
    return twice ? `${twice[1]}${twice[2]}` : d.title;
  }
  const [, total, , escalated] = m.map((x) => (x == null ? 0 : Number(x))) as number[];
  return `Read ${plural(total, "message", "messages")} · ${escalated ? `${escalated} for you` : "nothing for you"}`;
}

/** An action's detail without the copy of its title the journal leads
 *  with ("Drafted a reply to Jordan: \"Sunday works\"" → the quote). */
export function didDetail(d: TodayDid): string {
  const detail = d.detail ?? "";
  const counts = d.kind === "triage" ? TRIAGE_TITLE.exec(d.title) : null;
  if (counts) {
    const parts: string[] = [];
    if (Number(counts[2])) parts.push(`noted ${counts[2]}`);
    if (counts[4] && Number(counts[4])) parts.push(`you handled ${counts[4]}`);
    const rest = detail.trim().replace(/[.]+$/, "");
    if (rest) parts.push(rest);
    const line = parts.join(" · ");
    return line ? line[0].toUpperCase() + line.slice(1) : "";
  }
  const cut = detail.indexOf(": ");
  if (cut < 4) return detail;
  const head = detail.slice(0, cut).toLowerCase();
  return d.title.toLowerCase().startsWith(head) ? detail.slice(cut + 2).trim() : detail;
}

const ERROR_TEXT = /\b\w*(Error|Exception)\b|Traceback|HTTP \d{3}|\bstatus \d{3}\b/;

/** The folded wakes row's line: his last closing words, never an error. */
export function wakesDetail(d: TodayDid): string | null {
  const text = (d.detail ?? "").trim();
  if (!text || ERROR_TEXT.test(text)) return null;
  return `Last: ${text}`;
}

/** The folded row of every wake (it summarises the whole window, so it
 *  heads the section instead of sitting in one part of the day). */
export function splitDid(did: TodayDid[]): { wakes: TodayDid | null; rows: TodayDid[] } {
  const wakes = did.find((d) => d.kind === "wakes") ?? null;
  return { wakes, rows: did.filter((d) => d !== wakes) };
}

interface LocalStamp {
  day: number; // days since epoch, local
  hour: number;
}

function localStamp(date: Date, tz: string | undefined): LocalStamp {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  return { day: Math.round(Date.UTC(get("year"), get("month") - 1, get("day")) / 86_400_000), hour: get("hour") % 24 };
}

const NIGHT_ENDS = 5;
const EVENING_STARTS = 17;

function partLabel(hour: number): string {
  if (hour < 12) return "This morning";
  if (hour < EVENING_STARTS) return "This afternoon";
  return "This evening";
}

function part(hour: number): number {
  if (hour < NIGHT_ENDS) return 0;
  if (hour < 12) return 1;
  if (hour < EVENING_STARTS) return 2;
  return 3;
}

function bucketLabel(at: LocalStamp, now: LocalStamp): string {
  const ago = now.day - at.day;
  if (now.hour < NIGHT_ENDS) {
    // After midnight: the evening before and the night so far are tonight.
    if ((ago === 0) || (ago === 1 && at.hour >= EVENING_STARTS)) return "Tonight";
    if (ago === 1) return "Yesterday";
    return "Earlier";
  }
  if (ago === 0) {
    if (at.hour < NIGHT_ENDS) return "Last night";
    return part(at.hour) === part(now.hour) ? partLabel(now.hour) : "Earlier today";
  }
  if (ago === 1) return at.hour >= EVENING_STARTS ? "Last night" : "Yesterday";
  return "Earlier";
}

export interface DidBucket {
  label: string;
  items: TodayDid[];
}

/** "This evening / Earlier today / Last night / Yesterday", newest first
 *  within each (review 2026-09-26: moving his actions to the front made the
 *  clock run backwards; his colour already marks them). */
export function bucketDid(rows: TodayDid[], now: Date, tz?: string): DidBucket[] {
  const nowStamp = localStamp(now, tz);
  const sorted = [...rows].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const buckets: DidBucket[] = [];
  for (const d of sorted) {
    const t = new Date(d.at);
    const label = Number.isNaN(t.getTime()) ? "Earlier" : bucketLabel(localStamp(t, tz), nowStamp);
    let b = buckets.find((x) => x.label === label);
    if (!b) {
      b = { label, items: [] };
      buckets.push(b);
    }
    b.items.push(d);
  }
  return buckets;
}

/** The line under "Your conversations", in his voice; none when there are none. */
export function describeThoughts(n: number): string | null {
  if (!n) return null;
  return n === 1 ? "1 thought of my own today" : `${n} thoughts of my own today`;
}

/** A conversation that came from outside (a text to Edward's number) gets a dot. */
export function isInbound(c: Pick<TodayConversation, "source">): boolean {
  return c.source === "external_message";
}

/** Done (or dismiss) clears a card and can wait out an Undo before it is
 *  sent (review 2026-09-26, design §1.7 "undo instead of confirm"). A held
 *  send is never one: a send, a dose, an answer or a snooze goes at once. */
export function isDeferredDone(need: TodayNeed, a: TodayAction): boolean {
  return need.kind !== "approval" && a.method !== "GET" && (a.id === "done" || a.id === "dismiss");
}

/** A note from GET /api/notes. */
export interface LiveNote {
  id: string;
  kind?: string;
  title: string;
  body?: string;
  href?: string;
  needs_you?: boolean;
}

export interface HeldBackRow {
  id: string;
  title: string;
  body?: string;
  href: string;
  /** Notes sharing this title (one row, as one card). */
  count: number;
}

/** "And 3 more notes": the notes that need the owner and are not already a card
 *  above (review 2026-09-26) — no whispers, no ideas — folded by title the
 *  way the cards are, newest first (the order /api/notes sends). */
export function heldBackNotes(notes: LiveNote[], needs: Pick<TodayNeed, "note_ids" | "id">[]): HeldBackRow[] {
  const onCards = new Set<string>();
  for (const n of needs) {
    for (const id of n.note_ids ?? []) onCards.add(id);
    if (n.id.startsWith("note:")) onCards.add(n.id.slice(5));
  }
  const rows = new Map<string, HeldBackRow>();
  for (const n of notes ?? []) {
    if (!n || n.needs_you !== true || n.kind === "idea" || onCards.has(n.id)) continue;
    const key = (n.title ?? "").trim().toLowerCase();
    const row = rows.get(key);
    if (row) {
      row.count += 1;
      continue;
    }
    rows.set(key, { id: n.id, title: n.title, ...(n.body ? { body: n.body } : {}), href: n.href || "/today", count: 1 });
  }
  return Array.from(rows.values());
}
