// N8, reworded 2026-09-26 (review: interrupted-answer-jargon). When the
// SSE stream errors, the half answer Edward managed is kept exactly as he
// said it — no "_(stream interrupted)_" in the markdown and no canned
// "Sorry, I encountered an error" — and the message carries `wasCutOff`,
// which the bubble renders as its own warn notice, in his voice, with
// Retry. A refusal the server worded for Ellis is still his answer.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { contentForStreamError } from "../lib/streamReducer.ts";

describe("contentForStreamError", () => {
  it("nothing streamed: no canned line in the answer (the notice speaks)", () => {
    assert.equal(contentForStreamError({ streamed: "" }), "");
    assert.equal(contentForStreamError({ streamed: "   \n " }), "");
  });

  it("keeps streamed content exactly as it came, no marker in the markdown", () => {
    assert.equal(contentForStreamError({ streamed: "Yeah, sure — I'll check United for" }), "Yeah, sure — I'll check United for");
  });

  it("an old marker from before is taken out, not doubled", () => {
    assert.equal(contentForStreamError({ streamed: "I tried but...\n\n_(stream interrupted)_" }), "I tried but...");
  });
});

describe("contentForStreamError with a refusal (2026-09-25)", () => {
  it("shows the server's sentence when nothing was streamed", () => {
    assert.equal(
      contentForStreamError({ streamed: "", refusal: "Edward is still writing this entry — open it again in a moment." }),
      "Edward is still writing this entry — open it again in a moment.",
    );
  });

  it("still keeps a partial reply over a refusal", () => {
    assert.equal(contentForStreamError({ streamed: "Partial", refusal: "nope" }), "Partial");
  });
});
