const withPWA = require('next-pwa')({
  dest: 'public',
  register: true,
  skipWaiting: true,
  // Enable in development for testing (set to true to disable in dev)
  disable: false,
  // Custom service worker for push notifications
  sw: 'sw.js',
  // Include our push handler in the service worker
  importScripts: ['/sw-push.js'],
  runtimeCaching: [
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
      // Auth status — serve stale, revalidate in background
      urlPattern: /\/api\/auth\/status$/,
      handler: 'StaleWhileRevalidate',
      options: {
        cacheName: 'auth-status',
        expiration: {
          maxEntries: 1,
          maxAgeSeconds: 60 * 60, // 1 hour
        },
      },
    },
    {
      // SSE chat endpoint should not be cached
      urlPattern: /\/api\/chat$/,
      handler: 'NetworkOnly',
    },
  ],
});

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
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
