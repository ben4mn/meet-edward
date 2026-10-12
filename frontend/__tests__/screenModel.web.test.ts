// The screen is tiles plus overlays. Web content is not drawn in the page
// any more (2026-09-07, second pass): Edward opens a tab in Ellis's Chrome and
// goes full screen there, so the browser tile, the embed helpers and the
// screencast frame poller are gone.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import * as model from "../app/(display)/display/lib/screenModel";

describe("screen model without the browser tile", () => {
  it("exports no embed or screencast helpers", () => {
    for (const name of ["webEmbedUrl", "frameUrl", "staleClient"]) {
      assert.equal((model as Record<string, unknown>)[name], undefined, name);
    }
  });
  it("treats every show kind as a household renderer", () => {
    const renderers = new Set(["alert", "text", "img", "video", "iframe-video", "iframe-html"]);
    for (const kind of ["alert", "text", "image", "video", "html"]) {
      const s = { kind, body: "x", source: "https://x/y.mp4", title: null, embed_url: null, shown_at: "2026-09-07T20:00:00Z", expires_at: null, shown_by: "edward" } as never;
      assert.ok(renderers.has(model.showRenderer(s) as string), kind);
    }
  });
});
