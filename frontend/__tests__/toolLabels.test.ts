// R/G for the work rail's tool labels (2026-09-26).
//
// Ellis reads Edward's tool calls in the chat's work rail. A raw tool name
// ("browser_open", "get_recent_messages") is internal vocabulary; each call
// reads as what Edward did, in plain words, with the argument that matters
// in the label ('Opened example.com', 'Searched “kayak weather”', 'Texted
// Parker'). Outward acts (a send, a push, a note, a hosted page) are marked so
// the rail can give them Edward's "acted" treatment. The receipt groups calls
// into phrases ("opened 2 pages", "texted Parker").

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import { describeTool, hasExplicitLabel, humanizeToolName, receiptPhrase } from "../lib/toolLabels.ts";

describe("describeTool", () => {
  it("opens a page by its host", () => {
    const d = describeTool("browser_open", "https://www.example.com/forecast/lake-geneva");
    assert.equal(d.icon, "globe");
    assert.equal(d.title, "Opened example.com");
    assert.equal(d.live, "Opening example.com…");
    assert.equal(d.outward, false);
  });

  it("quotes a search with curly quotes and no stray ellipsis", () => {
    const d = describeTool("web_search", '"Lake Tahoe CA hourly forecast Saturday"');
    assert.equal(d.icon, "search");
    assert.equal(d.title, "Searched “Lake Tahoe CA hourly forecast Saturday”");
    assert.equal(d.live, "Searching “Lake Tahoe CA hourly forecast Saturday”…");
    const cut = describeTool("web_search", '"a very long query that the backend clipped..."');
    assert.equal(cut.title, "Searched “a very long query that the backend clipped…”");
  });

  it("names who he texted, and marks it outward", () => {
    const d = describeTool("send_imessage", "to: Parker");
    assert.equal(d.icon, "send");
    assert.equal(d.title, "Texted Parker");
    assert.equal(d.outward, true);
    assert.equal(describeTool("send_imessage").title, "Sent a text");
  });

  it("leaves a note and sends a push as outward acts", () => {
    const note = describeTool("leave_note", '"Biscuit\'s meds"');
    assert.equal(note.icon, "note");
    assert.equal(note.title, "Left you a note");
    assert.deepEqual(note.chips, ["Biscuit's meds"]);
    assert.equal(note.outward, true);
    assert.equal(describeTool("send_push_notification", '"Door open"').outward, true);
    assert.equal(describeTool("create_hosted_page", '"<html>"').outward, true);
  });

  it("closes an intention in words, with what came of it beside it (2026-09-27)", () => {
    // close_intention (chunk 1) retires one of his own intentions; the
    // backend's args_summary carries the outcome, never the memory id.
    const d = describeTool("close_intention", '"Briefed Theo on Friday"');
    assert.equal(d.title, "Closed an intention");
    assert.equal(d.live, "Closing an intention…");
    assert.deepEqual(d.chips, ["Briefed Theo on Friday"]);
    assert.equal(d.outward, false);
    assert.equal(describeTool("close_intention").title, "Closed an intention");
  });

  it("schedules by title and reads messages by chat", () => {
    assert.equal(describeTool("schedule_event", '"Call the vet"').title, "Scheduled “Call the vet”");
    assert.equal(describeTool("schedule_event").icon, "calendar-plus");
    assert.equal(describeTool("get_recent_messages", '"Parker"').title, "Read messages with Parker");
    assert.equal(describeTool("get_recent_messages").title, "Read recent messages");
  });

  it("remembers, reads documents, checks goals and runs code", () => {
    const r = describeTool("remember", '"Ellis prefers notes after 9pm"');
    assert.equal(r.icon, "brain");
    assert.equal(r.title, "Remembered");
    assert.deepEqual(r.chips, ["Ellis prefers notes after 9pm"]);
    assert.equal(describeTool("read_document", "id: 42").title, "Read a document");
    assert.deepEqual(describeTool("read_document", "id: 42").chips, [], "an id is not worth a chip");
    assert.equal(describeTool("search_documents", '"trip"').title, "Searched documents for “trip”");
    assert.equal(describeTool("list_goals").title, "Checked your goals");
    assert.equal(describeTool("execute_code").title, "Ran Python");
    assert.equal(describeTool("execute_code").live, "Running Python…");
  });

  it("reads Apple's MCP tools by what they touch", () => {
    assert.equal(describeTool("calendar").title, "Checked your calendar");
    assert.equal(describeTool("apple_calendar_list_events", '"today"').title, "Checked your calendar");
    assert.equal(describeTool("apple_calendar_list_events").live, "Checking your calendar…");
    assert.equal(describeTool("reminders").icon, "list-checks");
  });

  it("humanizes anything it does not know", () => {
    const d = describeTool("whatsapp_list_chats");
    assert.equal(d.icon, "wrench");
    assert.equal(d.title, "List chats (WhatsApp)");
    assert.equal(d.live, "List chats (WhatsApp)…");
    const other = describeTool("frobnicate_widget_state", '"x"');
    assert.equal(other.title, "Frobnicate widget state");
    assert.deepEqual(other.chips, ["x"]);
  });
});

describe("humanizeToolName", () => {
  it("snake and camel case read as words", () => {
    assert.equal(humanizeToolName("get_alexa_devices"), "Get alexa devices");
    assert.equal(humanizeToolName("webSearch"), "Web search");
    assert.equal(humanizeToolName(""), "A tool");
  });
});

describe("receiptPhrase", () => {
  it("groups repeated calls into one phrase", () => {
    assert.equal(receiptPhrase("browser_open", []), "opened a page");
    assert.equal(receiptPhrase("browser_open", ["a", "b"]), "opened 2 pages");
    assert.equal(receiptPhrase("web_search", ["x"]), "searched the web");
    assert.equal(receiptPhrase("web_search", ["x", "y", "z"]), "searched the web 3 times");
  });

  it("names people he texted", () => {
    assert.equal(receiptPhrase("send_imessage", ["to: Parker"]), "texted Parker");
    assert.equal(receiptPhrase("send_imessage", ["to: Parker", "to: Jordan"]), "texted Parker and Jordan");
    assert.equal(receiptPhrase("send_imessage", ["to: Parker", "to: Parker"]), "texted Parker");
    assert.equal(receiptPhrase("send_imessage", ["to: A", "to: B", "to: C"]), "texted 3 people");
    assert.equal(receiptPhrase("send_imessage", [""]), "sent a text");
  });

  it("falls back to the lower-cased label", () => {
    assert.equal(receiptPhrase("list_goals", [""]), "checked your goals");
    assert.equal(receiptPhrase("frobnicate", ["", ""]), "frobnicate ×2");
  });
});

describe("coverage", () => {
  it("every tool the backend defines has a written label", () => {
    // The legacy tools (graph/tools.py, still the bodies the toolset wraps
    // and EDWARD_TOOLSET=legacy's set) and the one toolset (services/toolset/,
    // 2026-10-11). The old >80 floor went with the deleted families.
    const backend = path.resolve(import.meta.dirname, "../../backend/services");
    const toolsetDir = path.join(backend, "toolset");
    const toolsetFiles = fs.existsSync(toolsetDir)
      ? fs.readdirSync(toolsetDir).filter((f) => f.endsWith(".py")).map((f) => path.join(toolsetDir, f))
      : [];
    const files = [path.join(backend, "graph/tools.py"), ...toolsetFiles].filter((f) => fs.existsSync(f));
    assert.ok(files.length > 0, "backend tool files not found");
    const names = new Set<string>();
    const toolsetNames = new Set<string>();
    for (const f of files) {
      const src = fs.readFileSync(f, "utf8");
      for (const m of src.matchAll(/@tool[^\n]*\n(?:@[^\n]*\n)*(?:async )?def ([a-z_]+)\(/g)) {
        names.add(m[1]);
        if (toolsetFiles.includes(f)) toolsetNames.add(m[1]);
      }
    }
    assert.ok(names.size > 40, `found only ${names.size} tools`);
    assert.ok(toolsetNames.size >= 5, `found only ${toolsetNames.size} toolset tools`);
    const missing = [...names].filter((n) => !hasExplicitLabel(n)).sort();
    assert.deepEqual(missing, []);
  });
});

describe("describeTool failed form", () => {
  it("says what he could not do, not that he did it", () => {
    assert.equal(describeTool("apple_calendar_list_events", '"today"').failed, "Couldn't check your calendar");
    assert.equal(describeTool("browser_open", "https://www.nws.gov/x").failed, "Couldn't open nws.gov");
    assert.equal(describeTool("send_imessage", "to: Parker").failed, "Couldn't text Parker");
    assert.equal(describeTool("execute_code").failed, "Couldn't run Python");
    assert.equal(describeTool("schedule_event", '"Call the vet"').failed, "Couldn't schedule “Call the vet”");
    assert.equal(describeTool("save_document", '"Trip"').failed, "Couldn't save “Trip”");
    assert.equal(describeTool("browser_scroll").failed, "Couldn't scroll");
    assert.equal(describeTool("leave_note").failed, "Couldn't leave you a note");
    assert.equal(describeTool("remember").failed, "Couldn't remember");
    assert.equal(describeTool("update_widget").failed, "Couldn't update your widget");
  });

  it("falls back to the name when there is no verb to turn round", () => {
    assert.equal(describeTool("frobnicate_widget").failed, "Couldn't finish: Frobnicate widget");
    assert.equal(describeTool("spawn_cc_worker").failed, "Couldn't finish: Had a helper write code");
  });
});

// 2026-09-26 review: a tool call being composed streamed its raw partial JSON
// into the rail in monospace — `{"query": "…kayaking\non saturday at the lak`.
// The pending node shows what the arguments say, the way a finished call does.
import { pendingArgsSummary } from "../lib/toolLabels.ts";

describe("pendingArgsSummary", () => {
  it("reads the value out of partial JSON: no braces, keys or escapes", () => {
    const s = pendingArgsSummary("web_search", '{"query": "slow what is the weather for kayaking\\non saturday at the lak');
    assert.equal(s, '"slow what is the weather for kayaking on saturday at the lak"');
    const live = describeTool("web_search", s).live;
    assert.equal(/[{}\\]|"query"/.test(live), false, live);
    assert.ok(live.includes("kayaking on saturday"), live);
  });
  it("nothing to show until a value has begun", () => {
    assert.equal(pendingArgsSummary("web_search", '{"query":'), null);
    assert.equal(pendingArgsSummary("web_search", ""), null);
    assert.equal(pendingArgsSummary("web_search", '{"query": "'), null);
  });
  it("a send names its recipient; a URL stays a URL; escaped quotes unescape", () => {
    assert.equal(pendingArgsSummary("send_imessage", '{"message": "hi", "to": "Parker"}'), "to: Parker");
    assert.equal(pendingArgsSummary("browser_open", '{"url": "https://www.nws.gov/x'), '"https://www.nws.gov/x"');
    assert.equal(pendingArgsSummary("save_document", '{"title": "Say \\"hi\\"'), '"Say "hi""');
  });
  it("a closing intention shows its outcome once it begins, not the memory id (2026-09-27)", () => {
    const partial = '{"memory_id": "5b1f0c2e-8d7a-4c1b-9e3f-2a6d8c4b7e10", "outcome": "Briefed Theo on Fri';
    assert.equal(pendingArgsSummary("close_intention", partial), '"Briefed Theo on Fri"');
  });
  it("code is shown by its own node, never as a chip", () => {
    assert.equal(pendingArgsSummary("execute_code", '{"code": "import os'), null);
  });
  it("a long value is cut like the backend's summary", () => {
    const s = pendingArgsSummary("web_search", `{"query": "${"a".repeat(200)}"}`)!;
    assert.ok(s.length <= 84, String(s.length));
    assert.ok(s.endsWith('…"'), s);
  });
});

describe("the stage's tools (2026-09-28)", () => {
  it("compose_screen and say_on_screen read in his words, live and failed", () => {
    const scene = describeTool("compose_screen");
    assert.equal(scene.title, "Set the scene on the screen");
    assert.equal(scene.live, "Setting the scene on the screen…");
    assert.equal(scene.failed, "Couldn't set the scene on the screen");
    const say = describeTool("say_on_screen");
    assert.equal(say.title, "Said something on the screen");
    assert.equal(say.failed, "Couldn't say something on the screen");
    assert.equal(say.icon, "speaker");
  });

  it("keeps set_screen_layout's label: old chat receipts still name it", () => {
    assert.ok(hasExplicitLabel("set_screen_layout"));
  });
});
