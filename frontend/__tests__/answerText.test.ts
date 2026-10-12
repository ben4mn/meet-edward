// R/G for how an answer's links and read-aloud text behave (2026-09-26).
//
// A link to somewhere in the app (/chat?c=…, /today, or the app's own origin)
// stays in the app — no new tab, no ↗. A link off the app opens in a new tab
// with ↗. A stored file downloads. "Read aloud" speaks the answer without its
// markdown punctuation, code or tables.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { classifyLink, speakableText } from "../lib/answerText.ts";

describe("classifyLink", () => {
  const origin = "https://edward.example.com";

  it("keeps app paths in the app", () => {
    assert.deepEqual(classifyLink("/chat?c=t-reply-parker", origin), { kind: "internal", href: "/chat?c=t-reply-parker" });
    assert.deepEqual(classifyLink("https://edward.example.com/today", origin), { kind: "internal", href: "/today" });
    assert.deepEqual(classifyLink("#top", origin), { kind: "internal", href: "#top" });
  });

  it("sends other sites to a new tab", () => {
    assert.deepEqual(classifyLink("https://www.nws.gov/forecast", origin), { kind: "external", href: "https://www.nws.gov/forecast" });
    assert.deepEqual(classifyLink("mailto:owner@example.com", origin), { kind: "external", href: "mailto:owner@example.com" });
  });

  it("downloads a stored file by its relative path, whatever the host", () => {
    assert.deepEqual(classifyLink("https://other.host/api/files/f1/download", origin), { kind: "download", href: "/api/files/f1/download" });
    assert.deepEqual(classifyLink("/api/files/f2/download", origin), { kind: "download", href: "/api/files/f2/download" });
  });

  it("treats a missing href as nothing to follow", () => {
    assert.deepEqual(classifyLink(undefined, origin), { kind: "none", href: "" });
    assert.deepEqual(classifyLink("javascript:alert(1)", origin), { kind: "none", href: "" });
  });
});

describe("speakableText", () => {
  it("drops markdown punctuation, links' targets, code and tables", () => {
    const md = [
      "## The short version",
      "",
      "You're **on pace** — see [the plan](/today/goals/g1).",
      "",
      "- Make the last *2 miles* fast",
      "1. Sleep",
      "",
      "| a | b |",
      "|---|---|",
      "| 1 | 2 |",
      "",
      "```text",
      "week 5: 10 mi",
      "```",
      "",
      "> Never drop the long run. Use `strides`.",
    ].join("\n");
    assert.equal(
      speakableText(md),
      "The short version.\nYou're on pace — see the plan.\nMake the last 2 miles fast.\nSleep.\nNever drop the long run. Use strides.",
    );
  });
});

import { retryQuestion } from "../lib/answerText.ts";

describe("retryQuestion", () => {
  const pack = `[WAKE: morning] thu 7:00am. Ellis is away.\n${"line\n".repeat(200)}`;
  it("is Ellis's question before the answer", () => {
    const msgs = [
      { role: "user" as const, content: "what's the weather?" },
      { role: "assistant" as const, content: "Sunny." },
    ];
    assert.equal(retryQuestion(msgs, 1), "what's the weather?");
  });
  it("never resends a wake pack, a trigger or an attachment-only message", () => {
    assert.equal(retryQuestion([{ role: "user", content: pack }, { role: "assistant", content: "Left it." }], 1), undefined);
    assert.equal(retryQuestion([{ role: "user", content: "[HEARTBEAT EVENT] x", isTrigger: true }, { role: "assistant", content: "ok" }], 1), undefined);
    assert.equal(retryQuestion([{ role: "user", content: "  " }, { role: "assistant", content: "ok" }], 1), undefined);
    assert.equal(retryQuestion([{ role: "assistant", content: "hi" }], 0), undefined);
  });
});

import { describeTrigger } from "../lib/answerText.ts";

describe("describeTrigger", () => {
  it("names what woke him by its type, whatever the header's case", () => {
    const t = describeTrigger('[Heartbeat event] 3 new messages in "Family" from Mom, Sam', "heartbeat");
    assert.equal(t.kind, "heartbeat");
    assert.equal(t.label, "A message I noticed");
    assert.deepEqual(t.fields, []);
    assert.equal(t.body, '3 new messages in "Family" from Mom, Sam');
  });

  it("reads key: value lines and drops internal ids", () => {
    const t = describeTrigger("[SCHEDULED EVENT] The following event is now due.\nEvent: Call the vet\nConversation ID: c1\nWhen: 10:00", undefined);
    assert.equal(t.kind, "scheduled");
    assert.equal(t.label, "Something I scheduled");
    assert.deepEqual(t.fields, [["Event", "Call the vet"], ["When", "10:00"]]);
    assert.equal(t.body, "The following event is now due.");
  });
});
