// The voice view's wiring (integration pass 2026-09-26). The view needs a
// socket, a mic and an AudioContext to render, so these pin the source:
//  - Under the presence provider an avatar no longer perks on its own (the
//    provider sends the one perk as a beat) — but the voice hero reads no
//    presence beats, so it keeps its own return perk while it is the live one.
//  - Hold to talk shows a keyboard focus ring (review: weak-focus-indicators).

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../components/chat/ContinuousVoiceView.tsx", import.meta.url), "utf8");

describe("ContinuousVoiceView wiring", () => {
  it("the hero Edward perks on return while he is the live one", () => {
    const hero = src.match(/<EdwardAvatar\s[^>]*size=\{156\}[^>]*\/>/s)?.[0] ?? "";
    assert.notEqual(hero, "", "the 156px hero avatar");
    assert.match(hero, /perkOnReturn=\{heroLive\}/);
  });

  it("Hold to talk has a visible keyboard focus ring", () => {
    const i = src.indexOf('aria-label="Hold to talk"');
    assert.notEqual(i, -1);
    const cls = src.slice(i, i + 900);
    assert.match(cls, /focus-visible:ring-2/);
  });
});
