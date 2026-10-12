// The presence hero on Today (UX pass 2026-09-26). Pure: the date line, the
// quiet subline under Edward's greeting, how a time reads, and which beat
// the hero Edward may play. Every word comes from a real signal —
// /api/today's `presence`, GET /api/mind/now, the folded wakes row — and a
// failed wake never shows its error. Pinned in __tests__/todayHero.test.ts.

import { formatClock, type Beat, type Mood } from "@/lib/edwardMood";
import type { MindNow } from "@/lib/mindNowApi";
import { getOwnerName } from "@/lib/owner";
import type { TodayDid, TodayPresence } from "./todayModel";

export interface ClockOpts {
  now?: Date;
  tz?: string;
}

interface Local {
  day: number;
  hour: number;
  minute: number;
}

function local(date: Date, tz: string | undefined): Local {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  return {
    day: Math.round(Date.UTC(get("year"), get("month") - 1, get("day")) / 86_400_000),
    hour: get("hour") % 24,
    minute: get("minute"),
  };
}

/** The app's one clock format ("7:34 pm"), shared with edwardMood. */
function clock(l: Local): string {
  return formatClock(l.hour, l.minute);
}

/** "in 38 min" · "8:00 pm" · "8:30 pm" · "tomorrow 7:00 am" · "Tue 6:00 pm" · "Oct 9". */
export function shortClock(iso: string, opts: ClockOpts = {}): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const now = opts.now ?? new Date();
  const mins = Math.round((at.getTime() - now.getTime()) / 60_000);
  if (mins > 0 && mins < 60) return `in ${mins} min`;
  const a = local(at, opts.tz);
  const n = local(now, opts.tz);
  const days = a.day - n.day;
  if (days === 0) return clock(a);
  if (days === 1) return `tomorrow ${clock(a)}`;
  if (days > 1 && days < 7) {
    const wd = new Intl.DateTimeFormat("en-US", { timeZone: opts.tz, weekday: "short" }).format(at);
    return `${wd} ${clock(a)}`;
  }
  return new Intl.DateTimeFormat("en-US", { timeZone: opts.tz, month: "short", day: "numeric" }).format(at);
}

// Opening words that are ordinary words, safe to lower-case after "last:".
// Anything else (a name, "I", an acronym) keeps its capital.
const ORDINARY = new Set(
  (
    "left replied sent pushed drafted noted read looked checked told asked scheduled saved wrote found kept " +
    "moved updated answered held set put showed cleared nothing no quiet all everything some a an the one two " +
    "reminded logged booked added took gave started finished stood reached tidied wished"
  ).split(" "),
);

const SOFT_MAX = 48;

/** His closing line, as a quiet clause: "Left Theo's thread alone." →
 *  "left Theo's thread alone"; its first clause, at most 48 characters. */
export function softLine(line: string): string {
  // The first clause is the point; the rest is in the Thoughts panel.
  let s = (line ?? "").replace(/\s+/g, " ").trim().split(/;\s|\s—\s|\.\s/)[0].replace(/[.!]+$/, "");
  const first = s.split(" ")[0] ?? "";
  if (ORDINARY.has(first.toLowerCase()) && first !== first.toUpperCase()) s = s[0].toLowerCase() + s.slice(1);
  if (s.length > SOFT_MAX) s = s.slice(0, SOFT_MAX - 1).replace(/[\s,;:·—-]+\S*$/, "").trimEnd() + "…";
  return s;
}

export interface HeroSublineInput {
  presence: TodayPresence | null;
  mindNow: MindNow | null;
  /** The folded wakes row's count, when "What I did" has one. */
  wakes: number | null;
  mood: Mood;
  /** The presence status line ("Thinking · morning wake"). */
  status: string;
  /** "What I did" rows: an acted look is named by its action's title. */
  actions?: TodayDid[];
  now: Date;
  tz?: string;
}

const QUIET_MOODS: ReadonlySet<Mood> = new Set<Mood>(["idle", "sleepy"]);

/** How close an action's entry must be to a wake to be that wake's. */
const ACTION_NEAR_WAKE_MS = 15 * 60_000;

function actionNear(actions: TodayDid[] | undefined, at: string | null | undefined): TodayDid | null {
  const t = at ? Date.parse(at) : NaN;
  if (!actions || Number.isNaN(t)) return null;
  const near = actions
    .filter((a) => a.kind === "action" && Math.abs(Date.parse(a.at) - t) <= ACTION_NEAR_WAKE_MS)
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  return near[0] ?? null;
}

function lastPart(
  outcome: string | null | undefined,
  line: string | null | undefined,
  at?: string | null,
  actions?: TodayDid[],
): string | null {
  if (outcome === "acted") {
    const action = actionNear(actions, at);
    if (action?.title) return `last: ${softLine(action.title)}`;
  }
  switch (outcome) {
    case "failed":
      return "last look didn't finish";
    case "skipped":
      return "skipped a look (quota spent)";
    case "acted":
    case "left_alone": {
      const soft = line ? softLine(line) : "";
      if (soft) return `last: ${soft}`;
      return outcome === "acted" ? "last: acted" : "last: left things alone";
    }
    default:
      return null;
  }
}

/** A subline part's words, joined with non-breaking spaces: the line
 *  breaks between parts, never inside one ("in 38" / "min"). */
export function keepTogether(part: string): string {
  return part.replace(/ /g, "\u00a0");
}

const HANDS_SUFFIX = " · Chrome link down";

/** The line under the greeting: what he is doing now (only when it is
 *  not rest), how many times he looked, what the last look did, when he
 *  looks next. Parts are joined with " · " by the page. Offline, only the
 *  past is said: nothing about now or next can be known. */
export function heroSubline(input: HeroSublineInput): string[] {
  const { presence, mindNow, wakes, mood, status, now, tz, actions } = input;
  const parts: string[] = [];
  const offline = mood === "offline";
  const waking = !offline && Boolean(mindNow?.waking);
  // Review 2026-09-26: what is wrong is said whatever the mood — "never
  // 'fine' when his hands are down", and a spent week has no next look.
  const handsDown = !offline && mindNow?.hands?.connected === false;
  const spent = !offline && mindNow?.quota?.exhausted === true;
  if (!QUIET_MOODS.has(mood) && status) {
    // The full sentence about his hands follows; the status need not say it too.
    const own = handsDown && status.endsWith(HANDS_SUFFIX) ? status.slice(0, -HANDS_SUFFIX.length) : status;
    parts.push(own.replace(" · ", " — "));
  }
  if (handsDown) parts.push("My hands in your Chrome are down");
  if (spent && mood !== "resting") {
    const back = mindNow?.quota.resets_at ? shortClock(mindNow.quota.resets_at, { now, tz }) : "";
    parts.push(back ? `looks resume ${back}` : "resting (quota spent)");
  }
  if (!offline && presence?.listening && presence.listening.length === 0) parts.push("Not reading messages");
  if (wakes && wakes > 0) parts.push(`${wakes} ${wakes === 1 ? "wake" : "wakes"}`);
  if (!waking) {
    const last = mindNow?.last
      ? lastPart(mindNow.last.outcome, mindNow.last.line, mindNow.last.at, actions)
      : lastPart(presence?.mind.last_outcome, presence?.mind.last_line, presence?.mind.last_wake_at, actions);
    if (last) parts.push(last);
  }
  const next = waking || spent || offline ? null : mindNow?.next_wake_at ?? presence?.mind.next_wake_at ?? null;
  if (next && Date.parse(next) > now.getTime()) parts.push(`next look ${shortClock(next, { now, tz })}`);
  return parts;
}

/** The beat the hero Edward plays: anything from the moment the page
 *  opened on (the day's wave) or later — never an old nod from another
 *  screen replayed on mount. */
export function heroBeat(
  beat: { beat: Beat; key: number; at: number } | null,
  mountedAt: number,
  windowMs = 2000,
): Beat | null {
  if (!beat) return null;
  return beat.at >= mountedAt - windowMs ? beat.beat : null;
}

/** "Saturday, September 26" in the owner's zone. */
export function dateOverline(now: Date, tz?: string): string {
  return new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "long", month: "long", day: "numeric" }).format(now);
}

/** "5:10 pm" / "9:00 am" in the owner's zone, for list rows; "" for a bad stamp. */
export function clockOnly(iso: string, tz?: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return clock(local(at, tz));
}

/** Does a streamed greeting grow the line on screen (keep its words), or
 *  replace it (start the fade over)? */
export function isContinuation(prev: string, next: string): boolean {
  return next.startsWith(prev);
}

// ---------------------------------------------------------------------------
// The greeting after the owner acts (review 2026-09-26, greeting-stale-after-action)
// ---------------------------------------------------------------------------

/** Capitalised words that are not a name or a thing the owner could have
 *  done. The owner's own name is one too, read at call time (lib/owner.ts). */
const NOT_NAMES: ReadonlySet<string> = new Set(
  (
    "Edward I I'm I've I'll I'd The This That These Those Your You My Our A An And But Or So If It Its " +
    "Send Sent Remind Reminder Done Open Check Call Tell Ask Reply Approve Which What When Where Who Why How " +
    "Is Are Was Were Due Today Tonight Tomorrow Yesterday Morning Afternoon Evening Night Needs Note Notes " +
    "Monday Tuesday Wednesday Thursday Friday Saturday Sunday Mon Tue Wed Thu Fri Sat Sun " +
    "January February March April May June July August September October November December " +
    "Jan Feb Mar Apr Jun Jul Aug Sep Sept Oct Nov Dec Hey Hi Hello Good"
  ).split(" "),
);

/** The words of the owner's name, lowercased ("" names nobody). */
function ownerWords(): Set<string> {
  return new Set(getOwnerName().toLowerCase().split(/\s+/).filter(Boolean));
}

function namesIn(text: string): Set<string> {
  const out = new Set<string>();
  const owner = ownerWords();
  for (const raw of (text ?? "").split(/[^A-Za-z'’-]+/)) {
    const w = raw.replace(/['’]s$/i, "").replace(/^['’-]+|['’-]+$/g, "");
    if (w.length >= 3 && /^[A-Z]/.test(w) && !NOT_NAMES.has(w) && !owner.has(w.toLowerCase())) out.add(w);
  }
  return out;
}

type NeedText = { kind?: string; title: string; detail?: string };

const CLAUSE_SEP = /(,\s+(?:and|but|so|while)\s+|;\s+)/;

/** Does the sentence name the owner ("Evening, Sam.")? Never, when no name is set. */
function namesOwner(sentence: string): boolean {
  const name = getOwnerName().trim();
  if (!name) return false;
  return new RegExp(`\\b${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(sentence);
}

function isSalutation(sentence: string): boolean {
  const words = sentence.trim().split(/\s+/).length;
  return words <= 3 || (words <= 5 && namesOwner(sentence));
}

/** The greeting without the clauses about needs the owner has since resolved:
 *  a clause naming someone or something (Biscuit, Jordan) from a need that
 *  has gone is dropped — unless a need of the same kind still waiting
 *  names it too (one of Biscuit's two doses given: her meds still wait; an
 *  email about Biscuit does not keep "her meds are waiting" true). The
 *  salutation stays; a line that names nothing gone comes back unchanged. */
export function greetingWithout(text: string, gone: NeedText[], live: NeedText[]): string {
  const goneKinds = new Map<string, Set<string>>();
  for (const n of gone) {
    namesIn(`${n.title} ${n.detail ?? ""}`).forEach((w) => {
      if (!goneKinds.has(w)) goneKinds.set(w, new Set());
      goneKinds.get(w)!.add(n.kind ?? "");
    });
  }
  for (const n of live) {
    namesIn(`${n.title} ${n.detail ?? ""}`).forEach((w) => {
      if (goneKinds.get(w)?.has(n.kind ?? "")) goneKinds.delete(w);
    });
  }
  const goneNames = new Set(Array.from(goneKinds.keys()));
  if (!text || goneNames.size === 0) return text;

  // (No lookbehind: older iOS WebKit cannot parse it.)
  const sentences = (text.trim().match(/[^.!?…]+(?:[.!?…]+|$)/g) ?? [text]).map((x) => x.trim()).filter(Boolean);
  let changed = false;
  const kept: string[] = [];
  sentences.forEach((sentence, i) => {
    if (i === 0 && isSalutation(sentence)) {
      kept.push(sentence);
      return;
    }
    const end = /[.!?…]+$/.exec(sentence)?.[0] ?? "";
    const body = end ? sentence.slice(0, -end.length) : sentence;
    const pieces = body.split(CLAUSE_SEP);
    const out: string[] = [];
    for (let k = 0; k < pieces.length; k += 2) {
      const clause = pieces[k];
      const names = namesIn(clause);
      if (Array.from(names).some((w) => goneNames.has(w))) {
        changed = true;
        continue;
      }
      if (out.length) out.push(pieces[k - 1] ?? ", ");
      out.push(out.length ? clause : clause.charAt(0).toUpperCase() + clause.slice(1));
    }
    if (out.length) kept.push(`${out.join("")}${end || "."}`);
  });
  return changed ? kept.join(" ") : text;
}
