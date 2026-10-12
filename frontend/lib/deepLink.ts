// ?id= / ?flag= deep links in the settings panels (2026-09-26). Pure;
// pinned in __tests__/deepLink.test.ts.
//
// A panel reads its deep link from useSearchParams() (null outside the app
// router, as in tests), opens the thing it names, and on close strips the
// param with history.replaceState so a refresh does not reopen it.

export interface ParamsLike {
  get(key: string): string | null;
}

export function readParam(params: ParamsLike | null | undefined, key: string): string | null {
  const v = params?.get(key);
  if (typeof v !== "string") return null;
  const t = v.trim();
  return t ? t : null;
}

/** `href` without the named query params (path, other params and hash kept). */
export function withoutParams(href: string, keys: string[]): string {
  const hashAt = href.indexOf("#");
  const hash = hashAt >= 0 ? href.slice(hashAt) : "";
  const rest = hashAt >= 0 ? href.slice(0, hashAt) : href;
  const qAt = rest.indexOf("?");
  if (qAt < 0) return href;
  const path = rest.slice(0, qAt);
  const params = new URLSearchParams(rest.slice(qAt + 1));
  for (const k of keys) params.delete(k);
  const q = params.toString();
  return `${path}${q ? `?${q}` : ""}${hash}`;
}

/** Strip params from the address bar without a navigation. Keeps the
 *  router's own history state so back/forward still work. */
export function clearUrlParams(keys: string[]): void {
  if (typeof window === "undefined") return;
  const { pathname, search, hash } = window.location;
  const next = withoutParams(`${pathname}${search}${hash}`, keys);
  if (next !== `${pathname}${search}${hash}`) {
    window.history.replaceState(window.history.state, "", next);
  }
}
