// 44pt targets on the phone (design system §1.7, review 2026-09-26:
// verdict-and-chip-targets-36px). A control may be 36px tall on touch only
// when an ::after extends its hit area to 44 (`after:-inset-y-1`); md+ may
// shrink to 36/32 for the pointer. The Segment primitive is 44 on touch,
// so no caller may squeeze its buttons back to 36 on the phone.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const FILES = [
  "components/settings/MindPanel.tsx",
  "components/settings/HeartbeatDigest.tsx",
  "components/settings/DocumentBrowser.tsx",
  "components/settings/CodexUsageCard.tsx",
  "components/today/DidSection.tsx",
];

const src = (f: string) => readFileSync(new URL(`../${f}`, import.meta.url), "utf8");

/** Every class string (a "…" literal) that sets a bare 36px min-height. */
function bare36(text: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(/"([^"\n]*)"/g)) {
    const cls = m[1];
    if (/(^|\s)(\[&>button\]:)?min-h-\[36px\]/.test(cls)) out.push(cls);
  }
  return out;
}

describe("touch targets are 44 on the phone", () => {
  for (const f of FILES) {
    it(`${f}: a 36px control on touch has a 44px hit area`, () => {
      for (const cls of bare36(src(f))) {
        assert.equal(/\[&>button\]:min-h-\[36px\]/.test(cls), false, `a Segment squeezed to 36 on touch in ${f}: ${cls}`);
        assert.equal(/after:-inset-y-1/.test(cls), true, `no 44px hit area in ${f}: ${cls}`);
      }
    });
  }
});
