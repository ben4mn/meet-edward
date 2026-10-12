// Which build this server runs (H3, 2026-09-27 follow-ups).
//
// Every open tab compares this with the id its own bundle was built with
// (lib/buildReload.ts) and reloads onto a new deploy when they differ — the
// /screen tab in the owner's Chrome and the hub's kiosk used to run the old bundle
// until someone reloaded by hand. next.config.js inlines the same value
// here and into the client (NEXT_PUBLIC_BUILD_ID, also .next/BUILD_ID), so
// the answer changes exactly when `next start` serves a new build.
//
// Outside /api on purpose: next.config.js rewrites /api/* to the backend.
// Dynamic and uncacheable, and NetworkOnly in the service worker
// (pwa-caching.js): a cached answer would say the old build forever.

export const dynamic = "force-dynamic";
export const revalidate = 0;

export function GET(): Response {
  return new Response(JSON.stringify({ build_id: process.env.NEXT_PUBLIC_BUILD_ID ?? "" }), {
    status: 200,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store, max-age=0",
    },
  });
}
