// Settings → House → Skills / Edward's servers (2026-09-26). Pure.
//
// - Skills are grouped by what they let him do, in plain words.
// - Status is a dot and a word; an error says what is wrong.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { groupSkills, serverStatus, skillCopy, skillStatus, skillsSummary } from "../lib/skillView.ts";

const s = (id: string, over: Record<string, unknown> = {}) => ({
  id, name: id, description: "technical", enabled: true, status: "connected", status_message: null, metadata: null, ...over,
});

describe("groupSkills", () => {
  it("groups by what he can do, in a fixed order, dropping empty groups", () => {
    const groups = groupSkills([s("brave_search"), s("imessage_applescript"), s("code_interpreter"), s("mystery"), s("twilio_sms")] as never);
    assert.deepEqual(groups.map((g) => g.label), ["Messages", "Web", "Code", "Other"]);
    assert.deepEqual(groups[0].skills.map((x) => x.id), ["imessage_applescript", "twilio_sms"]);
  });
});

describe("skillCopy", () => {
  it("says what a skill does for Ellis", () => {
    assert.deepEqual(skillCopy(s("imessage_applescript") as never), { name: "iMessage", description: "Send iMessages from your number." });
    assert.deepEqual(skillCopy(s("mystery", { name: "Mystery", description: "Does a thing" }) as never), { name: "Mystery", description: "Does a thing" });
  });
});

describe("skillStatus", () => {
  it("is a tone and a word", () => {
    assert.deepEqual(skillStatus(s("x") as never), { tone: "ok", text: "Connected" });
    assert.deepEqual(skillStatus(s("x", { status: "connecting" }) as never), { tone: "warn", text: "Starting…" });
    assert.deepEqual(skillStatus(s("x", { status: "error", status_message: "Bridge not paired" }) as never), { tone: "bad", text: "Bridge not paired" });
    assert.deepEqual(skillStatus(s("x", { status: "error" }) as never), { tone: "bad", text: "Can't connect" });
    assert.deepEqual(skillStatus(s("x", { status: "disabled", enabled: false }) as never), { tone: "neutral", text: "Off" });
  });

  it("sums up the list", () => {
    assert.equal(
      skillsSummary([s("a"), s("b", { status: "error" }), s("c", { enabled: false, status: "disabled" })] as never),
      "2 on · 1 needs a look",
    );
    assert.equal(skillsSummary([s("a")] as never), "1 on");
  });
});

describe("serverStatus", () => {
  it("names each server state", () => {
    assert.deepEqual(serverStatus("connected"), { tone: "ok", text: "Running" });
    assert.deepEqual(serverStatus("starting"), { tone: "warn", text: "Starting…" });
    assert.deepEqual(serverStatus("error"), { tone: "bad", text: "Stopped with an error" });
    assert.deepEqual(serverStatus("disabled"), { tone: "neutral", text: "Off" });
    assert.deepEqual(serverStatus("stopped"), { tone: "neutral", text: "Stopped" });
  });
});
