// Runtime caching rules for the service worker (next-pwa / Workbox).
// Kept out of next.config.js so a test can load them: __tests__/pwaCaching.test.ts.
//
// Rule of the file: the worker caches *immutable* things (hashed bundles,
// media) and never anything that says who you are or what is true now.
// 2026-09-21: `/api/auth/status` was StaleWhileRevalidate for an hour, so a
// phone whose cookie had just died could be told "authenticated" from the
// cache, render the app, 401 on the first real call, bounce to /login, be
// told "authenticated" again, and loop. The auth check is NetworkOnly.

const runtimeCaching = [
  {
    // Immutable JS/CSS bundles — cache aggressively
    urlPattern: /\/_next\/static\/.*/,
    handler: 'CacheFirst',
    options: {
      cacheName: 'static-assets',
      expiration: {
        maxEntries: 200,
        maxAgeSeconds: 30 * 24 * 60 * 60, // 30 days
      },
    },
  },
  {
    // The answer to "am I logged in?" is never served from a cache.
    urlPattern: /\/api\/auth\/.*/,
    handler: 'NetworkOnly',
  },
  {
    // "Which build does the server run?" (app/build-id/route.ts). Every tab
    // compares it with its own bundle and reloads onto a new deploy
    // (lib/buildReload.ts, H3 2026-09-27); a cached answer would keep the
    // /screen tab and the hub kiosk on the old build forever.
    urlPattern: /\/build-id(?:\?.*)?$/,
    handler: 'NetworkOnly',
  },
  {
    // Hub screensaver photos. MUST stay above the image rule below —
    // Workbox takes the first matching route, and `/api/hub/photos/x.jpg`
    // ends in .jpg, so it was being CacheFirst'd for 30 days. That made a
    // photo you deleted in settings carry on rendering, and a re-used
    // filename serve the old image forever.
    //
    // NetworkOnly still goes through the browser's own HTTP cache, so the
    // Pi kiosk keeps the 24h `Cache-Control` the endpoint sets and isn't
    // re-downloading the album every slide.
    urlPattern: /\/api\/hub\/photos/,
    handler: 'NetworkOnly',
  },
  {
    // The household photo door (app/photos): its session check and the
    // same album. Above the image rule for the same reason as the hub's photos.
    urlPattern: /\/api\/photo-door\//,
    handler: 'NetworkOnly',
  },
  {
    // Images and fonts
    urlPattern: /\.(?:png|jpg|jpeg|svg|gif|webp|ico|woff2?|ttf|eot)$/,
    handler: 'CacheFirst',
    options: {
      cacheName: 'media-assets',
      expiration: {
        maxEntries: 100,
        maxAgeSeconds: 30 * 24 * 60 * 60, // 30 days
      },
    },
  },
  {
    // Voice endpoints must bypass the service worker entirely
    urlPattern: /\/api\/voice\/.*/,
    handler: 'NetworkOnly',
  },
  {
    // SSE chat endpoint should not be cached
    urlPattern: /\/api\/chat$/,
    handler: 'NetworkOnly',
  },
];

/** The handler Workbox would pick for a URL path, or undefined (plain network).
 *  Workbox's RegExpRoute tests a pattern against the whole URL (href), so
 *  this does too: a pattern anchored at the path ("^/build-id") would pass a
 *  path-only check here and never match in the real worker. */
function handlerFor(path) {
  const url = new URL(path, 'https://edward.localhost');
  const target = url.href;
  for (const rule of runtimeCaching) {
    const p = rule.urlPattern;
    const hit = p instanceof RegExp ? p.test(target) : typeof p === 'function' ? p({ url }) : p === target;
    if (hit) return rule.handler;
  }
  return undefined;
}

module.exports = { runtimeCaching, handlerFor };
