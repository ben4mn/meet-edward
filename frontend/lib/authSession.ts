/**
 * The pure half of "am I logged in?" — tested in __tests__/authSession.test.ts.
 *
 * Two facts about the app's auth state live in the browser:
 *  - the cookie (HttpOnly; only the server can read it), and
 *  - a localStorage hint that lets the shell render optimistically instead
 *    of showing a spinner on every cold open of the PWA.
 *
 * The hint is a guess. Anything that *leaves* the login page or *starts* a
 * session must wait for the server's verdict (`authChecked`), and a 401 from
 * any call must clear the hint so the next mount does not guess wrong again.
 * 2026-09-21: without those two rules a phone with a dead cookie bounced
 * /login → /chat → 401 → /login forever.
 *
 * 2026-09-26: an unreachable server is not a verdict. A 500 while uvicorn
 * restarts (or the tunnel's 502) used to read as "signed out": the hint was
 * cleared and the owner was sent to /login with a perfectly good cookie. Only a
 * real answer from /api/auth/status (`authVerdict`) decides; anything else
 * keeps the hint and retries (`authRetryDelayMs`). Signing out clears what
 * the tab holds of the owner's (`clearSessionContent`).
 */

export const AUTH_HINT_KEY = "edward_auth_hint";

export interface HintStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function readAuthHint(storage: HintStorage | null | undefined): boolean {
  if (!storage) return false;
  try {
    return storage.getItem(AUTH_HINT_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeAuthHint(storage: HintStorage | null | undefined, authenticated: boolean): void {
  if (!storage) return;
  try {
    if (authenticated) storage.setItem(AUTH_HINT_KEY, "1");
    else storage.removeItem(AUTH_HINT_KEY);
  } catch {
    /* localStorage unavailable */
  }
}

export interface LoginGateState {
  isLoading: boolean;
  isAuthenticated: boolean;
  /** True once the server has answered /api/auth/status at least once. */
  authChecked: boolean;
  /** The server has no password yet: the page is the setup form. */
  needsSetup?: boolean;
}

/** May the login page send the user on? Only on a verified session with a
 *  password set — with none (a restore lost the row while the cookie still
 *  verifies) AuthGuard sends setup here, and leaving again looped forever. */
export function shouldLeaveLogin(s: LoginGateState): boolean {
  return !s.isLoading && s.authChecked && s.isAuthenticated && !s.needsSetup;
}

export interface AuthStatusVerdict {
  configured: boolean;
  authenticated: boolean;
  /** EDWARD_OWNER_NAME on the server ("" when unset); present only when the
   *  server sent it (lib/owner.ts). */
  owner_name?: string;
  /** The setup form needs the one-time code (backend/.setup_code); present
   *  only when the server sent it. */
  setup_code_required?: boolean;
}

/** A real answer from /api/auth/status, or null: the server was not
 *  reachable (a 5xx page, a proxy error, an odd body) — never "signed out". */
export function authVerdict(raw: unknown): AuthStatusVerdict | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as { configured?: unknown; authenticated?: unknown; owner_name?: unknown; setup_code_required?: unknown };
  if (typeof r.authenticated !== "boolean") return null;
  const verdict: AuthStatusVerdict = {
    configured: typeof r.configured === "boolean" ? r.configured : true,
    authenticated: r.authenticated,
  };
  if (typeof r.owner_name === "string") verdict.owner_name = r.owner_name;
  if (typeof r.setup_code_required === "boolean") verdict.setup_code_required = r.setup_code_required;
  return verdict;
}

/** Wait before asking again after the server was unreachable: 1, 2, 4 … 30 s. */
export function authRetryDelayMs(attempt: number): number {
  return Math.min(30_000, 1_000 * 2 ** Math.max(0, attempt));
}

/** Window event: the owner signed out (module caches — the Today feed — drop what they hold). */
export const SIGNED_OUT_EVENT = "edward:signed-out";

/** sessionStorage keys (and prefixes) that hold the owner's content. */
const SESSION_CONTENT = ["edward:today:v1"];
const SESSION_CONTENT_PREFIXES = ["edward:draft:"];
/** localStorage keys that hold the owner's content (lib/presenceModel MIND_NOW_CACHE_KEY). */
const LOCAL_CONTENT = ["edward:mindNow:v1"];

export interface ClearableStorage {
  readonly length: number;
  key(index: number): string | null;
  removeItem(key: string): void;
}

function clearFrom(storage: ClearableStorage | null | undefined, exact: string[], prefixes: string[]): void {
  if (!storage) return;
  try {
    const doomed: string[] = [];
    for (let i = 0; i < storage.length; i++) {
      const k = storage.key(i);
      if (k && (exact.includes(k) || prefixes.some((p) => k.startsWith(p)))) doomed.push(k);
    }
    for (const k of doomed) storage.removeItem(k);
  } catch {
    /* storage blocked */
  }
}

/** Forget what the tab holds of the owner's: the Today feed, composer drafts and
 *  the cached mind/now answer. Theme, the wave and other settings stay. */
export function clearSessionContent(
  session: ClearableStorage | null | undefined = sessionStore(),
  local: ClearableStorage | null | undefined = browserStorage(),
): void {
  clearFrom(session, SESSION_CONTENT, SESSION_CONTENT_PREFIXES);
  clearFrom(local, LOCAL_CONTENT, []);
}

/** Where a 401 sends the page: /login carrying the page to come back to. */
export function loginUrlFor(pathname: string, search: string): string {
  const dest = `${pathname || ""}${search || ""}`;
  if (!pathname || pathname === "/" || pathname === "/login") return "/login";
  return `/login?redirect=${encodeURIComponent(dest)}`;
}

function browserStorage(): (HintStorage & ClearableStorage) | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function sessionStore(): ClearableStorage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/**
 * A 401 arrived: forget the optimistic hint and go to /login (once — a
 * page already on /login is left alone so pollers cannot reload it).
 * Returns whether a navigation was started.
 */
export function redirectToLogin(): boolean {
  if (typeof window === "undefined") return false;
  writeAuthHint(browserStorage(), false);
  // A dead session: nothing of the owner's stays in the tab (the navigation below
  // is a full load, so module memory goes with it).
  clearSessionContent();
  if (window.location.pathname === "/login") return false;
  window.location.href = loginUrlFor(window.location.pathname, window.location.search);
  return true;
}

