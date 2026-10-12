// Single source of truth for date/time display across the app.
//
// Contract: backends hand us UTC ISO strings (ending in Z). These helpers
// parse them, localise exactly once to an explicit tz or the browser's
// resolved zone, and format for display. Never use `.toLocaleString(undefined, ...)`
// directly in components — call one of these instead.

export type TimeOpts = {
  /** Explicit IANA timezone, e.g. "America/Chicago". Falls back to browser. */
  tz?: string;
  /** Override "now" for deterministic tests. */
  now?: Date;
  /** Override the locale for tests. Defaults to en-US. */
  locale?: string;
};

/** Parse an ISO timestamp from any Edward API. A naive string (no Z, no
 *  offset) is UTC by contract — `new Date()` would read it as local time
 *  and turn "five hours ago" into "in 58m" for anyone west of Greenwich.
 *  Fractional seconds beyond milliseconds are trimmed so Safari parses
 *  Python's microsecond output. */
export function parseIso(iso: string): Date {
  if (typeof iso !== "string") return new Date(NaN);
  let s = iso.trim();
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(s);
  // 2026-09-06T02:30:42.820153 -> 2026-09-06T02:30:42.820
  s = s.replace(/(\.\d{3})\d+/, "$1");
  if (!hasZone && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(s)) s += "Z";
  return new Date(s);
}

function resolveTz(tz: string | undefined): string {
  if (tz) return tz;
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return "UTC";
  }
}

function sameLocalYear(
  a: Date,
  b: Date,
  tz: string,
  locale: string,
): boolean {
  const fmt = new Intl.DateTimeFormat(locale, { timeZone: tz, year: "numeric" });
  return fmt.format(a) === fmt.format(b);
}

function sameLocalDay(
  a: Date,
  b: Date,
  tz: string,
  locale: string,
): boolean {
  const fmt = new Intl.DateTimeFormat(locale, {
    timeZone: tz,
    year: "numeric",
    month: "numeric",
    day: "numeric",
  });
  return fmt.format(a) === fmt.format(b);
}

/**
 * Intl's parts joined in the app's one clock format, "7:34 pm" (L1,
 * 2026-09-27 follow-ups): the day period lower-case, after a plain space.
 * Intl says "9:00 AM", and browsers on a recent ICU put a narrow no-break
 * space (U+202F) before it — so the sidebar, the Listening log and the
 * Memories panel read "9:00 AM" beside Today's "7:34 pm"
 * (lib/edwardMood.formatClock, today_service._clock).
 */
export function joinClockParts(parts: Intl.DateTimeFormatPart[]): string {
  return parts
    .map((p, i) => {
      if (p.type === "dayPeriod") return p.value.toLowerCase();
      if (p.type === "literal" && parts[i + 1]?.type === "dayPeriod") return p.value.replace(/[\u00a0\u202f]/g, " ");
      return p.value;
    })
    .join("");
}

/** Human-friendly date + time. Shows year only when it differs from `now`. */
export function formatDateTime(iso: string, opts: TimeOpts = {}): string {
  const d = parseIso(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const tz = resolveTz(opts.tz);
  const locale = opts.locale ?? "en-US";
  const now = opts.now ?? new Date();

  const showYear = !sameLocalYear(d, now, tz, locale);

  return joinClockParts(
    new Intl.DateTimeFormat(locale, {
      timeZone: tz,
      month: "short",
      day: "numeric",
      year: showYear ? "numeric" : undefined,
      hour: "numeric",
      minute: "2-digit",
    }).formatToParts(d),
  );
}

/** Clock time only (e.g. "9:00 am"). An ISO string is read by parseIso
 *  (naive = UTC); a Date is taken as it is — the hub's clocks, and the
 *  calendar's naive local times, which must not be re-read as UTC
 *  (2026-09-27). An invalid Date is "". */
export function formatClock(at: string | Date, opts: TimeOpts = {}): string {
  const d = typeof at === "string" ? parseIso(at) : at;
  if (Number.isNaN(d.getTime())) return typeof at === "string" ? at : "";
  const tz = resolveTz(opts.tz);
  const locale = opts.locale ?? "en-US";

  return joinClockParts(
    new Intl.DateTimeFormat(locale, {
      timeZone: tz,
      hour: "numeric",
      minute: "2-digit",
    }).formatToParts(d),
  );
}

/** "just now" / "5m ago" / "2h ago" / "3d ago" — or "in 2h" for future. */
export function formatRelative(iso: string, opts: TimeOpts = {}): string {
  const d = parseIso(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const now = opts.now ?? new Date();
  const deltaMs = now.getTime() - d.getTime();
  const abs = Math.abs(deltaMs);
  const sec = Math.round(abs / 1000);

  if (sec < 60) return "just now";

  let value: number;
  let unit: string;
  if (sec < 3600) {
    value = Math.round(sec / 60);
    unit = "m";
  } else if (sec < 86400) {
    value = Math.round(sec / 3600);
    unit = "h";
  } else if (sec < 7 * 86400) {
    value = Math.round(sec / 86400);
    unit = "d";
  } else {
    value = Math.round(sec / (7 * 86400));
    unit = "w";
  }

  return deltaMs >= 0 ? `${value}${unit} ago` : `in ${value}${unit}`;
}

/** Day-bucket label for grouping lists: "Today" / "Yesterday" / "Mon Apr 14". */
export function formatDayHeader(iso: string, opts: TimeOpts = {}): string {
  const d = parseIso(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const tz = resolveTz(opts.tz);
  const locale = opts.locale ?? "en-US";
  const now = opts.now ?? new Date();

  if (sameLocalDay(d, now, tz, locale)) return "Today";

  const yesterday = new Date(now.getTime() - 86400 * 1000);
  if (sameLocalDay(d, yesterday, tz, locale)) return "Yesterday";

  const showYear = !sameLocalYear(d, now, tz, locale);
  return new Intl.DateTimeFormat(locale, {
    timeZone: tz,
    weekday: "short",
    month: "short",
    day: "numeric",
    year: showYear ? "numeric" : undefined,
  }).format(d);
}
