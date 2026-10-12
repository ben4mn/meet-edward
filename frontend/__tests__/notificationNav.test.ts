// R/G red for the dead notification tap.
//
// Every push carries a destination. Tapping one must take Ellis there —
// Edward's chain of thought (/chat?c=…), the Today feed, or a settings
// room — whatever tab the PWA was left on. Before this, the app only
// reacted to /chat?c= URLs and only by loading state, so a tap from the
// Today tab changed nothing on screen.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  approvalDecisionRequest,
  notificationDestination,
  resolveNotificationNav,
} from "../lib/notificationNav.ts";

const ORIGIN = "https://edward.example.com";

describe("resolveNotificationNav", () => {
  it("keeps a relative app path, query included", () => {
    assert.equal(resolveNotificationNav("/chat?c=abc", ORIGIN), "/chat?c=abc");
    assert.equal(resolveNotificationNav("/today", ORIGIN), "/today");
  });

  it("reduces an absolute same-origin URL to its path", () => {
    assert.equal(
      resolveNotificationNav("https://edward.example.com/settings/room/work", ORIGIN),
      "/settings/room/work",
    );
  });

  it("refuses another origin", () => {
    assert.equal(resolveNotificationNav("https://evil.example/x", ORIGIN), null);
  });

  it("refuses garbage and empty input", () => {
    assert.equal(resolveNotificationNav("", ORIGIN), null);
    assert.equal(resolveNotificationNav(undefined, ORIGIN), null);
  });

  it("sends a bare root to Today, the front door", () => {
    assert.equal(resolveNotificationNav("/", ORIGIN), "/today");
  });
});

// 2026-09-24, taint and approvals: a send Edward held at the boundary
// arrives as a push of kind "approval" with Approve / Deny / Open. The
// buttons answer out of band (POST /api/approvals/<id>); Open — and a plain
// tap, which is all iOS draws — goes to the thread the send came from.
describe("approval pushes", () => {
  const approval = {
    kind: "approval",
    approval_id: 7,
    conversation_id: "edward-mind",
    url: "/chat?c=edward-mind",
  };

  it("routes a tap to the conversation the send came from", () => {
    assert.equal(notificationDestination(approval, ORIGIN), "/chat?c=edward-mind");
    assert.equal(
      notificationDestination({ ...approval, url: "/today" }, ORIGIN),
      "/chat?c=edward-mind",
    );
  });

  it("falls back to the push URL, then Today", () => {
    assert.equal(
      notificationDestination({ ...approval, conversation_id: null }, ORIGIN),
      "/chat?c=edward-mind",
    );
    assert.equal(notificationDestination({ kind: "approval", approval_id: 7 }, ORIGIN), "/today");
    assert.equal(notificationDestination({ kind: "mind", url: "/settings/room/mind" }, ORIGIN), "/settings/room/mind");
    assert.equal(notificationDestination({ url: "https://evil.example/x" }, ORIGIN), "/today");
    assert.equal(notificationDestination(null, ORIGIN), "/today");
  });

  it("turns Approve and Deny into the decision request", () => {
    assert.deepEqual(approvalDecisionRequest(approval, "approve"), {
      path: "/api/approvals/7",
      body: { decision: "approve" },
    });
    assert.deepEqual(approvalDecisionRequest({ ...approval, approval_id: "12" }, "deny"), {
      path: "/api/approvals/12",
      body: { decision: "deny" },
    });
  });

  it("never posts a decision for anything else", () => {
    assert.equal(approvalDecisionRequest(approval, "open"), null);
    assert.equal(approvalDecisionRequest(approval, ""), null);
    assert.equal(approvalDecisionRequest({ ...approval, kind: "mind" }, "approve"), null);
    assert.equal(approvalDecisionRequest({ ...approval, approval_id: null }, "approve"), null);
    assert.equal(approvalDecisionRequest({ ...approval, approval_id: "7/../../push" }, "approve"), null);
    assert.equal(approvalDecisionRequest(null, "approve"), null);
  });
});
