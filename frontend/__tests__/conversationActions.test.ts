// R/G red: renaming a conversation from the list (lib/conversationActions.ts).
// PATCH /api/conversations/{id} {title} exists on the backend; the list's
// "⋯" menu uses it. A title is trimmed, never empty, never a novel.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { MAX_TITLE_CHARS, cleanTitle, renameConversation } from "../lib/conversationActions.ts";

describe("cleanTitle", () => {
  it("trims and collapses whitespace", () => {
    assert.equal(cleanTitle("  Cabin   weekend \n"), "Cabin weekend");
  });
  it("empty means no rename", () => {
    assert.equal(cleanTitle("   "), null);
  });
  it("caps the length", () => {
    assert.equal(cleanTitle("x".repeat(500))!.length, MAX_TITLE_CHARS);
  });
});

describe("renameConversation", () => {
  it("PATCHes the title with credentials, id encoded", async () => {
    const calls: [string, RequestInit][] = [];
    const fake = async (url: string, init: RequestInit) => {
      calls.push([url, init]);
      return new Response(JSON.stringify({ id: "a b", title: "New" }), { status: 200 });
    };
    const out = await renameConversation("a b", "  New ", fake as typeof fetch);
    assert.equal(out.title, "New");
    assert.equal(calls[0][0], "/api/conversations/a%20b");
    assert.equal(calls[0][1].method, "PATCH");
    assert.equal(calls[0][1].credentials, "include");
    assert.deepEqual(JSON.parse(String(calls[0][1].body)), { title: "New" });
  });
  it("refuses an empty title without calling the API", async () => {
    let called = false;
    await assert.rejects(renameConversation("a", "  ", (async () => ((called = true), new Response("{}"))) as typeof fetch));
    assert.equal(called, false);
  });
  it("a server error is an error", async () => {
    await assert.rejects(
      renameConversation("a", "x", (async () => new Response("{}", { status: 404 })) as typeof fetch),
      /couldn't rename/i,
    );
  });
});
