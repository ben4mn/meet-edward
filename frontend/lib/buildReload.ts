"use client";

// A tab running an old bundle reloads itself (H3, 2026-09-27 follow-ups).
//
// Why: nothing told an open tab that a new build existed. The /screen tab
// in the owner's Chrome kept the pre-deploy bundle after the 2026-09-26 deploy —
// 36,694 GET /api/display/signals the next day at the old 2 s cadence from
// two pollers, the new code's /api/mind/now almost never — so the display
// fixes (shared poll, trust light, all-day events) were not on the monitor.
// The hub's kiosk and a PWA left open do the same.
//
// How: next.config.js bakes one build id into the client and the server
// (NEXT_PUBLIC_BUILD_ID, the same value as .next/BUILD_ID). The tab asks
// the server which build it runs (GET /build-id — a Next route, never the
// backend and never a cache) every few minutes and whenever it comes back
// into view, and reloads when the two differ:
//   - display and hub routes (the (display) layout): whenever nobody has
//     touched the screen for half a minute;
//   - the app (the (app) layout, AppBuildReload): only when the owner is idle —
//     never mid chat turn, never with live voice on, never with something
//     typed, attached or queued in the composer, never within two minutes
//     of a tap, key or scroll (wheel or touch).
// On every surface the page's own work holds a reload too (pageHoldsWork,
// 2026-09-27 review): something written in a box, a video or sound
// playing (or set to play by itself), a player embedded from elsewhere. Any component can hold reloads
// while it has something to lose that the page does not show
// (useBuildReloadHold: the composer, an open microphone). A reload that
// left the old bundle running (a stale cache) is not retried for ten
// minutes.
//
// React only: this sits in the display, hub and Today first loads, so it
// must never pull framer-motion or the chat into them
// (__tests__/buildReloadWiring.test.tsx).

import { useEffect, useRef } from "react";

/** The build this bundle was built from (inlined by next.config.js). */
export const BAKED_BUILD_ID: string = process.env.NEXT_PUBLIC_BUILD_ID ?? "";

/** Outside /api: next.config.js rewrites /api/* to the backend. */
export const BUILD_ID_PATH = "/build-id";

/** "Every few minutes." */
export const CHECK_EVERY_MS = 3 * 60_000;
/** The first look after a page opens (it was just served, so it is rarely stale). */
export const FIRST_CHECK_MS = 20_000;
/** How long since the last tap, key or scroll before a reload may happen. */
export const INPUT_QUIET_MS = { display: 30_000, app: 120_000 } as const;
/** A reload toward a build that left the old bundle running waits this long before another. */
export const RETRY_GUARD_MS = 10 * 60_000;

export const LAST_ATTEMPT_KEY = "edward:buildReload:v1";

export type BuildSurface = "display" | "app";

export interface BuildReloadBusy {
  /** A chat turn is running (app only). */
  turn?: boolean;
  /** Live voice is on (app only). */
  voice?: boolean;
  /** Something typed, attached or queued in the composer (app only). */
  composer?: boolean;
  /** A component asked to hold reloads (useBuildReloadHold): every surface. */
  held?: boolean;
  /** The page itself has work a reload would lose (pageHoldsWork): every surface. */
  page?: boolean;
}

export interface LastAttempt {
  to: string;
  at: number;
}

export interface BuildReloadState {
  baked: string | null | undefined;
  served: string | null | undefined;
  surface: BuildSurface;
  /** ms since the last tap, key or scroll on this page (Infinity: none yet). */
  sinceInputMs: number;
  now: number;
  busy?: BuildReloadBusy;
  lastAttempt?: LastAttempt | null;
}

export type BuildReloadDecision = "reload" | "current" | "unknown" | "busy" | "gave-up";

export function buildReloadDecision(s: BuildReloadState): BuildReloadDecision {
  const baked = (s.baked ?? "").trim();
  const served = (s.served ?? "").trim();
  if (!baked || !served) return "unknown";
  if (baked === served) return "current";
  if (s.lastAttempt && s.lastAttempt.to === served && s.now - s.lastAttempt.at < RETRY_GUARD_MS) return "gave-up";
  const busy = s.busy ?? {};
  if (busy.held || busy.page) return "busy";
  if (s.sinceInputMs < INPUT_QUIET_MS[s.surface]) return "busy";
  if (s.surface === "app" && (busy.turn || busy.voice || busy.composer)) return "busy";
  return "reload";
}

/** The route's body ({build_id}) → the id, or null for anything else. */
export function parseServedBuildId(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const id = (body as { build_id?: unknown }).build_id;
  return typeof id === "string" && id.trim() ? id.trim() : null;
}

export async function fetchServedBuildId(): Promise<string | null> {
  try {
    const res = await fetch(`${BUILD_ID_PATH}?t=${Date.now()}`, { cache: "no-store", credentials: "same-origin" });
    if (!res.ok) return null;
    return parseServedBuildId(await res.json());
  } catch {
    return null;
  }
}

type AttemptStorage = Pick<Storage, "getItem" | "setItem">;

function sessionStore(): AttemptStorage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

function readLastAttempt(storage: AttemptStorage | null): LastAttempt | null {
  try {
    const v = JSON.parse(storage?.getItem(LAST_ATTEMPT_KEY) ?? "null");
    return v && typeof v.to === "string" && typeof v.at === "number" ? v : null;
  } catch {
    return null;
  }
}

function writeLastAttempt(storage: AttemptStorage | null, attempt: LastAttempt): void {
  try {
    storage?.setItem(LAST_ATTEMPT_KEY, JSON.stringify(attempt));
  } catch {
    /* private mode: the ten-minute guard simply is not kept */
  }
}

// ---- what the page itself would lose -------------------------------------------
//
// The composer was the only thing that held a reload, so a new document
// pasted into its sheet and read over for two minutes, a message
// half-typed into the /screen compose bar, a video someone was watching
// on /screen or the hub, or a stream embedded there was lost or started
// over as soon as nobody had touched the page for a while (2026-09-27
// review). Read from the page at the moment of each check, so every form,
// player and embed counts without having to remember to ask.

const TEXT_INPUT_TYPES = new Set(["text", "search", "email", "url", "tel"]);

function isTextField(el: Element): el is HTMLInputElement | HTMLTextAreaElement {
  if (el.tagName === "TEXTAREA") return true;
  return el.tagName === "INPUT" && TEXT_INPUT_TYPES.has((el as HTMLInputElement).type);
}

function writable(el: HTMLInputElement | HTMLTextAreaElement): boolean {
  return !el.disabled && !el.readOnly;
}

function isEditableContent(el: Element): boolean {
  if ((el as HTMLElement).isContentEditable) return true;
  const attr = el.getAttribute("contenteditable");
  return attr !== null && attr.toLowerCase() !== "false";
}

/**
 * Would a reload lose something on this page? Text being written — the
 * focused field, or any writable textarea with text in it (the phone
 * drops focus when the PWA goes to the background; a one-line field at
 * rest is usually a filled-in setting, not a draft) — a video or sound
 * playing, one that plays by itself (autoplay: a /screen or hub video
 * show, finished or paused, would start over with sound after a reload —
 * pre-deploy review, 2026-09-27), or a player embedded from another site
 * (an iframe with a src: it may be playing and a reload restarts it; the
 * /screen tiles are srcdoc and do not count).
 */
export function pageHoldsWork(doc: Document): boolean {
  const focused = doc.activeElement;
  if (focused && focused !== doc.body) {
    if (isTextField(focused) && writable(focused) && focused.value.trim()) return true;
    if (isEditableContent(focused) && (focused.textContent ?? "").trim()) return true;
  }
  for (const t of Array.from(doc.querySelectorAll("textarea"))) {
    if (writable(t) && t.value.trim()) return true;
  }
  for (const m of Array.from(doc.querySelectorAll<HTMLMediaElement>("video, audio"))) {
    if (!m.paused && !m.ended) return true;
    if (m.autoplay || m.hasAttribute("autoplay")) return true;
  }
  for (const f of Array.from(doc.querySelectorAll("iframe[src]"))) {
    const src = (f.getAttribute("src") ?? "").trim();
    if (src && src !== "about:blank") return true;
  }
  return false;
}

// ---- holds ------------------------------------------------------------------

const holds = new Set<symbol>();

/** Some component on the page has something a reload would lose. */
export function reloadHeld(): boolean {
  return holds.size > 0;
}

/** Hold reloads while `active` (the composer: text, attachments). */
export function useBuildReloadHold(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const token = Symbol("build-reload-hold");
    holds.add(token);
    return () => {
      holds.delete(token);
    };
  }, [active]);
}

// ---- the hook -----------------------------------------------------------------

export interface BuildReloadDeps {
  bakedId?: string;
  fetchServed?: () => Promise<string | null>;
  reload?: () => void;
  now?: () => number;
  storage?: AttemptStorage | null;
  firstCheckMs?: number;
  everyMs?: number;
}

// What a person at the page sends. Not "scroll": a page that scrolls itself
// (the /screen reply card following a streamed answer) would read as
// someone there and never reload; a person scrolling sends wheel, touch or
// key events anyway.
const INPUT_EVENTS = ["pointerdown", "keydown", "wheel", "touchstart"] as const;

/**
 * Compare this bundle's build with the server's and reload when they
 * differ and the page is idle (buildReloadDecision). `busy` is read at each
 * check — an object from the last render, or a function asked at the
 * moment of the check — so passing a fresh one every render costs nothing.
 */
export function useBuildReload(
  surface: BuildSurface,
  busy?: BuildReloadBusy | (() => BuildReloadBusy),
  deps: BuildReloadDeps = {},
): void {
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const depsRef = useRef(deps);
  depsRef.current = deps;

  useEffect(() => {
    const d = depsRef.current;
    const baked = d.bakedId ?? BAKED_BUILD_ID;
    if (!baked || typeof window === "undefined") return;
    const now = d.now ?? Date.now;
    const storage = d.storage === undefined ? sessionStore() : d.storage;
    let lastInput = -Infinity;
    let checking = false;
    let stopped = false;

    const onInput = () => {
      lastInput = now();
    };

    const check = async () => {
      if (checking || stopped || document.visibilityState === "hidden") return;
      checking = true;
      try {
        const served = await (d.fetchServed ?? fetchServedBuildId)();
        if (stopped) return;
        const t = now();
        const decision = buildReloadDecision({
          baked,
          served,
          surface,
          sinceInputMs: t - lastInput,
          now: t,
          busy: {
            ...(typeof busyRef.current === "function" ? busyRef.current() : busyRef.current),
            held: reloadHeld(),
            page: pageHoldsWork(document),
          },
          lastAttempt: readLastAttempt(storage),
        });
        if (decision === "reload" && served) {
          writeLastAttempt(storage, { to: served, at: t });
          (d.reload ?? (() => window.location.reload()))();
        }
      } finally {
        checking = false;
      }
    };

    const onVisible = () => {
      if (document.visibilityState !== "hidden") void check();
    };
    // A page's own load fires pageshow too, and it was just served; only a
    // page restored from the back/forward cache may be running an old build.
    const onPageShow = (e: PageTransitionEvent) => {
      if (e.persisted) onVisible();
    };

    for (const e of INPUT_EVENTS) window.addEventListener(e, onInput, { capture: true, passive: true });
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("pageshow", onPageShow);
    window.addEventListener("online", onVisible);
    const first = setTimeout(() => void check(), d.firstCheckMs ?? FIRST_CHECK_MS);
    const every = setInterval(() => void check(), d.everyMs ?? CHECK_EVERY_MS);
    return () => {
      stopped = true;
      for (const e of INPUT_EVENTS) window.removeEventListener(e, onInput, { capture: true });
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("pageshow", onPageShow);
      window.removeEventListener("online", onVisible);
      clearTimeout(first);
      clearInterval(every);
    };
  }, [surface]);
}
