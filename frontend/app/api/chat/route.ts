/**
 * Streaming passthrough for /api/chat → backend.
 *
 * Next.js rewrites() buffer the entire response before forwarding, which breaks
 * SSE streaming (the browser never receives events until the connection closes).
 * This App Router route handler pipes the backend stream directly to the client
 * without buffering, preserving real-time SSE delivery through Ngrok.
 */

import { NextRequest } from "next/server";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function POST(req: NextRequest) {
  const upstream = await fetch(`${BACKEND_URL}/api/chat`, {
    method: "POST",
    headers: req.headers,
    body: req.body,
    // Required for streaming request bodies in Node.js fetch
    // @ts-ignore — duplex is valid but not yet in TypeScript types
    duplex: "half",
  });

  return new Response(upstream.body, {
    status: upstream.status,
    headers: upstream.headers,
  });
}
