// Where next-pwa writes sw.js and the workbox runtime. A staged deploy
// build (deploy-build.sh: NEXT_DIST_DIR=.next-staging) runs while the old
// `next start` keeps serving public/ from disk, and a new worker there
// precaches manifests the old server cannot serve, so every install failed
// for the minutes the build ran (2026-09-27). Staged, the worker goes into
// the build's own EDWARD_PWA/ (ensure-build.sh STAGING_DIR / PWA_DIR), which
// the swap installs into public/. Pinned in __tests__/harnessSafety.test.ts.
const STAGING_DIR = '.next-staging';
const PWA_DEST = process.env.NEXT_DIST_DIR === STAGING_DIR ? `${STAGING_DIR}/EDWARD_PWA` : 'public';

const withPWA = require('next-pwa')({
  dest: PWA_DEST,
  register: true,
  skipWaiting: true,
  // Enable in development for testing (set to true to disable in dev)
  // Off only under the UX harness (scripts/mock-api/run-harness.sh), whose
  // `next dev` must never regenerate the public/sw.js production serves.
  disable: process.env.EDWARD_HARNESS === '1',
  // Custom service worker for push notifications
  sw: 'sw.js',
  // Include our push handler in the service worker
  importScripts: ['/sw-push.js'],
  // next start never serves this file (404); Workbox fails the whole
  // install when one precached entry fails, so with it in the list no
  // device could activate the worker or subscribe to push (2026-09-14).
  buildExcludes: [/app-build-manifest\.json$/],
  // The rules live in pwa-caching.js so __tests__/pwaCaching.test.ts can
  // pin them (the auth check is never cached; app data never cached).
  runtimeCaching: require('./pwa-caching.js').runtimeCaching,
});

// One build id, baked into both the server and the client (H3, 2026-09-27).
// Open tabs compare the id their bundle was built with against the one the
// server answers at /build-id (app/build-id/route.ts) and reload onto a new
// deploy (lib/buildReload.ts) — the /screen tab in the owner's Chrome and the
// hub's kiosk used to run the old bundle until someone reloaded by hand. It is the
// commit plus the build time, so a rebuild of a dirty tree is a new build
// too. Kept in the environment so every process of one `next build` sees the
// same value; a deploy may hand one in as EDWARD_BUILD_ID. `env` inlines it
// as NEXT_PUBLIC_BUILD_ID into client and server code alike, and
// generateBuildId makes .next/BUILD_ID the same value.
function computeBuildId() {
  let sha = 'nogit';
  try {
    sha =
      require('child_process')
        .execSync('git rev-parse --short=12 HEAD', { cwd: __dirname, stdio: ['ignore', 'pipe', 'ignore'] })
        .toString()
        .trim() || sha;
  } catch {
    /* not a git checkout: the time alone still tells builds apart */
  }
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, '');
  return `${sha}-${stamp}`;
}
const BUILD_ID = process.env.EDWARD_BUILD_ID || (process.env.EDWARD_BUILD_ID = computeBuildId());

/** @type {import('next').NextConfig} */
const nextConfig = {
  generateBuildId: async () => BUILD_ID,
  env: { NEXT_PUBLIC_BUILD_ID: BUILD_ID },
  // The UX harness builds into its own directory (NEXT_DIST_DIR=.next-harness)
  // so a dev server can never overwrite the .next that `next start` serves.
  ...(process.env.NEXT_DIST_DIR ? { distDir: process.env.NEXT_DIST_DIR } : {}),
  // Served with `next start` (see run-launchd.sh / ensure-build.sh), so no
  // standalone output — `next start` refuses to run one.
  // Proxy /api/* to the backend in development (in production, nginx/Caddy handles this)
  async rewrites() {
    const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
    return [
      {
        source: '/api/:path*',
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

module.exports = withPWA(nextConfig);
