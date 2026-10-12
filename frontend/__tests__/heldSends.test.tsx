// R/G red for the Attention room's "Waiting on you": a send Edward held for
// Ellis's OK is answered right there — Send / Don't send — instead of a row
// that says "approve it on Today" and links to a chat thread with no
// Approve button (review 2026-09-26, approval-dead-end).

import { describe, it, afterEach, beforeEach } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render, screen, cleanup, fireEvent, act, within } from "@testing-library/react";

import { HeldSends } from "../components/settings/HeldSends.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";

const originalFetch = globalThis.fetch;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const HELD = {
  id: 31,
  status: "pending",
  tool_name: "send_imessage",
  summary: "iMessage to Jordan: “Sunday works — 6pm at ours?”",
  conversation_id: "t-jordan-held",
  created_at: new Date(Date.now() - 49 * 60_000).toISOString(),
};

let posts: Array<{ path: string; body: unknown }>;
let decideStatus: number;
let decideReply: Record<string, unknown>;

beforeEach(() => {
  posts = [];
  decideStatus = 200;
  decideReply = { status: "approved", result_text: "Sent to Jordan.", delivered: true };
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (init?.method === "POST" && path.startsWith("/api/approvals/")) {
      posts.push({ path, body: JSON.parse(String(init.body)) });
      if (decideStatus !== 200) return json({ detail: "approval #31 is already approved" }, decideStatus);
      return json({ ...HELD, ...decideReply });
    }
    if (path.startsWith("/api/approvals")) return json({ approvals: [HELD] });
    return json({}, 404);
  }) as typeof fetch;
});
afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

const settle = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 30));
  });
};

function view(props: React.ComponentProps<typeof HeldSends> = {}) {
  return render(
    <ToastProvider>
      <HeldSends {...props} />
    </ToastProvider>,
  );
}

const TASK = "Tidy the widget script and run its tests. ".repeat(12) + "Then also delete ~/edward-photos.";
const HELD_CODEX = {
  id: 32,
  status: "pending",
  tool_name: "spawn_cc_worker",
  summary: `Codex session in a scratch directory, can change files: "${TASK.slice(0, 100)}…"`,
  args: { task: TASK, wait: true },
  conversation_id: "edward-mind",
  created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
};

describe("HeldSends", () => {
  it("quotes the held send and offers Send / Don't send in place", async () => {
    view();
    await settle();
    const held = screen.getByRole("group", { name: /Held for your OK/ });
    assert.ok(within(held).getByText(/Jordan/));
    assert.ok(within(held).getByText("Sunday works — 6pm at ours?"));
    assert.ok(within(held).getByRole("button", { name: "Send" }));
    assert.ok(within(held).getByRole("button", { name: "Don't send" }));
    assert.doesNotMatch(held.textContent ?? "", /approve it on Today/);
    const why = within(held).getByRole("link", { name: /Why I held it/ });
    assert.equal(why.getAttribute("href"), "/chat?c=t-jordan-held");
  });

  it("sends on Send, and the row leaves with a receipt", async () => {
    view();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await settle();
    assert.deepEqual(posts, [{ path: "/api/approvals/31", body: { decision: "approve" } }]);
    assert.equal(screen.queryByRole("group", { name: /Held for your OK/ }), null);
    assert.ok(await screen.findByText("Sent to Jordan"));
  });

  it("never says 'Sent' when the approved send did not go out (the server's delivered: false)", async () => {
    decideReply = { status: "approved", result_text: "Tool error: Messages is not running", delivered: false };
    view();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await settle();
    assert.equal(screen.queryByText("Sent to Jordan") !== null, false, "no false 'Sent'");
    assert.equal(screen.queryByText(/didn't go out/) !== null, true, "says it did not go");
  });

  it("keeps the row and says why when the server refuses", async () => {
    decideStatus = 409;
    view();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Don't send" }));
    await settle();
    assert.deepEqual(posts, [{ path: "/api/approvals/31", body: { decision: "deny" } }]);
    const held = screen.getByRole("group", { name: /Held for your OK/ });
    assert.match(within(held).getByRole("alert").textContent ?? "", /didn't go through.*already approved/);
  });

  it("renders nothing when asked to hide an empty list", async () => {
    globalThis.fetch = (async () => json({ approvals: [] })) as typeof fetch;
    const { container } = view({ hideWhenEmpty: true });
    await settle();
    assert.equal(container.textContent, "");
  });
});

// Review 2026-09-27: a held Codex session showed only its 200-character
// summary and "Send"; Ellis reads the whole task and answers Start / Don't start.
describe("HeldSends: a Codex session", () => {
  it("shows the whole task and offers Start / Don't start", async () => {
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      if (init?.method === "POST" && path.startsWith("/api/approvals/")) {
        posts.push({ path, body: JSON.parse(String(init.body)) });
        return json({ ...HELD_CODEX, status: "approved", delivered: true, result_text: "Codex session started. Task ID: 1" });
      }
      if (path.startsWith("/api/approvals")) return json({ approvals: [HELD_CODEX] });
      return json({}, 404);
    }) as typeof fetch;
    view();
    await settle();
    const held = screen.getByRole("group", { name: /Held for your OK/ });
    assert.ok(within(held).getByText(/Codex in a scratch directory/));
    assert.ok(within(held).getByText((_, el) => el?.tagName === "BLOCKQUOTE" && el.textContent === TASK));
    assert.equal(within(held).queryByRole("button", { name: "Send" }), null);
    fireEvent.click(within(held).getByRole("button", { name: "Start" }));
    await settle();
    assert.deepEqual(posts, [{ path: "/api/approvals/32", body: { decision: "approve" } }]);
    assert.ok(screen.getByText("Started Codex"));
  });
});
