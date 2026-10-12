// Settings → House → Gmail and Alexa (2026-09-26, Jarvis polish).
//
// - Gmail: the selected poll interval is a Segment (it was an undefined
//   bg-accent-blue: invisible); disconnect asks in a ConfirmSheet.
// - Alexa: the app's relative API base — never localhost:8000, which on
//   the phone is the phone.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { mockFetch, restoreFetch, type Call } from "./panelTestKit.ts";
import { GmailPanel } from "../components/settings/GmailPanel.tsx";
import { AlexaPanel } from "../components/settings/AlexaPanel.tsx";

let calls: Call[] = [];
let gmailConfigured = true;

beforeEach(() => {
  gmailConfigured = true;
  calls = mockFetch((url, method) => {
    if (url.includes("/api/gmail/status")) return gmailConfigured
      ? { configured: true, email_address: "owner@example.com", token_valid: true }
      : { configured: false, email_address: null, token_valid: false };
    if (url.includes("/api/gmail/disconnect")) return { status: "disconnected" };
    if (url.includes("/api/heartbeat/status")) return { gmail_enabled: true, gmail_poll_seconds: 120 };
    if (url.includes("/api/heartbeat/config") && method === "PATCH") return {};
    if (url.includes("/api/alexa/status")) return {
      outbound: { configured: true, healthy: true, ha_url: "http://homeassistant.local:8123", device_count: 2 },
      inbound: { configured: false, skill_id: "" },
      devices: [{ entity_id: "media_player.kitchen_echo", friendly_name: "Kitchen Echo", state: "idle" }],
      recent_announcements: [],
    };
    if (url.includes("/api/alexa/test")) return { success: true, message: "Announced on Kitchen Echo" };
    return undefined;
  });
});

afterEach(() => {
  cleanup();
  restoreFetch();
});

describe("GmailPanel", () => {
  it("shows the chosen poll interval as a selected segment", async () => {
    render(<GmailPanel />);
    await screen.findByText("owner@example.com");
    const group = screen.getByRole("radiogroup", { name: "How often I look" });
    const checked = within(group).getAllByRole("radio").filter((r) => r.getAttribute("aria-checked") === "true");
    assert.deepEqual(checked.map((r) => r.textContent), ["2m"]);
    fireEvent.click(within(group).getByRole("radio", { name: "5m" }));
    await waitFor(() => assert.ok(calls.some((c) => c.method === "PATCH" && (c.body as { gmail_poll_seconds?: number }).gmail_poll_seconds === 300)));
    assert.equal(within(group).getByRole("radio", { name: "5m" }).getAttribute("aria-checked"), "true");
    assert.equal(screen.getByRole("switch", { name: "Watch my inbox" }).getAttribute("aria-checked"), "true");
  });

  it("asks before disconnecting, in a sheet", async () => {
    render(<GmailPanel />);
    await screen.findByText("owner@example.com");
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    const dialog = await screen.findByRole("dialog", { name: "Disconnect Gmail?" });
    assert.equal(calls.some((c) => c.url.includes("/api/gmail/disconnect")), false);
    fireEvent.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => assert.ok(calls.some((c) => c.url.includes("/api/gmail/disconnect") && c.method === "POST")));
  });

  it("offers to connect when it is not", async () => {
    gmailConfigured = false;
    render(<GmailPanel />);
    assert.ok(await screen.findByRole("button", { name: "Connect Gmail" }));
  });
});

describe("AlexaPanel", () => {
  it("talks to the app's own API, not localhost:8000", async () => {
    render(<AlexaPanel />);
    within(await screen.findByRole("region", { name: "Echo devices" })).getByText("Kitchen Echo");
    const alexa = calls.filter((c) => c.url.includes("/api/alexa/"));
    assert.ok(alexa.length > 0);
    for (const c of alexa) {
      assert.doesNotMatch(c.url, /localhost:8000|^https?:/);
      assert.ok(c.url.startsWith("/api/alexa/"));
    }
  });

  it("sends a test announcement and says how it went", async () => {
    render(<AlexaPanel />);
    within(await screen.findByRole("region", { name: "Echo devices" })).getByText("Kitchen Echo");
    fireEvent.change(screen.getByLabelText("What should I say?"), { target: { value: "Dinner's ready" } });
    fireEvent.click(screen.getByRole("button", { name: "Announce" }));
    const status = await screen.findByText(/Announced on Kitchen Echo/);
    assert.ok(status);
    const c = calls.find((x) => x.url === "/api/alexa/test");
    assert.deepEqual(c?.body, { message: "Dinner's ready" });
  });
});
