// R/G TDD red for Bucket D (mobile nav).
//
// The ThumbDock needs active-state highlighting based on the current
// pathname, since it now lives in ClientLayout and renders on every
// authenticated page. A pure helper keeps this testable.
//
// Run: node --test frontend/__tests__/navItems.test.ts

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { isNavActive, NAV_ITEMS } from "../components/layout/navItems.ts";

describe("NAV_ITEMS", () => {
  it("lists Today, Chat, Edward in dock order", () => {
    const labels = NAV_ITEMS.map((i) => i.label);
    assert.deepEqual(labels, ["Today", "Chat", "Edward"]);
  });

  it("keeps /settings as the Edward tab's route so old links survive", () => {
    const edward = NAV_ITEMS.find((i) => i.label === "Edward");
    assert.equal(edward?.href, "/settings");
    const today = NAV_ITEMS.find((i) => i.label === "Today");
    assert.equal(today?.href, "/today");
  });
});

describe("isNavActive", () => {
  it("matches /chat exactly", () => {
    assert.equal(isNavActive("/chat", "/chat"), true);
    assert.equal(isNavActive("/today", "/chat"), false);
  });

  it("matches /settings prefix for any settings subpage", () => {
    assert.equal(isNavActive("/settings", "/settings"), true);
    assert.equal(isNavActive("/settings/heartbeat", "/settings"), true);
    assert.equal(isNavActive("/settings/events", "/settings"), true);
    assert.equal(isNavActive("/chat", "/settings"), false);
  });

  it("matches /today exactly", () => {
    assert.equal(isNavActive("/today", "/today"), true);
    assert.equal(isNavActive("/chat", "/today"), false);
  });

  it("handles query strings on pathname", () => {
    assert.equal(isNavActive("/chat?c=abc", "/chat"), true);
  });
});

// The dock had a "New" button next to a "Chat" tab: two buttons that both
// land you in a chat. One tab now: tap Chat from elsewhere to open it, tap
// it again while you're there to start a fresh one.
import { chatTabAction } from "../components/layout/navItems.ts";

describe("chatTabAction", () => {
  it("opens chat from another tab", () => {
    assert.equal(chatTabAction("/today"), "open");
    assert.equal(chatTabAction("/settings/room/mind"), "open");
  });
  it("starts a new chat when already on chat", () => {
    assert.equal(chatTabAction("/chat"), "new");
    assert.equal(chatTabAction("/chat?c=abc"), "new");
  });
});

// The native tab bar (2026-09-26, design system §5): tapping a tab you are
// already on does what iOS does — Today scrolls back to the top, the Edward
// tab pops a sub-page back to its root (and scrolls when already there),
// and Chat starts a fresh conversation.
import { dockTapAction, barActions, SCROLL_TOP_EVENT } from "../components/layout/navItems.ts";

describe("dockTapAction", () => {
  it("navigates to a tab you are not on", () => {
    assert.equal(dockTapAction("/chat", "/today"), "navigate");
    assert.equal(dockTapAction("/today", "/settings"), "navigate");
    assert.equal(dockTapAction("/settings/room/mind", "/chat"), "navigate");
  });
  it("scrolls Today to the top when tapped on Today", () => {
    assert.equal(dockTapAction("/today", "/today"), "scroll-top");
    assert.equal(dockTapAction("/today?x=1", "/today"), "scroll-top");
  });
  it("starts a new chat when Chat is tapped on chat", () => {
    assert.equal(dockTapAction("/chat", "/chat"), "new-chat");
    assert.equal(dockTapAction("/chat?c=abc", "/chat"), "new-chat");
  });
  it("pops an Edward sub-page back to the Edward tab's root, and scrolls at the root", () => {
    assert.equal(dockTapAction("/settings/room/mind", "/settings"), "navigate");
    assert.equal(dockTapAction("/settings/thoughts", "/settings"), "navigate");
    assert.equal(dockTapAction("/settings", "/settings"), "scroll-top");
  });
  it("names the window event pages listen to", () => {
    assert.equal(SCROLL_TOP_EVENT, "edward:scroll-top");
  });
});

describe("barActions (the presence bar's one contextual slot)", () => {
  it("on chat: history and a new chat", () => {
    assert.deepEqual(barActions("/chat"), ["history", "new-chat"]);
    assert.deepEqual(barActions("/chat?c=abc"), ["history", "new-chat"]);
  });
  it("elsewhere: search (the command palette on a phone)", () => {
    assert.deepEqual(barActions("/today"), ["search"]);
    assert.deepEqual(barActions("/settings/room/memory"), ["search"]);
  });
});

// The Edward tab's pip: a small emerald dot when he wants Ellis — an unread
// thought or a send held for approval — with a label that says which.
import { edwardTabBadge } from "../components/layout/navItems.ts";

describe("edwardTabBadge", () => {
  it("is quiet when nothing waits", () => {
    assert.deepEqual(edwardTabBadge({ unreadThoughts: 0, approvalsWaiting: 0 }), { show: false, label: "Edward" });
  });
  it("names new thoughts", () => {
    assert.deepEqual(edwardTabBadge({ unreadThoughts: 1, approvalsWaiting: 0 }), { show: true, label: "Edward, 1 new thought" });
    assert.deepEqual(edwardTabBadge({ unreadThoughts: 3, approvalsWaiting: 0 }), { show: true, label: "Edward, 3 new thoughts" });
  });
  it("puts approvals first", () => {
    assert.deepEqual(edwardTabBadge({ unreadThoughts: 2, approvalsWaiting: 1 }), {
      show: true,
      label: "Edward, 1 waiting for your approval, 2 new thoughts",
    });
  });
});
