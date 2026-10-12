// Mail on the work rail (2026-10-11): messages(mail_search | mail_read) and
// send_message with a subject (the backend's chip reads "email: <to>").
// A text stays "Texted Parker"; an email reads "Emailed parker@…", folds into
// "emailed Parker and Jo" and fails as "Couldn't email …".

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { describeTool, failedPhrase, hasActionLabel, pendingArgsSummary, receiptPhrase, tallyKey } from "../lib/toolLabels.ts";

describe("reading email", () => {
  it("a search names what it looked for", () => {
    const d = describeTool("messages", 'mail_search: "from:parker dinner"');
    assert.equal(d.title, "Searched your email for “from:parker dinner”");
    assert.equal(d.live, "Searching your email for “from:parker dinner”…");
    assert.equal(d.icon, "mail");
    assert.equal(describeTool("messages", "mail_search").title, "Searched your email");
  });
  it("reading one", () => {
    assert.equal(describeTool("messages", "mail_read").title, "Read an email");
    assert.equal(receiptPhrase("messages", ["mail_read", "mail_read"]), "read 2 emails");
  });
  it("both are labelled actions", () => {
    assert.ok(hasActionLabel("messages", "mail_search"));
    assert.ok(hasActionLabel("messages", "mail_read"));
  });
});

describe("sending email", () => {
  it("an email reads Emailed, a text still Texted", () => {
    const email = describeTool("send_message", "email: parker@example.com");
    assert.equal(email.title, "Emailed parker@example.com");
    assert.equal(email.live, "Emailing parker@example.com…");
    assert.equal(email.icon, "mail");
    assert.ok(email.outward);
    assert.equal(describeTool("send_message", "to: Parker").title, "Texted Parker");
  });
  it("a reply with no address yet", () => {
    assert.equal(describeTool("send_message", "email: a reply").title, "Emailed a reply");
    assert.equal(describeTool("send_message", "email").title, "Sent an email");
  });
  it("emails fold together, apart from texts", () => {
    assert.equal(receiptPhrase("send_message", ["email: Parker", "email: Jo"]), "emailed Parker and Jo");
    assert.notEqual(tallyKey("send_message", "email: Parker"), tallyKey("send_message", "to: Parker"));
    assert.equal(receiptPhrase("send_message", ["to: Parker"]), "texted Parker");
  });
  it("a failed email says so", () => {
    assert.equal(describeTool("send_message", "email: parker@example.com").failed, "Couldn't email parker@example.com");
    assert.equal(failedPhrase("send_message", ["email: parker@example.com"]), "couldn't email parker@example.com");
  });
  it("while it is being written, a send still shows who it's to", () => {
    assert.equal(pendingArgsSummary("send_message", '{"message": "hi", "to": "Parker"}'), "to: Parker");
    assert.equal(pendingArgsSummary("send_message", '{"to": "parker@example.com", "subject": "Dinner'), "email: parker@example.com");
  });
});
