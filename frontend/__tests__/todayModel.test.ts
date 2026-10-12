// R/G red for the Today page's pure bits.
//
// The backend composes the feed and ships each need with its actions
// (method/url/body). The page only has to decide how to lay the actions
// out and what to do with the feed after one succeeds — both pinned here
// so the React shell stays thin.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  needHref,
  partitionActions,
  afterAction,
  describeThoughts,
  type TodayNeed,
} from "../components/today/todayModel.ts";

const need = (id: string, actions: TodayNeed["actions"]): TodayNeed => ({
  id,
  kind: "open_loop",
  tone: "warn",
  priority: 0.8,
  title: "t",
  detail: "",
  href: "/chat?c=x",
  actions,
});

describe("partitionActions", () => {
  it("splits primary, secondary and plain links", () => {
    const acts = [
      { id: "open", label: "Open", method: "GET", url: "/chat?c=1", primary: false },
      { id: "snooze", label: "Snooze", method: "PATCH", url: "/api/x", body: { status: "snoozed" }, primary: false },
      { id: "done", label: "Done", method: "PATCH", url: "/api/x", body: { status: "resolved" }, primary: true },
    ];
    const p = partitionActions(acts);
    assert.deepEqual(p.primary?.id, "done");
    assert.deepEqual(p.secondary.map((a) => a.id), ["snooze"]);
    assert.deepEqual(p.links.map((a) => a.id), ["open"]);
  });
});

describe("afterAction", () => {
  const feed = [need("a", []), need("b", [])];
  it("removes the need once a mutating action succeeded", () => {
    assert.deepEqual(afterAction(feed, "a").map((n) => n.id), ["b"]);
  });
  it("leaves the feed alone for unknown ids", () => {
    assert.deepEqual(afterAction(feed, "zzz").map((n) => n.id), ["a", "b"]);
  });
});

describe("describeThoughts", () => {
  it("counts Edward's own conversations in his voice, and says nothing when there are none", () => {
    // UX pass 2026-09-26: the line shows only when > 0 and links to the Thoughts panel.
    assert.equal(describeThoughts(0), null);
    assert.equal(describeThoughts(1), "1 thought of my own today");
    assert.equal(describeThoughts(34), "34 thoughts of my own today");
  });
});

describe("needHref", () => {
  it("uses the need's own link", () => {
    assert.equal(needHref({ id: "x", kind: "heartbeat", href: "/chat?c=abc", actions: [] } as never), "/chat?c=abc");
  });
  it("falls back to the first GET action, then to Today — never undefined", () => {
    // 2026-09-22: a note need arrived without `href`; <Link href={undefined}>
    // throws in a production build and the PWA opened onto "Application error".
    assert.equal(
      needHref({ id: "x", kind: "note", actions: [{ id: "open", label: "Open", method: "GET", url: "/settings/thoughts", primary: false }] } as never),
      "/settings/thoughts",
    );
    assert.equal(needHref({ id: "x", kind: "note", actions: [] } as never), "/today");
    assert.equal(needHref({ id: "x", kind: "note" } as never), "/today");
  });
});

// Reading Edward's thinking (2026-09-25). "What I did" gains rows of kind
// "action": a wake of his mind that acted, linking to its thought entry.
// Those must read as his choices ("Edward did") and always open the entry;
// other digest rows keep their tap-to-expand.
import { didRowModel, type TodayDid } from "../components/today/todayModel.ts";

const did = (over: Partial<TodayDid> = {}): TodayDid => ({
  id: "d1",
  kind: "triage",
  title: "Looked at 4 messages",
  detail: "",
  at: "2026-09-25T10:00:00Z",
  href: "/settings/heartbeat",
  ...over,
});

describe("didRowModel", () => {
  it("an action links to its entry, says Edward did it and shows its detail", () => {
    const m = didRowModel(did({ kind: "action", title: "Sent Parker a note", detail: "running 10 min late", href: "/chat?c=t1" }));
    assert.deepEqual(m, { href: "/chat?c=t1", expandable: false, badge: "Edward did", inlineDetail: true });
  });

  it("an action without a link still reads as his (and never hands Link an empty href)", () => {
    assert.deepEqual(didRowModel(did({ kind: "action", detail: "why", href: "" })), {
      href: undefined,
      expandable: false,
      badge: "Edward did",
      inlineDetail: true,
    });
  });

  it("other rows keep tap-to-expand when they have a detail, else link", () => {
    assert.deepEqual(didRowModel(did({ detail: "2 escalated" })), { href: undefined, expandable: true, badge: null, inlineDetail: false });
    assert.deepEqual(didRowModel(did()), { href: "/settings/heartbeat", expandable: false, badge: null, inlineDetail: false });
  });
});

// ---------------------------------------------------------------------------
// UX pass 2026-09-26: Today as a briefing. The backend's /api/today changed
// in Phase A (counts, note ids, an inline choice, approvals, overflow,
// presence, did_since); these pin what the page does with it.
// ---------------------------------------------------------------------------
import {
  NEEDS_CAP,
  actionLabel,
  approvalFor,
  approvalPreview,
  bothFineAction,
  bucketDid,
  cardActions,
  didTitle,
  dismissAction,
  isInbound,
  needCountLabel,
  needDetail,
  needTitle,
  needVisual,
  normalizeTodayFeed,
  overflowLabel,
  receiptFor,
  splitDid,
  undoFor,
  visibleNeeds,
  type TodayAction,
} from "../components/today/todayModel.ts";

const A = (over: Partial<TodayAction> & { id: string }): TodayAction => ({
  label: over.id,
  method: "POST",
  url: `/api/x/${over.id}`,
  primary: false,
  ...over,
});

const approval: TodayNeed = {
  id: "approval:31",
  kind: "approval",
  tone: "warn",
  priority: 0.95,
  title: "Approve Edward's send?",
  detail: "send_imessage to Jordan: \"Sunday works — 6pm at ours?\"",
  href: "/chat?c=t-jordan-held",
  actions: [
    A({ id: "approve", label: "Approve", url: "/api/approvals/31", body: { decision: "approve" }, primary: true }),
    A({ id: "deny", label: "Deny", url: "/api/approvals/31", body: { decision: "deny" } }),
    A({ id: "open", label: "Open", method: "GET", url: "/chat?c=t-jordan-held" }),
  ],
};

const dose: TodayNeed = {
  id: "dose:biscuit-carprofen",
  kind: "dose",
  tone: "warn",
  priority: 1,
  title: "Biscuit · Carprofen 100mg",
  detail: "Due 18:00, 42 min late.",
  href: "/hub/pets",
  actions: [
    A({ id: "log", label: "Log given", url: "/api/hub/care/biscuit-carprofen/log", body: { given_by: "Ellis", status: "given" }, primary: true }),
    A({ id: "board", label: "Open board", method: "GET", url: "/hub/pets" }),
  ],
};

const flagNote: TodayNeed = {
  id: "note:n-flag-1",
  kind: "note",
  tone: "edward",
  priority: 0.6,
  title: "Which is right?",
  detail: "Is it 100mg twice a day, or 300mg in the evening?",
  href: "/settings/dreaming?flag=fl-1",
  count: 2,
  note_ids: ["n-flag-1", "n-flag-2"],
  choice: {
    flag_id: "fl-1",
    question: "Is it 100mg twice a day, or 300mg in the evening?",
    options: [
      { id: "a", label: "Keep this", text: "Biscuit takes Carprofen 100mg twice a day.", action_id: "keep_a" },
      { id: "b", label: "Keep this", text: "Biscuit takes Carprofen 300mg in the evening.", action_id: "keep_b" },
    ],
  },
  actions: [
    A({ id: "open", label: "Open", method: "GET", url: "/settings/dreaming?flag=fl-1" }),
    A({ id: "keep_a", label: "First is right", url: "/api/consolidation/flags/fl-1/resolve", body: { keep: "a" } }),
    A({ id: "keep_b", label: "Second is right", url: "/api/consolidation/flags/fl-1/resolve", body: { keep: "b" } }),
    A({ id: "done", label: "Done", url: "/api/notes/n-flag-1/dismiss", body: { also: ["n-flag-2"] } }),
  ],
};

describe("normalizeTodayFeed", () => {
  it("fills what an older backend or a cached feed leaves out", () => {
    const f = normalizeTodayFeed({ generated_at: "2026-09-26T12:00:00Z", needs: [{ id: "x", kind: "note", title: "t" }] });
    assert.deepEqual(f.did, []);
    assert.deepEqual(f.conversations, []);
    assert.equal("goals" in f, false, "goals and ideas left Today with unit G (2026-10-11)");
    assert.equal("ideas" in f, false);
    assert.deepEqual(f.overflow, { note: 0, heartbeat: 0 });
    assert.equal(f.presence, null);
    assert.equal(f.thoughts_today, 0);
    assert.deepEqual(f.needs[0].actions, [], "a need always has an action list");
    assert.equal(f.needs[0].detail, "");
  });
  it("never throws on junk", () => {
    assert.deepEqual(normalizeTodayFeed(null).needs, []);
    assert.deepEqual(normalizeTodayFeed("nope").needs, []);
  });
});

describe("needVisual", () => {
  it("fixes the kind icons: approvals are a shield, doses a paw", () => {
    assert.equal(needVisual(approval).icon, "shield");
    assert.equal(needVisual(dose).icon, "paw");
    assert.equal(needVisual({ ...dose, kind: "calendar" }).icon, "calendar");
    assert.equal(needVisual({ ...dose, kind: "heartbeat" }).icon, "message");
    assert.equal(needVisual({ ...dose, kind: "open_loop" }).icon, "loop");
    assert.equal(needVisual({ ...dose, kind: "event" }).icon, "clock");
    assert.equal(needVisual({ ...dose, kind: "worker" }).icon, "worker");
    assert.equal(needVisual({ ...flagNote, choice: undefined, href: "/chat?c=edward-mind" }).icon, "note");
    assert.equal(needVisual(flagNote).icon, "question");
    assert.equal(needVisual({ ...dose, kind: "brand-new" }).icon, "activity");
  });
  it("keeps the backend's tone, and an unknown one is neutral", () => {
    assert.equal(needVisual(dose).tone, "warn");
    assert.equal(needVisual({ ...dose, tone: "loud" as never }).tone, "neutral");
  });
});

describe("cardActions", () => {
  it("hides a link that only repeats where the whole card already goes", () => {
    const a = cardActions(dose);
    assert.equal(a.primary?.id, "log");
    assert.deepEqual(a.links, []);
    const other = cardActions({ ...dose, href: "/chat?c=1" });
    assert.deepEqual(other.links.map((l) => l.id), ["board"]);
  });
  it("leaves the choice's own actions to the choice and keeps Done as a quiet way out", () => {
    const a = cardActions(flagNote);
    assert.equal(a.primary, null);
    assert.deepEqual(a.secondary.map((x) => x.id), ["done"]);
  });
  it("approvals: send first, then don't send", () => {
    const a = cardActions(approval);
    assert.equal(a.primary?.id, "approve");
    assert.deepEqual(a.secondary.map((x) => x.id), ["deny"]);
  });
});

describe("actionLabel", () => {
  it("says what an approval does in plain words", () => {
    assert.equal(actionLabel(approval, approval.actions[0]), "Send");
    assert.equal(actionLabel(approval, approval.actions[1]), "Don't send");
  });
  it("Done beside a question is a skip", () => {
    assert.equal(actionLabel(flagNote, flagNote.actions[3]), "Skip");
  });
  it("otherwise the backend's label", () => {
    assert.equal(actionLabel(dose, dose.actions[0]), "Log given");
  });
});

describe("visibleNeeds", () => {
  const many = Array.from({ length: 9 }, (_, i) => ({ ...dose, id: `n${i}` }));
  it("caps what shows and counts the rest", () => {
    const v = visibleNeeds(many, new Set(), false);
    assert.equal(v.shown.length, NEEDS_CAP);
    assert.equal(v.more, 9 - NEEDS_CAP);
  });
  it("shows everything once expanded", () => {
    const v = visibleNeeds(many, new Set(), true);
    assert.equal(v.shown.length, 9);
    assert.equal(v.more, 0);
  });
  it("leaves out needs being resolved, so a refresh cannot bring one back mid-flight", () => {
    const v = visibleNeeds(many, new Set(["n0", "n1"]), false);
    assert.equal(v.shown[0].id, "n2");
    assert.equal(v.more, 7 - NEEDS_CAP);
  });
  it("never says +1 more: one past the cap just shows", () => {
    const six = many.slice(0, NEEDS_CAP + 1);
    assert.equal(visibleNeeds(six, new Set(), false).shown.length, NEEDS_CAP + 1);
    assert.equal(visibleNeeds(six, new Set(), false).more, 0);
  });
});

describe("overflowLabel / needCountLabel", () => {
  it("names what the backend held back", () => {
    assert.equal(overflowLabel({ note: 0, heartbeat: 0 }), null);
    assert.equal(overflowLabel(undefined), null);
    assert.equal(overflowLabel({ note: 1, heartbeat: 0 }), "1 more note");
    assert.equal(overflowLabel({ note: 3, heartbeat: 2 }), "3 more notes and 2 more threads");
    assert.equal(overflowLabel({ note: 0, heartbeat: 1 }), "1 more thread");
  });
  it("a collapsed note card says how many it stands for", () => {
    assert.equal(needCountLabel(flagNote), "2 notes");
    assert.equal(needCountLabel({ ...flagNote, count: 1 }), null);
    // A thread's count is already in its detail ("3 messages · …").
    assert.equal(needCountLabel({ ...dose, kind: "heartbeat", count: 3 }), null);
  });
});

describe("approvalPreview", () => {
  it("reads the recipient and the quoted text of a held send", () => {
    assert.deepEqual(approvalPreview('send_imessage to Parker: "On my way"'), {
      channel: "iMessage",
      recipient: "Parker",
      text: "On my way",
      truncated: false,
    });
    assert.deepEqual(approvalPreview("iMessage to Jordan: “Sunday works — 6pm at ours?”"), {
      channel: "iMessage",
      recipient: "Jordan",
      text: "Sunday works — 6pm at ours?",
      truncated: false,
    });
  });
  it("names each channel without tool names", () => {
    assert.equal(approvalPreview('send_sms to +15555550100: "hi"').channel, "Text");
    assert.equal(approvalPreview('whatsapp_send_message to Mom: "hi"').channel, "WhatsApp");
    assert.equal(approvalPreview('mail to a@b.c: "Invoice"').channel, "Email");
    assert.equal(approvalPreview('send_message to Dad: "hi"').channel, "Message");
  });
  it("notices the backend cut a long message short", () => {
    assert.equal(approvalPreview('send_imessage to Parker: "a very long line…"').truncated, true);
  });
  it("gives up quietly on anything else", () => {
    assert.deepEqual(approvalPreview("add_mcp_server weather (npx)"), { channel: null, recipient: null, text: null, truncated: false });
  });
});

// The backend now sends a held send's parts as fields (review 2026-09-26);
// the summary string stays the fallback for an older backend.
describe("approvalFor", () => {
  it("prefers the structured channel, recipient and full text", () => {
    const n = normalizeTodayFeed({
      needs: [{ ...approval, detail: 'send_imessage to Jordan: "Sunday works — 6pm…"', channel: "imessage", recipient: "Jordan R", text: "Sunday works — 6pm at ours? Bring the dog." }],
    }).needs[0];
    assert.deepEqual(approvalFor(n), { channel: "iMessage", recipient: "Jordan R", text: "Sunday works — 6pm at ours? Bring the dog.", truncated: false });
    assert.equal(needTitle(n), "Send this to Jordan R?");
  });
  it("reads the backend's `send` object (channel, recipient, the whole text, a mail's subject)", () => {
    const n = normalizeTodayFeed({
      needs: [{ ...approval, send: { channel: "email", recipient: "frontdesk@patel-vet.example.com", text: "Thursday at 9 works.", subject: "Biscuit" } }],
    }).needs[0];
    assert.deepEqual(approvalFor(n), { channel: "Email", recipient: "frontdesk@patel-vet.example.com", text: "Thursday at 9 works.", truncated: false, subject: "Biscuit" });
  });
  it("a held call that is not a message is not asked as a send", () => {
    const page = normalizeTodayFeed({
      needs: [{ ...approval, detail: "create_hosted_page trip-plan: 5120 characters of HTML", send: { channel: "page", recipient: "trip-plan", text: null } }],
    }).needs[0];
    assert.equal(approvalFor(page).recipient, null);
    assert.equal(needTitle(page), "Approve Edward's send?");
  });
  it("falls back to the summary line", () => {
    assert.deepEqual(approvalFor(approval), approvalPreview(approval.detail));
    const nested = normalizeTodayFeed({ needs: [{ ...approval, approval: { channel: "whatsapp", recipient: "Mom", text: "hi" } }] }).needs[0];
    assert.equal(approvalFor(nested).channel, "WhatsApp");
    assert.equal(approvalFor(nested).recipient, "Mom");
  });
});

// Review 2026-09-27: a Codex session Edward asked to start is held like a
// send, but it is not a message. The card showed ~150 characters of the
// call's JSON; Ellis has to read the whole task before he lets it run, and
// the card must not ask "Send this to a scratch directory?".
describe("a held Codex session", () => {
  const task = "Tidy the widget script and run its tests. ".repeat(12) + "Then also delete ~/edward-photos.";
  const codex = normalizeTodayFeed({
    needs: [{
      ...approval,
      title: "Let Edward start Codex?",
      detail: `Codex session in a scratch directory, can change files: "${task.slice(0, 100)}…"`,
      send: { channel: "codex", recipient: "a scratch directory", text: task },
    }],
  }).needs[0];
  it("shows the whole task and where it runs", () => {
    assert.deepEqual(approvalFor(codex), {
      channel: "Codex", recipient: "a scratch directory", text: task, truncated: false, connector: "in", kind: "codex",
    });
  });
  it("asks to start it, not to send it", () => {
    assert.equal(needTitle(codex), "Let Edward start Codex?");
  });
  it("says it started only when the server says so", () => {
    assert.deepEqual(
      receiptFor(codex, codex.actions[0], { status: "approved", delivered: true, result_text: "Codex session started. Task ID: 1234" }),
      { title: "Started Codex", tone: "ok" },
    );
    assert.deepEqual(
      receiptFor(codex, codex.actions[0], { status: "approved", delivered: false, result_text: "Tool error: Codex never runs in Edward's live code" }),
      { title: "Codex didn't start", body: "Codex never runs in Edward's live code", tone: "bad", open: "/chat?c=t-jordan-held" },
    );
    assert.deepEqual(receiptFor(codex, codex.actions[1]), { title: "Not started", tone: "neutral" });
  });
});

// Review 2026-09-26 (email-need-icon): mail escalated by the mind is its own
// kind, with a mail icon and a sender a person can scan.
describe("email needs", () => {
  const email: TodayNeed = {
    id: "heartbeat:hb-8847",
    kind: "email",
    tone: "warn",
    priority: 0.9,
    title: "frontdesk@patel-vet.example.com emailed you: Can Biscuit come in Thursday at 9?",
    detail: "",
    href: "/settings/heartbeat",
    actions: [],
  };
  it("gets the mail icon", () => {
    assert.equal(needVisual(email).icon, "mail");
  });
  it("names the sender, not the address; the address moves to the detail", () => {
    assert.equal(needTitle(email), "Patel Vet emailed you: Can Biscuit come in Thursday at 9?");
    assert.equal(needDetail(email), "frontdesk@patel-vet.example.com");
    // A mailbox provider names the person; only the provider label matters
    // (the reserved .example TLD keeps these from being anyone's address).
    assert.equal(needTitle({ ...email, title: "jane.doe@gmail.example emailed you: Lunch?" }), "Jane Doe emailed you: Lunch?");
    assert.equal(needTitle({ ...email, title: "orders@kibblebox.example.com emailed you" }), "Kibblebox emailed you");
    assert.equal(needTitle({ ...email, title: "no-reply@accounts.google.com emailed you: Security alert" }), "Google emailed you: Security alert");
  });
  it("leaves a named sender alone", () => {
    assert.equal(needTitle({ ...email, title: "Dr Patel emailed you: Thursday?" }), "Dr Patel emailed you: Thursday?");
    assert.equal(needDetail({ ...email, title: "Dr Patel emailed you: Thursday?", detail: "" }), "");
  });
});

describe("receiptFor", () => {
  it("an approved send says who it went to — only when the server says it was delivered", () => {
    assert.deepEqual(receiptFor(approval, approval.actions[0], { status: "approved", delivered: true, result_text: "iMessage sent to Jordan" }), {
      title: "Sent to Jordan",
      tone: "ok",
    });
    assert.deepEqual(receiptFor(approval, approval.actions[1]), { title: "Not sent", body: "Jordan won't get it.", tone: "neutral" });
  });
  // Review 2026-09-26 (approval-sent-receipt-lies): POST /api/approvals/{id}
  // is a 200 even when the stored send failed; "Sent to Jordan" then lied.
  it("a send that failed says so, with why, and opens the thread", () => {
    assert.deepEqual(
      receiptFor(approval, approval.actions[0], { status: "approved", delivered: false, result_text: "Failed to send iMessage: timeout\nmore" }),
      { title: "Couldn't send to Jordan", body: "Failed to send iMessage: timeout", tone: "bad", open: "/chat?c=t-jordan-held" },
    );
    // An older backend (no `delivered`): the result's own words decide.
    assert.deepEqual(
      receiptFor(approval, approval.actions[0], { status: "approved", result_text: "Tool error: blocked — the message contains what looks like a secret (jwt)" }),
      { title: "Couldn't send to Jordan", body: "Blocked — the message contains what looks like a secret (jwt)", tone: "bad", open: "/chat?c=t-jordan-held" },
    );
    assert.deepEqual(receiptFor(approval, approval.actions[0], { status: "approved", result_text: "iMessage sent to Jordan" }), {
      title: "Sent to Jordan",
      tone: "ok",
    });
  });
  it("never claims a send it cannot confirm", () => {
    for (const result of [undefined, null, {}, { status: "approved", result_text: "Could not resolve 'Jordan'" }]) {
      const r = receiptFor(approval, approval.actions[0], result);
      assert.notEqual(r.title, "Sent to Jordan", JSON.stringify(result));
      assert.equal(r.open, "/chat?c=t-jordan-held");
    }
    assert.deepEqual(receiptFor(approval, approval.actions[0]), {
      title: "I couldn't confirm it went",
      body: "Open the thread to check.",
      tone: "neutral",
      open: "/chat?c=t-jordan-held",
    });
  });
  it("a logged dose names the dose", () => {
    assert.deepEqual(receiptFor(dose, dose.actions[0]), { title: "Logged", body: "Biscuit · Carprofen 100mg", tone: "ok" });
  });
  it("an answered question says what he will remember", () => {
    assert.deepEqual(receiptFor(flagNote, flagNote.actions[1]), {
      title: "Got it",
      body: "I'll remember: Biscuit takes Carprofen 100mg twice a day.",
      tone: "edward",
    });
    assert.deepEqual(receiptFor(flagNote, bothFineAction(flagNote.choice!)), { title: "Got it", body: "Both stay.", tone: "edward" });
  });
  it("Done and Snooze", () => {
    assert.deepEqual(receiptFor(dose, A({ id: "done", label: "Done" })), { title: "Done", body: "Biscuit · Carprofen 100mg", tone: "neutral" });
    assert.deepEqual(receiptFor(dose, A({ id: "snooze", label: "Snooze", method: "PATCH" })), {
      title: "Snoozed until morning",
      body: "Biscuit · Carprofen 100mg",
      tone: "neutral",
    });
  });
});

describe("undoFor / dismissAction / bothFineAction", () => {
  it("a logged dose can be taken back (DELETE the same log)", () => {
    assert.deepEqual(undoFor(dose, dose.actions[0]), {
      id: "undo",
      label: "Undo",
      method: "DELETE",
      url: "/api/hub/care/biscuit-carprofen/log",
      primary: false,
    });
  });
  it("nothing else claims an undo it does not have", () => {
    assert.equal(undoFor(approval, approval.actions[0]), null);
    assert.equal(undoFor(flagNote, flagNote.actions[3]), null);
  });
  it("x dismisses with Done, never with a send or a deny", () => {
    assert.equal(dismissAction(flagNote)?.id, "done");
    assert.equal(dismissAction(approval), null);
    assert.equal(dismissAction(dose), null);
  });
  it("'Both fine' resolves the flag without retiring either memory", () => {
    assert.deepEqual(bothFineAction(flagNote.choice!), {
      id: "both",
      label: "Both fine",
      method: "PATCH",
      url: "/api/consolidation/flags/fl-1",
      body: { resolved: true },
      primary: false,
    });
  });
});

describe("What I did", () => {
  const tz = "America/Chicago";
  // 2026-09-26 19:30 in Springfield.
  const now = new Date("2026-09-27T00:30:00Z");
  const row = (id: string, at: string, over: Partial<TodayDid> = {}): TodayDid => ({
    id,
    kind: "note",
    title: id,
    detail: "",
    at,
    href: "/x",
    ...over,
  });

  it("rephrases the message count without internal words", () => {
    assert.equal(
      didTitle(row("t", now.toISOString(), { kind: "triage", title: "Triaged 47 messages, noted 6, escalated 4, you handled 3" })),
      "Read 47 messages · 4 for you",
    );
    assert.equal(
      didTitle(row("t", now.toISOString(), { kind: "triage", title: "Triaged 5 messages, noted 0, escalated 0" })),
      "Read 5 messages · nothing for you",
    );
    assert.equal(didTitle(row("x", now.toISOString(), { title: "Wished for: Strava" })), "Wished for: Strava");
  });

  it("takes the folded wakes row out of the list", () => {
    const s = splitDid([row("a", "2026-09-26T23:00:00Z"), row("wakes:recent", "2026-09-26T22:00:00Z", { kind: "wakes", count: 6 })]);
    assert.equal(s.wakes?.id, "wakes:recent");
    assert.deepEqual(s.rows.map((r) => r.id), ["a"]);
  });

  it("buckets by part of the day, newest first in each — his actions carry his colour, not a place in the queue (review)", () => {
    const b = bucketDid(
      [
        row("evening-note", "2026-09-26T23:40:00Z"), // 6:40 pm
        row("evening-action", "2026-09-26T22:10:00Z", { kind: "action" }), // 5:10 pm
        row("afternoon", "2026-09-26T19:00:00Z"), // 2 pm
        row("last-night", "2026-09-26T03:00:00Z"), // 10 pm yesterday
        row("yesterday", "2026-09-25T20:00:00Z"), // 3 pm yesterday
      ],
      now,
      tz,
    );
    assert.deepEqual(
      b.map((x) => [x.label, x.items.map((i) => i.id)]),
      [
        ["This evening", ["evening-note", "evening-action"]],
        ["Earlier today", ["afternoon"]],
        ["Last night", ["last-night"]],
        ["Yesterday", ["yesterday"]],
      ],
    );
  });

  it("names the current part of the day in the morning too", () => {
    const morning = new Date("2026-09-26T14:00:00Z"); // 9 am Springfield
    const b = bucketDid([row("a", "2026-09-26T13:00:00Z"), row("b", "2026-09-26T08:00:00Z")], morning, tz);
    assert.deepEqual(b.map((x) => x.label), ["This morning", "Last night"]);
  });

  it("after midnight the whole night so far is tonight", () => {
    const late = new Date("2026-09-27T06:00:00Z"); // 1 am Springfield
    const b = bucketDid(
      [row("half-past", "2026-09-27T05:30:00Z"), row("eleven", "2026-09-27T04:00:00Z"), row("afternoon", "2026-09-26T19:00:00Z")],
      late,
      tz,
    );
    assert.deepEqual(
      b.map((x) => [x.label, x.items.map((i) => i.id)]),
      [
        ["Tonight", ["half-past", "eleven"]],
        ["Yesterday", ["afternoon"]],
      ],
    );
  });
});

describe("isInbound", () => {
  it("only a conversation that came from outside gets a dot", () => {
    assert.equal(isInbound({ source: "external_message" }), true);
    assert.equal(isInbound({ source: "user" }), false);
  });
});

import { needMeta, needTitle } from "../components/today/todayModel.ts";

describe("needTitle", () => {
  it("an approval asks the question in plain words", () => {
    assert.equal(needTitle(approval), "Send this to Jordan?");
    assert.equal(needTitle({ ...approval, detail: "add_mcp_server weather (npx)" }), "Approve Edward's send?");
  });
  it("everything else keeps its title", () => {
    assert.equal(needTitle(dose), "Biscuit · Carprofen 100mg");
  });
});

describe("needMeta", () => {
  const tz = "America/Chicago";
  const now = new Date("2026-09-26T23:30:00Z"); // 6:30 pm
  it("upcoming things say when, past things how long ago", () => {
    assert.equal(needMeta({ ...dose, kind: "event", at: "2026-09-27T02:00:00Z" }, { now, tz }), "9:00 pm");
    assert.equal(needMeta({ ...approval, at: "2026-09-26T22:41:00Z" }, { now, tz }), "49m ago");
  });
  it("a dose or a calendar card already says when in its own words, so no meta; no time, no meta", () => {
    assert.equal(needMeta({ ...dose, kind: "calendar", at: "2026-09-26T23:12:00Z" }, { now, tz }), null);
    assert.equal(needMeta({ ...dose, at: "2026-09-26T23:00:00Z" }, { now, tz }), null);
    assert.equal(needMeta(approval, { now, tz }), null);
  });
});

import { didDetail } from "../components/today/todayModel.ts";

describe("What I did, in his words", () => {
  const d = (over: Partial<TodayDid>): TodayDid => ({ id: "x", kind: "action", title: "", detail: "", at: "2026-09-26T22:00:00Z", href: "/x", ...over });
  // The owner is Ellis (__tests__/setup.ts; the app learns the name from
  // /api/auth/status), so the model's "Remind Ellis …" is a reminder for him.
  it("a reminder he fired is one he gave Ellis", () => {
    assert.equal(didTitle(d({ kind: "event_fired", title: "Remind Ellis to water the plants (fern + fiddle leaf)" })), "Reminded you to water the plants (fern + fiddle leaf)");
    assert.equal(didTitle(d({ kind: "event_fired", title: "Remind Ellis about the vet" })), "Reminded you about the vet");
    assert.equal(didTitle(d({ kind: "event_fired", title: "Remind me about the vet" })), "Reminded you about the vet");
  });
  // Review 2026-09-26 (third-person-and-imperative-titles): a task he ran
  // reads as done, not as a to-do for Ellis.
  it("a scheduled task he ran reads in the past tense", () => {
    assert.equal(didTitle(d({ kind: "event_fired", title: "Check table availability for the 29th" })), "Checked table availability for the 29th");
    assert.equal(didTitle(d({ kind: "event_fired", title: "Send Mom the chili recipe" })), "Sent Mom the chili recipe");
    assert.equal(didTitle(d({ kind: "event_fired", title: "Look up flights to Denver" })), "Looked up flights to Denver");
    assert.equal(didTitle(d({ kind: "event_fired", title: "Weekly backup" })), "Weekly backup", "not a verb: left alone");
    assert.equal(didTitle(d({ kind: "note", title: "Check the oven" })), "Check the oven", "only a task he ran");
  });
  it("a reply in a chat named after the person says it once", () => {
    assert.equal(didTitle(d({ title: "Replied to Parker in Parker" })), "Replied to Parker");
    assert.equal(didTitle(d({ title: "Replied to Parker in Family" })), "Replied to Parker in Family");
  });
  it("an action's detail does not repeat its title", () => {
    assert.equal(
      didDetail(d({ title: "Drafted a reply to Jordan", detail: 'Drafted a reply to Jordan: "Sunday works — 6pm at ours?" (held for your approval)' })),
      '"Sunday works — 6pm at ours?" (held for your approval)',
    );
    assert.equal(didDetail(d({ title: "Replied to Parker in Parker", detail: 'Replied to Parker in Parker: "Saturday\'s still on"' })), '"Saturday\'s still on"');
    assert.equal(didDetail(d({ title: "Pushed you: dog food on the porch", detail: 'Pushed you: "Dog food is on the porch"' })), '"Dog food is on the porch"');
    assert.equal(didDetail(d({ title: "Looked", detail: "Something else entirely" })), "Something else entirely");
  });
});

import { wakesDetail } from "../components/today/todayModel.ts";

describe("wakesDetail", () => {
  const w = (detail: string): TodayDid => ({ id: "wakes:recent", kind: "wakes", title: "Woke 3 times", detail, at: "2026-09-26T22:00:00Z", href: "/settings/thoughts", count: 3 });
  it("shows his last closing line", () => {
    assert.equal(wakesDetail(w("Nothing changed that matters.")), "Last: Nothing changed that matters.");
  });
  it("never shows an error message on Today", () => {
    assert.equal(wakesDetail(w("ValueError: Codex API error (HTTP 503): upstream connect error")), null);
    assert.equal(wakesDetail(w("Traceback (most recent call last)")), null);
    assert.equal(wakesDetail(w("")), null);
  });
});

describe("the message-count row", () => {
  it("the backend's own plain words pass through untouched (2026-09-26)", () => {
    const t: TodayDid = {
      id: "triage:today",
      kind: "triage",
      title: "Read 47 messages · 4 for you",
      detail: "Remembered 6 · you answered 3 yourself · 34 needed nothing",
      at: "2026-09-26T22:00:00Z",
      href: "/settings/heartbeat",
    };
    assert.equal(didTitle(t), "Read 47 messages · 4 for you");
    assert.equal(didDetail(t), "Remembered 6 · you answered 3 yourself · 34 needed nothing");
  });

  it("keeps the title short and says the rest underneath", () => {
    const t: TodayDid = {
      id: "triage:today",
      kind: "triage",
      title: "Triaged 47 messages, noted 6, escalated 4, you handled 3",
      detail: "34 set aside.",
      at: "2026-09-26T22:00:00Z",
      href: "/settings/heartbeat",
    };
    assert.equal(didDetail(t), "Noted 6 · you handled 3 · 34 set aside");
    assert.equal(didDetail({ ...t, title: "Triaged 5 messages, noted 0, escalated 0", detail: "" }), "");
  });
});

// Review 2026-09-26 (done-without-undo): Done waits out an Undo before it
// is sent; a decision (a send, a dose, an answer) is not a Done.
import { isDeferredDone } from "../components/today/todayModel.ts";

describe("isDeferredDone", () => {
  it("a Done or a dismiss on a message, loop, note or calendar card waits for its Undo", () => {
    for (const kind of ["heartbeat", "email", "open_loop", "note", "calendar", "event", "worker"]) {
      assert.equal(isDeferredDone({ ...dose, kind }, A({ id: "done", label: "Done" })), true, kind);
    }
    assert.equal(isDeferredDone(flagNote, A({ id: "dismiss", label: "Dismiss" })), true);
  });
  it("a send, a dose, an answer or a snooze goes at once", () => {
    assert.equal(isDeferredDone(approval, approval.actions[1]), false);
    assert.equal(isDeferredDone({ ...approval }, A({ id: "done", label: "Done" })), false);
    assert.equal(isDeferredDone(dose, dose.actions[0]), false);
    assert.equal(isDeferredDone(flagNote, flagNote.actions[1]), false);
    assert.equal(isDeferredDone(dose, A({ id: "snooze", label: "Snooze", method: "PATCH" })), false);
    assert.equal(isDeferredDone(dose, A({ id: "done", label: "Done", method: "GET" })), false);
  });
});

// Review 2026-09-26 (heldback-lists-all-notes): "And 3 more notes" lists
// the notes that need Ellis and are not already cards — one row per title,
// like the cards — not every note of the last day.
import { heldBackNotes } from "../components/today/todayModel.ts";

describe("heldBackNotes", () => {
  const note = (id: string, title: string, needs_you = true, kind = "mind") => ({ id, kind, title, body: `${title} body`, href: `/chat?c=${id}`, needs_you });
  it("keeps only notes that need Ellis and are not on a card already", () => {
    const needs = [{ ...flagNote, note_ids: ["n-flag-1", "n-flag-2"] }];
    const rows = heldBackNotes(
      [note("n-flag-1", "Which is right?"), note("n-flag-2", "Which is right?"), note("n-9", "Book the vet"), note("n-w", "whisper", false, "whisper"), note("n-i", "Idea", true, "idea")],
      needs,
    );
    assert.deepEqual(rows.map((r) => r.id), ["n-9"]);
  });
  it("folds notes sharing a title into one row, as the cards do", () => {
    const rows = heldBackNotes([note("a", "Pay the plumber"), note("b", "pay the plumber "), note("c", "Renew passport")], []);
    assert.deepEqual(rows.map((r) => [r.id, r.count]), [["a", 2], ["c", 1]]);
  });
  it("gives every row somewhere to go", () => {
    assert.equal(heldBackNotes([{ id: "x", kind: "mind", title: "t", needs_you: true }], [])[0].href, "/today");
  });
});

// Review 2026-09-26 (third-person-and-imperative-titles): Edward talks to
// Ellis, not about him.
describe("needTitle for a reminder", () => {
  const ev: TodayNeed = { ...dose, id: "event:7", kind: "event", title: "Remind Ellis to call the vet about Biscuit's bloodwork (three questions attached)", actions: [] };
  it("a reminder for Ellis reads as one", () => {
    assert.equal(needTitle(ev), "Reminder: call the vet about Biscuit's bloodwork (three questions attached)");
    assert.equal(needTitle({ ...ev, title: "Remind Ellis about the vet" }), "Reminder: the vet");
    assert.equal(needTitle({ ...ev, title: "Remind me to call the vet" }), "Reminder: call the vet");
    assert.equal(needTitle({ ...ev, title: "Water the plants" }), "Water the plants");
  });
});
