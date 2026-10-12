// Shared by the Memory / House panel tests (not a test file itself):
// a routed fetch mock that records calls, and the jsdom shims the Sheet
// needs (matchMedia).

export interface Call {
  url: string;
  method: string;
  body: unknown;
}

export type Route = (url: string, method: string, body: unknown) => unknown | Response | undefined;

if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    }),
  });
}

const originalFetch = globalThis.fetch;

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Install a fetch that answers with the first route returning non-undefined. */
export function mockFetch(route: Route): Call[] {
  const calls: Call[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    let body: unknown = null;
    if (init?.body && typeof init.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    calls.push({ url, method, body });
    const out = route(url, method, body);
    if (out instanceof Response) return out;
    if (out === undefined) return json({ detail: "not mocked" }, 404);
    return json(out);
  }) as typeof fetch;
  return calls;
}

export function restoreFetch(): void {
  globalThis.fetch = originalFetch;
}
