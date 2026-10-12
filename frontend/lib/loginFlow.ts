// Edward at the door (app/(auth)/login) — the pure half, pinned in
// __tests__/loginFlow.test.ts.

import type { Gaze } from "./edwardMood";

/** Signed in, the owner lands on Today — the PWA's start_url — not on chat. */
export const FRONT_DOOR = "/today";

/** The success nod plays in full (BEAT_MS.nod is 520)… */
export const LOGIN_NOD_MS = 540;
/** …then the door fades out (the slow motion token) and the app comes in. */
export const LOGIN_FADE_MS = 360;

const PROBE_ORIGIN = "https://edward.invalid";

/** Where to go after signing in: `redirect` when it is a safe same-site
 *  path that is not the login page (or /, which redirects to it).
 *  2026-09-26: the URL parser drops tab/LF/CR anywhere and reads "\\" as
 *  "/", so "/\t/evil.com" resolved to https://evil.com — control
 *  characters and backslashes are refused, the path is resolved against a
 *  probe origin that must survive, and the loop check runs on the resolved
 *  path ("/./login" is /login). */
export function loginRedirectTarget(redirect: string | null | undefined): string {
  const r = (redirect ?? "").trim();
  // eslint-disable-next-line no-control-regex
  if (!r.startsWith("/") || r.startsWith("//") || /[\u0000-\u001f\u007f\\]/.test(r)) return FRONT_DOOR;
  let u: URL;
  try {
    u = new URL(r, PROBE_ORIGIN);
  } catch {
    return FRONT_DOOR;
  }
  if (u.origin !== PROBE_ORIGIN) return FRONT_DOOR;
  const path = u.pathname;
  if (path === "/" || path === "/login" || path.startsWith("/login/")) return FRONT_DOOR;
  return path + u.search + u.hash;
}

/** He looks away while a hidden password is typed and peeks when it is shown. */
export function loginGaze(s: { reveal: boolean; hasText: boolean }): Gaze | null {
  if (!s.hasText) return null;
  return s.reveal ? "down" : "up";
}

/** Under the setup form's code field: where the one-time code is. */
export const SETUP_CODE_HINT = "It's in backend/.setup_code on the Mac Edward runs on.";

/** The setup form asks for the code only while no password exists and the
 *  server says so (GET /api/auth/status → setup_code_required). */
export function asksForSetupCode(s: { needsSetup: boolean; setupCodeRequired: boolean }): boolean {
  return s.needsSetup && s.setupCodeRequired;
}

/** api.setupPassword's fallback when the server sent no reason. */
const SETUP_FALLBACK = /^failed to setup password$/i;

/** A login error in his voice: what happened and what to do. Setting the
 *  first password, the server's own refusal is shown as it is — a wrong
 *  setup code, "only on the Mac Edward runs on", "already set" — never
 *  "That's not it", which reads as a wrong password (2026-10-12 review). */
export function loginErrorCopy(message: string | null | undefined, opts: { setup?: boolean } = {}): string {
  const raw = (message ?? "").trim();
  const m = raw.toLowerCase();
  if (/unavailable|failed to fetch|network|load failed|server running|timed? ?out/.test(m)) {
    return "I can't reach the Mac right now. Try again in a moment.";
  }
  if (/too many|rate|429|slow down/.test(m)) return "Too many tries. Give it a minute.";
  if (opts.setup) {
    return raw && !SETUP_FALLBACK.test(raw) ? raw : "That didn't work. Try again?";
  }
  return "That's not it. Try again?";
}

/** Did the server refuse the setup code (so the code field gets the focus)? */
export function isSetupCodeRefusal(message: string | null | undefined): boolean {
  return /setup code/i.test(message ?? "");
}
