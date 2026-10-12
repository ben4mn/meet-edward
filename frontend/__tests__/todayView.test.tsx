// Today's view layer, smoke-tested (UX pass 2026-09-26): a held send quotes
// who and what and asks in plain words; a contradiction is answered in
// place; a failed first load is one calm card in Edward's voice; Done
// takes a card away at once and leaves a receipt.
//
// DOM nodes are never handed to node:assert (inspecting a jsdom node on
// failure kills the run); compare booleans and strings.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { DidSection } from "../components/today/DidSection.tsx";
import { NeedCard } from "../components/today/NeedCard.tsx";
import { TodayView } from "../components/today/TodayView.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";
import type { TodayAction, TodayNeed } from "../components/today/todayModel.ts";

const present = (el: Element | null) => el !== null;
const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };
const now = new Date("2026-09-26T23:30:00Z");
const tz = "America/Chicago";

const approval: TodayNeed = {
  id: "approval:31",
  kind: "approval",
  tone: "warn",
  priority: 0.95,
  title: "Approve Edward's send?",
  detail: 'send_imessage to Jordan: "Sunday works — 6pm at ours?"',
  href: "/chat?c=t-jordan-held",
  at: "2026-09-26T22:41:00Z",
  actions: [
    { id: "approve", label: "Approve", method: "POST", url: "/api/approvals/31", body: { decision: "approve" }, primary: true },
    { id: "deny", label: "Deny", method: "POST", url: "/api/approvals/31", body: { decision: "deny" }, primary: false },
    { id: "open", label: "Open", method: "GET", url: "/chat?c=t-jordan-held", primary: false },
  ],
};

const question: TodayNeed = {
  id: "note:n-flag-1",
  kind: "note",
  tone: "edward",
  priority: 0.6,
  title: "Which is right?",
  detail: "100mg twice a day, or 300mg in the evening?",
  href: "/settings/dreaming?flag=fl-1",
  count: 2,
  note_ids: ["n-flag-1", "n-flag-2"],
  choice: {
    flag_id: "fl-1",
    question: "100mg twice a day, or 300mg in the evening?",
    options: [
      { id: "a", label: "Keep this", text: "Biscuit takes 100mg twice a day.", action_id: "keep_a" },
      { id: "b", label: "Keep this", text: "Biscuit takes 300mg in the evening.", action_id: "keep_b" },
    ],
  },
  actions: [
    { id: "open", label: "Open", method: "GET", url: "/settings/dreaming?flag=fl-1", primary: false },
    { id: "keep_a", label: "First is right", method: "POST", url: "/api/consolidation/flags/fl-1/resolve", body: { keep: "a" }, primary: false },
    { id: "keep_b", label: "Second is right", method: "POST", url: "/api/consolidation/flags/fl-1/resolve", body: { keep: "b" }, primary: false },
    { id: "done", label: "Done", method: "POST", url: "/api/notes/n-flag-1/dismiss", body: { also: ["n-flag-2"] }, primary: false },
  ],
};

function card(need: TodayNeed, onAction: (n: TodayNeed, a: TodayAction) => void = () => {}) {
  return render(
    <AppRouterContext.Provider value={router as never}>
      <NeedCard need={need} busyAction={null} error={null} onAction={onAction} now={now} tz={tz} />
    </AppRouterContext.Provider>,
  );
}

afterEach(cleanup);

describe("NeedCard", () => {
  it("a held send asks in plain words and quotes who and what", () => {
    card(approval);
    assert.equal(present(screen.queryByText("Send this to Jordan?")), true);
    assert.equal(present(screen.queryByText("Sunday works — 6pm at ours?")), true);
    assert.equal(present(screen.queryByRole("button", { name: "Send" })), true);
    assert.equal(present(screen.queryByRole("button", { name: "Don't send" })), true);
    // The whole card opens the thread; no separate "Open" repeats it.
    assert.equal(present(screen.queryByRole("link", { name: "Open" })), false);
    assert.equal(screen.getByRole("link", { name: "Send this to Jordan?" }).getAttribute("href"), "/chat?c=t-jordan-held");
    assert.equal(present(screen.queryByText(/send_imessage/)), false, "no tool names");
  });

  it("a contradiction is answered by tapping the memory that is right", () => {
    const picked: string[] = [];
    card(question, (_n, a) => picked.push(a.id));
    assert.equal(present(screen.queryByText("2 notes · 49m ago")), false, "no time: the note has no `at`");
    assert.equal(present(screen.queryByText("2 notes")), true);
    fireEvent.click(screen.getByRole("button", { name: /Biscuit takes 100mg twice a day/ }));
    fireEvent.click(screen.getByRole("button", { name: /Biscuit takes 300mg in the evening/ }));
    fireEvent.click(screen.getByRole("button", { name: "Both fine" }));
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    assert.deepEqual(picked, ["keep_a", "keep_b", "both", "done"]);
    assert.equal(present(screen.queryByRole("button", { name: "First is right" })), false, "the choice is the quotes, not two more buttons");
  });

  it("each memory is labelled the same, since each is its own answer (review)", () => {
    card(question);
    const picks = screen.getAllByRole("button").filter((b) => /Biscuit takes/.test(b.textContent ?? ""));
    assert.deepEqual(picks.map((b) => (b.textContent ?? "").endsWith("This one")), [true, true]);
    assert.equal(present(screen.queryByText("That one")), false);
  });

  it("an email names its sender and keeps the address in the detail (review)", () => {
    card({
      id: "heartbeat:hb-8847",
      kind: "email",
      tone: "warn",
      priority: 0.9,
      title: "frontdesk@patel-vet.example.com emailed you: Can Biscuit come in Thursday at 9?",
      detail: "",
      href: "/settings/heartbeat",
      actions: [],
    });
    assert.equal(present(screen.queryByRole("link", { name: "Patel Vet emailed you: Can Biscuit come in Thursday at 9?" })), true);
    assert.equal(present(screen.queryByText("frontdesk@patel-vet.example.com")), true);
  });

  it("a held send quotes the backend's full text when it sends one (review)", () => {
    card({ ...approval, detail: 'send_imessage to Jordan: "Sunday works — 6pm…"', held: { channel: "imessage", recipient: "Jordan", text: "Sunday works — 6pm at ours? Bring the dog." } });
    assert.equal(present(screen.queryByText("Sunday works — 6pm at ours? Bring the dog.")), true);
  });
});

// ------------------------------------------------------------------ the page

const originalFetch = globalThis.fetch;
const calls: string[] = [];
let todayOk = true;
let feed: Record<string, unknown>;
let greeting = "Evening, Ellis.";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  calls.length = 0;
  todayOk = true;
  greeting = "Evening, Ellis.";
  try {
    window.sessionStorage.clear();
  } catch {
    /* none */
  }
  feed = {
    generated_at: "2026-09-26T23:30:00Z",
    needs: [
      {
        id: "heartbeat:hb-1",
        kind: "heartbeat",
        tone: "warn",
        priority: 0.9,
        title: "Mom needs a reply",
        detail: "3 messages · chili",
        href: "/chat?c=hb-family",
        actions: [
          { id: "open", label: "Open thread", method: "GET", url: "/chat?c=hb-family", primary: false },
          { id: "done", label: "Done", method: "POST", url: "/api/today/heartbeat/hb-1/done", primary: true },
        ],
      },
    ],
    overflow: { note: 0, heartbeat: 0 },
    did: [],
    conversations: [],
    thoughts_today: 0,
    status: {},
    presence: null,
  };
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push(`${method} ${url.replace(/^https?:\/\/[^/]+/, "")}`);
    if (url.includes("/api/today/heartbeat/")) return json({ ok: true, done: 1 });
    if (url.endsWith("/api/today")) return todayOk ? json(feed) : json({ detail: "down" }, 503);
    if (url.includes("/api/greeting/stream")) {
      return new Response(`data: ${JSON.stringify({ chunk: greeting })}\n\ndata: [DONE]\n\n`, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    }
    if (url.includes("/api/greeting")) return json({ greeting });
    return json({});
  }) as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function page(props: React.ComponentProps<typeof TodayView> = {}) {
  return render(
    <AppRouterContext.Provider value={router as never}>
      <ToastProvider>
        <TodayView {...props} />
      </ToastProvider>
    </AppRouterContext.Provider>,
  );
}

const doseNeed = {
  id: "dose:biscuit-carprofen-pm",
  kind: "dose",
  tone: "warn",
  priority: 1,
  title: "Biscuit · Carprofen 100mg",
  detail: "Due 18:00, 42 min late.",
  href: "/hub/pets",
  actions: [{ id: "log", label: "Log given", method: "POST", url: "/api/hub/care/biscuit-carprofen-pm/log", body: { given_by: "Ellis", status: "given" }, primary: true }],
};
const acted = () => calls.filter((c) => /^(POST|DELETE|PATCH) \/api\/(hub|approvals|today|notes)/.test(c));

const settle = (ms = 40) => act(async () => new Promise((r) => setTimeout(r, ms)));

describe("TodayView", () => {
  it("a failed first load is one calm card in Edward's voice, with Retry", async () => {
    todayOk = false;
    page();
    await screen.findByText(/I can't reach the Mac right now — trying again/);
    assert.equal(present(screen.queryByRole("button", { name: "Retry" })), true);
    // Retry succeeds: the feed replaces the card.
    todayOk = true;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Mom needs a reply");
    assert.equal(present(screen.queryByText(/can't reach the Mac/)), false);
  });

  it("Done takes the card away at once, leaves a receipt with Undo, and posts once when the Undo window closes", async () => {
    page({ undoWindowMs: 400 });
    await screen.findByText("Mom needs a reply");
    await settle(40);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await settle(20);
    assert.equal(present(screen.queryByText("Mom needs a reply", { selector: "article *" })), false, "the card leaves at once");
    assert.equal(present(screen.queryByText("Mom needs a reply", { selector: "[data-toast-viewport] *" })), true, "the receipt names what was done");
    assert.equal(present(screen.queryByRole("button", { name: "Undo" })), true, "Done can be taken back (review)");
    assert.equal(calls.filter((c) => c === "POST /api/today/heartbeat/hb-1/done").length, 0, "nothing is sent while Undo stands");
    await settle(800);
    assert.equal(calls.filter((c) => c === "POST /api/today/heartbeat/hb-1/done").length, 1);
  });

  it("Undo on a Done brings the card back and sends nothing (review)", async () => {
    page({ undoWindowMs: 400 });
    await screen.findByText("Mom needs a reply");
    await settle(40);
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await settle(20);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await settle(800);
    assert.equal(present(screen.queryByText("Mom needs a reply", { selector: "article *" })), true, "the card is back");
    assert.deepEqual(acted(), []);
  });

  it("a double tap on Log given logs one dose, and the second tap cannot land on the card sliding up (review)", async () => {
    const probiotic = {
      ...doseNeed,
      id: "dose:biscuit-probiotic-pm",
      title: "Biscuit · Probiotic",
      actions: [{ ...doseNeed.actions[0], url: "/api/hub/care/biscuit-probiotic-pm/log" }],
    };
    feed.needs = [{ ...doseNeed }, probiotic];
    page();
    await screen.findByText("Biscuit · Carprofen 100mg");
    const [first, second] = screen.getAllByRole("button", { name: "Log given" });
    fireEvent.click(first);
    fireEvent.click(first); // the exiting card
    fireEvent.click(second); // the card that slid up under the finger
    await settle(60);
    assert.deepEqual(acted(), ["POST /api/hub/care/biscuit-carprofen-pm/log"]);
    // Once the list has settled, the next card takes taps again.
    await settle(500);
    fireEvent.click(screen.getByRole("button", { name: "Log given" }));
    await settle(60);
    assert.deepEqual(acted(), ["POST /api/hub/care/biscuit-carprofen-pm/log", "POST /api/hub/care/biscuit-probiotic-pm/log"]);
  });

  it("an action on a feed painted from the cache waits for a fresh one, and never acts on what has gone (review)", async () => {
    // Last night's feed is in the tab's memory…
    feed.needs = [{ ...doseNeed }];
    page();
    await screen.findByText("Biscuit · Carprofen 100mg");
    cleanup();
    // …this morning the network is slow, and the dose is no longer due.
    let answer!: () => void;
    const base = globalThis.fetch;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/api/today")) {
        calls.push("GET /api/today");
        await new Promise<void>((r) => (answer = r));
        return json({ ...feed, needs: [] });
      }
      return base(input, init);
    }) as typeof fetch;
    page();
    assert.equal(present(screen.queryByText("Biscuit · Carprofen 100mg")), true, "the cached feed paints");
    fireEvent.click(screen.getByRole("button", { name: "Log given" }));
    await settle(20);
    assert.deepEqual(acted(), [], "nothing is logged before the network answers");
    await act(async () => answer());
    await settle(40);
    assert.deepEqual(acted(), [], "the dose had gone: nothing is logged");
    assert.equal(present(screen.queryByText("That changed — here's the latest")), true);
  });

  it("keys act only on the card that has focus now, and never answer a held send (review)", async () => {
    feed.needs = [
      {
        id: "dose:biscuit-carprofen-pm",
        kind: "dose",
        tone: "warn",
        priority: 1,
        title: "Biscuit · Carprofen 100mg",
        detail: "Due 18:00, 42 min late.",
        href: "/hub/pets",
        actions: [{ id: "log", label: "Log given", method: "POST", url: "/api/hub/care/biscuit-carprofen-pm/log", body: { given_by: "Ellis", status: "given" }, primary: true }],
      },
      { ...approval },
    ];
    page();
    await screen.findByText("Biscuit · Carprofen 100mg");
    await settle();
    fireEvent.keyDown(window, { key: "j" });
    assert.equal(document.activeElement?.textContent, "Biscuit · Carprofen 100mg");
    // Ellis clicks away: focus is on the page, not the card.
    (document.activeElement as HTMLElement).blur();
    fireEvent.keyDown(window, { key: "e" });
    fireEvent.keyDown(window, { key: "x" });
    await settle();
    const acted = () => calls.filter((c) => /^(POST|DELETE|PATCH) \/api\/(hub|approvals|today|notes)/.test(c));
    assert.deepEqual(acted(), [], "a stray key does nothing");
    // The held send, focused: e does not send, x does not deny.
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "j" });
    assert.equal(document.activeElement?.textContent, "Send this to Jordan?");
    fireEvent.keyDown(window, { key: "e" });
    fireEvent.keyDown(window, { key: "x" });
    await settle();
    assert.deepEqual(acted(), [], "no key answers a held send");
  });

  it("a held send that failed never toasts 'Sent' (review)", async () => {
    feed.needs = [{ ...approval }];
    const base = globalThis.fetch;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/api/approvals/31") && init?.method === "POST") {
        calls.push("POST /api/approvals/31");
        return json({ id: 31, status: "approved", delivered: false, result_text: "Failed to send iMessage: timeout" });
      }
      return base(input, init);
    }) as typeof fetch;
    page();
    await screen.findByText("Send this to Jordan?");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await settle(60);
    assert.equal(calls.filter((c) => c === "POST /api/approvals/31").length, 1);
    assert.equal(present(screen.queryByText("Sent to Jordan")), false, "never 'Sent' for a failed send");
    assert.equal(present(screen.queryByText("Couldn't send to Jordan")), true);
    assert.equal(present(screen.queryByText("Failed to send iMessage: timeout")), true);
    assert.equal(present(screen.queryByRole("button", { name: "Open" })), true, "one tap to the thread");
  });

  it("the greeting stops claiming what Ellis has just done, and asks for a fresh line (review)", async () => {
    greeting = "Evening, Ellis. Mom needs a reply about the chili.";
    page({ undoWindowMs: 400 });
    await screen.findByText("Mom needs a reply");
    await settle(40);
    const line = () => document.querySelector("[data-greeting]")?.textContent ?? "";
    assert.equal(line(), "Evening, Ellis. Mom needs a reply about the chili.");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await settle(60);
    assert.equal(line(), "Evening, Ellis.", "the clause about Mom goes with her card");
    await settle(1200);
    assert.equal(calls.includes("GET /api/greeting/stream?fresh=1"), true, "a fresh line is asked for");
    assert.equal(line(), "Evening, Ellis.", "even when the server answers from its cache");
  });

  it("the greeting is announced once, when it has finished — not word by word (review)", async () => {
    greeting = "Evening, Ellis. Quiet night.";
    page();
    await screen.findByText("Mom needs a reply");
    await settle(40);
    const visible = document.querySelector("[data-greeting]");
    assert.equal(visible?.getAttribute("aria-live"), null, "the streamed line is not a live region");
    const live = document.querySelector("[data-greeting-live]");
    assert.equal(live?.getAttribute("aria-live"), "polite");
    assert.equal(live?.textContent, "Evening, Ellis. Quiet night.");
  });

  it("paints the last feed at once on return (the cache), before the network answers", async () => {
    page();
    await screen.findByText("Mom needs a reply");
    cleanup();
    // The network now hangs; the cached feed must still paint.
    globalThis.fetch = (() => new Promise(() => {})) as unknown as typeof fetch;
    page();
    assert.equal(present(screen.queryByText("Mom needs a reply")), true);
  });
});

// Review 2026-09-26 (weak-focus-indicators): a focused "What I did" row
// shows the design system's ring, not a 1.07:1 background tint.
describe("DidSection", () => {
  it("every row, link or expander, shows the focus ring", () => {
    render(
      <AppRouterContext.Provider value={router as never}>
        <DidSection
          did={[
            { id: "a", kind: "action", title: "Replied to Parker", detail: "", at: "2026-09-26T22:00:00Z", href: "/chat?c=1" },
            { id: "t", kind: "triage", title: "Read 47 messages", detail: "noted 6", at: "2026-09-26T21:00:00Z", href: "/x" },
          ]}
          now={now}
          tz={tz}
        />
      </AppRouterContext.Provider>,
    );
    const rows = [screen.getByRole("link", { name: /Replied to Parker/ }), screen.getByRole("button", { name: /Read 47 messages/ })];
    for (const r of rows) {
      const cls = r.getAttribute("class") ?? "";
      assert.equal(/focus-visible:ring-2/.test(cls) && /focus-visible:ring-terminal\/60/.test(cls), true, cls);
    }
  });
});

