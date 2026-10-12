// Settings → House → Skills and Edward's servers (2026-09-26, Jarvis polish).
//
// - Real switches (role=switch, spring knob), status dots, plain words,
//   grouped by what they let him do; tokens only.
// - Removing a server asks once in a ConfirmSheet.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { mockFetch, restoreFetch, type Call } from "./panelTestKit.ts";
import { SkillsPanel } from "../components/settings/SkillsPanel.tsx";
import { CustomMCPPanel } from "../components/settings/CustomMCPPanel.tsx";

const skill = (id: string, name: string, over: Record<string, unknown> = {}) => ({
  id, name, description: "technical words", enabled: true, status: "connected", status_message: null, metadata: null, ...over,
});

const SKILLS = [
  skill("imessage_applescript", "iMessage (AppleScript)"),
  skill("whatsapp_mcp", "WhatsApp (MCP)", { status: "error", status_message: "Bridge not paired — scan the QR code" }),
  skill("brave_search", "Web Search (Brave)"),
  skill("shell_interpreter", "Shell/Bash", { enabled: false, status: "disabled" }),
];

const SERVERS = [
  { id: "mcp-weather", name: "weather", description: "NWS forecasts and alerts", package_name: "@example/mcp-weather", runtime: "npx",
    tool_prefix: "weather", enabled: true, status: "connected", error: null, tool_names: ["a", "b", "c"], tool_count: 3, args: [],
    env_var_keys: [], source_url: null, added_at: null, updated_at: null },
  { id: "mcp-strava", name: "strava", description: "Read activities from Strava", package_name: "strava-mcp", runtime: "uvx",
    tool_prefix: "strava", enabled: true, status: "error", error: "Token expired — re-authorize in the Strava app", tool_names: [],
    tool_count: 0, args: ["--read-only"], env_var_keys: ["STRAVA_CLIENT_ID"], source_url: null, added_at: null, updated_at: null },
];

let calls: Call[] = [];

beforeEach(() => {
  calls = mockFetch((url, method, body) => {
    if (url.endsWith("/api/skills/reload")) return { skills: SKILLS, last_reload: new Date().toISOString() };
    const sm = url.match(/\/api\/skills\/([^/?]+)$/);
    if (sm && method === "PATCH") {
      const s = SKILLS.find((x) => x.id === sm[1])!;
      const on = (body as { enabled: boolean }).enabled;
      return { ...s, enabled: on, status: on ? "connected" : "disabled" };
    }
    if (url.includes("/api/skills")) return { skills: SKILLS, last_reload: new Date().toISOString() };
    if (url.includes("/api/custom-mcp/") && method === "DELETE") return { status: "removed" };
    if (url.includes("/api/custom-mcp")) return { servers: SERVERS };
    return undefined;
  });
});

afterEach(() => {
  cleanup();
  restoreFetch();
});

describe("SkillsPanel", () => {
  it("groups skills in plain words with real switches and status", async () => {
    const { container } = render(<SkillsPanel />);
    const messages = await screen.findByRole("region", { name: "Messages" });
    assert.ok(within(messages).getByText("iMessage"));
    assert.ok(within(messages).getByText("Send iMessages from your number."));
    assert.ok(within(messages).getByText("Bridge not paired — scan the QR code"));
    const sw = within(messages).getByRole("switch", { name: "iMessage" });
    assert.equal(sw.getAttribute("aria-checked"), "true");
    assert.equal(screen.getByRole("switch", { name: "Shell" }).getAttribute("aria-checked"), "false");
    assert.doesNotMatch(container.innerHTML, /\b(green|red|yellow|gray|zinc)-\d/);
  });

  it("switches a skill off", async () => {
    render(<SkillsPanel />);
    const sw = await screen.findByRole("switch", { name: "Web search" });
    fireEvent.click(sw);
    await waitFor(() => {
      const c = calls.find((x) => x.url.endsWith("/api/skills/brave_search") && x.method === "PATCH");
      assert.ok(c);
      assert.deepEqual(c!.body, { enabled: false });
    });
    await waitFor(() => assert.equal(screen.getByRole("switch", { name: "Web search" }).getAttribute("aria-checked"), "false"));
  });

  it("checks every connection again on request", async () => {
    render(<SkillsPanel />);
    await screen.findByText("iMessage");
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => assert.ok(calls.some((c) => c.url.endsWith("/api/skills/reload") && c.method === "POST")));
  });
});

describe("CustomMCPPanel", () => {
  it("shows each server with a switch, its status and what went wrong", async () => {
    render(<CustomMCPPanel />);
    await screen.findByText("weather");
    assert.equal(screen.getByRole("switch", { name: "weather" }).getAttribute("aria-checked"), "true");
    assert.ok(screen.getByText("Stopped with an error"));
    const alert = screen.getByRole("alert");
    assert.match(alert.textContent ?? "", /Token expired/);
  });

  it("removes a server only after one confirm", async () => {
    render(<CustomMCPPanel />);
    await screen.findByText("strava");
    fireEvent.click(screen.getByRole("button", { name: "Remove strava" }));
    const dialog = await screen.findByRole("dialog", { name: "Remove strava?" });
    assert.equal(calls.some((c) => c.method === "DELETE"), false);
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => assert.ok(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/api/custom-mcp/mcp-strava"))));
  });
});
