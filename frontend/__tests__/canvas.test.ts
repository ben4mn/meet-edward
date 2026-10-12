import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { contentKey, isExpired, rendererFor, type CanvasContent } from "../app/(display)/hub/lib/canvas.ts";

const base: CanvasContent = {
  kind: "text", title: null, source: null, body: "hi", embed_url: null,
  shown_at: "2026-09-02T23:30:00+00:00", expires_at: null, shown_by: "edward",
};

describe("rendererFor", () => {
  it("picks an iframe for YouTube and a video tag for direct files", () => {
    assert.equal(rendererFor({ ...base, kind: "video", embed_url: "https://www.youtube.com/embed/x" }), "iframe-video");
    assert.equal(rendererFor({ ...base, kind: "video", source: "https://a/b.mp4" }), "video");
  });
  it("maps the rest", () => {
    assert.equal(rendererFor({ ...base, kind: "image" }), "img");
    assert.equal(rendererFor({ ...base, kind: "alert" }), "alert");
    assert.equal(rendererFor({ ...base, kind: "html" }), "iframe-html");
  });
});

describe("isExpired", () => {
  it("never expires without expires_at", () => {
    assert.equal(isExpired(base, Date.now() + 1e9), false);
  });
  it("expires at the stamp", () => {
    const c = { ...base, expires_at: "2026-09-02T23:31:00+00:00" };
    assert.equal(isExpired(c, Date.parse("2026-09-02T23:30:59+00:00")), false);
    assert.equal(isExpired(c, Date.parse("2026-09-02T23:31:00+00:00")), true);
  });
});

describe("contentKey", () => {
  it("is stable for the same content and empty for none", () => {
    assert.equal(contentKey(base), contentKey({ ...base }));
    assert.notEqual(contentKey(base), contentKey({ ...base, shown_at: "2026-09-03T00:00:00+00:00" }));
    assert.equal(contentKey(null), "");
  });
});
