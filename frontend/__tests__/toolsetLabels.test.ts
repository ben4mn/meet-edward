// R/G for the one toolset's labels (AREA 2, 2026-10-11).
//
// The backend collapsed ~116 tools into 14 (bash, the file editor, web_search,
// web_fetch, browser, chrome, look, messages, send_message, tell_owner, memory,
// schedule, screen, plan). The action tools (browser, chrome, messages,
// memory, schedule, screen, and the editor's commands) carry their action in
// the stream's args_summary as "{action}: {detail}", so the rail reads what
// Edward did ("Opened tickets.com in my browser", "Searched memory for
// “Biscuit's meds”", "Stood down in Family") — never "Browser" or "Memory".
// Old tool names keep their labels: old receipts in history still name them.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { describeTool, hasExplicitLabel, receiptPhrase, splitAction, tallyKey } from "../lib/toolLabels.ts";
import { EXECUTION_TOOLS, isOutwardTool } from "../lib/turnModel.ts";
import { codeToolName } from "../lib/workRail.ts";

const TOOLSET = [
  "bash", "str_replace_based_edit_tool", "web_search", "web_fetch", "browser", "chrome", "look",
  "messages", "send_message", "tell_owner", "memory", "schedule", "screen", "plan",
];

describe("every toolset name has a written label", () => {
  for (const name of TOOLSET) {
    it(name, () => assert.ok(hasExplicitLabel(name), name));
  }
});

describe("splitAction", () => {
  it("splits on the first colon-space only", () => {
    assert.deepEqual(splitAction("open: https://x.com/a:b"), { action: "open", detail: "https://x.com/a:b" });
    assert.deepEqual(splitAction('search: "a: b"'), { action: "search", detail: '"a: b"' });
    assert.deepEqual(splitAction("read"), { action: "read", detail: "" });
    assert.deepEqual(splitAction(""), { action: "", detail: "" });
    assert.deepEqual(splitAction(null), { action: "", detail: "" });
  });
  it("an action is one word; anything else is all detail", () => {
    assert.deepEqual(splitAction("Biscuit's meds"), { action: "", detail: "Biscuit's meds" });
    assert.deepEqual(splitAction("to: Parker"), { action: "", detail: "to: Parker" });
  });
});

describe("bash and the editor", () => {
  it("bash ran a command, with the command as its chip, and is an execution tool", () => {
    const d = describeTool("bash", '"pip install yt-dlp && yt-dlp --version"');
    assert.equal(d.title, "Ran a command");
    assert.equal(d.live, "Running a command…");
    assert.equal(d.icon, "terminal");
    assert.deepEqual(d.chips, ["pip install yt-dlp && yt-dlp --version"]);
    assert.ok(EXECUTION_TOOLS.has("bash"));
    const long = describeTool("bash", `"${"x".repeat(200)}"`);
    assert.ok(long.chips[0].length <= 61, String(long.chips[0].length));
    assert.equal(describeTool("bash").failed, "Couldn't run a command");
  });
  it("the editor reads, creates and edits a file by its name", () => {
    assert.equal(describeTool("str_replace_based_edit_tool", "view: /Users/me/edward-home/notes/plan.md").title, "Read plan.md");
    assert.equal(describeTool("str_replace_based_edit_tool", "create: ~/edward-home/bin/tix.py").title, "Created tix.py");
    assert.equal(describeTool("str_replace_based_edit_tool", "str_replace: projects/x/app.py").title, "Edited app.py");
    assert.equal(describeTool("str_replace_based_edit_tool", "insert: app.py").title, "Edited app.py");
    assert.equal(describeTool("str_replace_based_edit_tool").title, "Worked on a file");
  });
  it("a code block in bash is the bash tool's", () => {
    assert.equal(codeToolName("bash"), "bash");
    assert.equal(codeToolName("sh"), "bash");
    assert.equal(codeToolName("shell"), "bash");
    assert.equal(codeToolName("python"), "execute_code");
  });
});

describe("the web", () => {
  it("web_fetch reads a host; web_search keeps its quote", () => {
    assert.equal(describeTool("web_fetch", '"https://www.riversidefest.example/lineup"').title, "Read riversidefest.example");
    assert.equal(describeTool("web_fetch", "https://www.riversidefest.example/lineup").failed, "Couldn't read riversidefest.example");
    assert.equal(describeTool("web_search", '"riverside fest tickets"').title, "Searched “riverside fest tickets”");
  });
  it("his own browser and Ellis's Chrome read differently", () => {
    const mine = describeTool("browser", "open: https://tickets.example/riverside-fest");
    assert.equal(mine.title, "Opened tickets.example in my browser");
    assert.equal(mine.live, "Opening tickets.example in my browser…");
    assert.equal(mine.failed, "Couldn't open tickets.example in my browser");
    const yours = describeTool("chrome", "open: https://www.youtube.com/watch?v=x");
    assert.equal(yours.title, "Opened youtube.com in your Chrome");
    assert.equal(describeTool("chrome", 'type: "lofi piano"').title, "Typed in your Chrome");
    assert.equal(describeTool("chrome", "done").title, "Finished in your Chrome");
    assert.equal(describeTool("browser", "click: [3]").title, "Clicked in my browser");
    assert.equal(describeTool("browser", "download: [2]").title, "Downloaded a file");
    assert.equal(describeTool("browser").title, "Used my browser");
  });
  it("browser calls fold into one receipt phrase; opens count pages", () => {
    assert.equal(receiptPhrase("browser", ["open: https://a.com", "open: https://b.com"]), "opened 2 pages");
    assert.equal(tallyKey("browser", "click: [1]"), tallyKey("chrome", "read"));
    assert.notEqual(tallyKey("browser", "open: https://a.com"), tallyKey("browser", "click: [1]"));
  });
});

describe("look", () => {
  it("names what he looked at", () => {
    assert.equal(describeTool("look", '"screen"').title, "Looked at the screen");
    assert.equal(describeTool("look", "chrome").title, "Looked at your Chrome tab");
    assert.equal(describeTool("look", "browser").title, "Looked at my browser");
    assert.equal(describeTool("look", '"attachment:449321"').title, "Looked at a photo");
    assert.equal(describeTool("look", '"https://www.example.com/menu.pdf"').title, "Looked at example.com");
    assert.equal(describeTool("look", '"~/edward-home/downloads/report.pdf"').title, "Looked at report.pdf");
    assert.equal(describeTool("look").title, "Looked at something");
    assert.equal(describeTool("look", '"screen"').icon, "eye");
  });
});

describe("messages, send_message, tell_owner", () => {
  it("messages reads by action", () => {
    assert.equal(describeTool("messages", "recent: Parker").title, "Read messages with Parker");
    assert.equal(describeTool("messages", "recent").title, "Read recent messages");
    assert.equal(describeTool("messages", "thread: Family").title, "Read the thread with Family");
    assert.equal(describeTool("messages", "participants: Trivia crew").title, "Checked who's in Trivia crew");
    assert.equal(describeTool("messages", "who: Rowan").title, "Looked up Rowan");
    assert.equal(describeTool("messages", "stand_down: Family").title, "Stood down in Family");
    assert.equal(describeTool("messages").title, "Checked your messages");   // apple-mcp's old receipts
  });
  it("send_message texts someone and is outward", () => {
    const d = describeTool("send_message", "to: Parker");
    assert.equal(d.title, "Texted Parker");
    assert.equal(d.outward, true);
    assert.equal(receiptPhrase("send_message", ["to: Parker", "to: Jordan"]), "texted Parker and Jordan");
  });
  it("tell_owner leaves a note, or pings when urgent; both outward", () => {
    const note = describeTool("tell_owner", '"Biscuit\'s refill is due"');
    assert.equal(note.title, "Left you a note");
    assert.equal(note.outward, true);
    assert.deepEqual(note.chips, ["Biscuit's refill is due"]);
    assert.equal(describeTool("tell_owner", 'urgent: "Door is open"').title, "Pinged you");
    assert.ok(isOutwardTool("tell_owner"));
  });
});

describe("memory, schedule, screen, plan", () => {
  it("memory by action", () => {
    assert.equal(describeTool("memory", 'search: "Biscuit\'s meds"').title, "Searched memory for “Biscuit's meds”");
    assert.equal(describeTool("memory", 'remember: "Ellis likes the window seat"').title, "Remembered");
    assert.equal(describeTool("memory", "update: id: 42").title, "Updated a memory");
    assert.equal(describeTool("memory", 'close: "Briefed Theo"').title, "Closed an intention");
    assert.equal(describeTool("memory", 'why: "Parker\'s birthday"').title, "Traced where that came from");
    assert.equal(describeTool("memory", 'doc_edit: "Edward: state"').title, "Edited “Edward: state”");
    assert.equal(describeTool("memory", 'doc_write: "Trip notes"').title, "Saved “Trip notes”");
    assert.equal(describeTool("memory", "doc_read: id: 7").title, "Read a document");
    assert.equal(describeTool("memory", 'forget: "the old address"').title, "Looked at what to forget about the old address");
  });
  it("schedule by action", () => {
    assert.equal(describeTool("schedule", 'add: "Call the vet"').title, "Scheduled “Call the vet”");
    assert.equal(describeTool("schedule", "calendar").title, "Checked your calendar");
    assert.equal(describeTool("schedule", "list").title, "Checked what's scheduled");
    assert.equal(describeTool("schedule", "cancel: id: 3").title, "Cancelled a scheduled event");
    assert.equal(describeTool("schedule", 'wake: "Check the ticket prices"').title, "Set a time to look again");
  });
  it("screen by action", () => {
    assert.equal(describeTool("screen", "compose").title, "Set the scene on the screen");
    assert.equal(describeTool("screen", 'say: "Morning!"').title, "Said something on the screen");
    assert.equal(describeTool("screen", "show: house").title, "Put something on the screen");
    assert.equal(describeTool("screen", "clear").title, "Cleared the screen");
    assert.equal(describeTool("screen", "widget").title, "Updated your widget");
    assert.equal(describeTool("screen", "state").title, "Checked the screens");
    assert.equal(describeTool("screen", "bring_up").title, "Brought up the screen");
  });
  it("plan", () => {
    assert.equal(describeTool("plan").title, "Updated the plan");
    assert.equal(receiptPhrase("plan", [undefined, undefined]), "made a plan");
  });
});

describe("old names keep their labels (history)", () => {
  for (const old of ["browser_open", "execute_code", "execute_shell", "leave_note", "send_push_notification", "remember_search",
    "schedule_event", "look_at_screen", "fetch_page_content", "spawn_cc_worker", "create_hosted_page", "search_inbox"]) {
    it(old, () => assert.ok(hasExplicitLabel(old), old));
  }
});
