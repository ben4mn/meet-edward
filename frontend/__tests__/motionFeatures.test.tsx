// R/G red (2026-09-26 review): framer-motion's LazyMotion registers its
// features in one global registry. The shell loaded `domAnimation` (no drag,
// no layout), so until /chat's sidebar happened to load `domMax`, sheets
// could not be swiped down and the dock/header pills snapped instead of
// sliding — on every cold open of the PWA (start_url /today). The shell's
// motion components load MOTION_FEATURES (domMax) themselves.
//
// This file renders only the Sheet, in its own process (node --test runs
// each file separately), so nothing else can have loaded the features.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import React from "react";
import { act, render } from "@testing-library/react";
import { getFeatureDefinitions } from "motion-dom";

import { Sheet } from "../components/ui/Sheet.tsx";

const ROOT = path.resolve(import.meta.dirname, "..");

describe("motion features", () => {
  it("a Sheet on its own brings drag (swipe to dismiss) and layout", async () => {
    render(
      <Sheet open onClose={() => {}} ariaLabel="probe">
        <p>x</p>
      </Sheet>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    const defs = getFeatureDefinitions() as Record<string, { Feature?: unknown; ProjectionNode?: unknown } | undefined>;
    assert.ok(defs.drag?.Feature, "drag is registered");
    assert.ok(defs.layout?.ProjectionNode, "layout projection is registered");
  });

  it("every shell component that animates loads MOTION_FEATURES, never domAnimation", () => {
    for (const file of [
      "components/ui/Sheet.tsx",
      "components/ui/Switch.tsx",
      "components/chat/ThumbDock.tsx",
      "components/layout/DesktopHeader.tsx",
      "components/layout/AvatarMenu.tsx",
      // Integration pass: one source for every LazyMotion in the app.
      "components/chat/InputBar.tsx",
      "components/chat/WorkRail.tsx",
      "components/chat/ChatSidebar.tsx",
      "components/today/TodayView.tsx",
    ]) {
      const src = fs.readFileSync(path.join(ROOT, file), "utf8");
      assert.doesNotMatch(src, /\bdomAnimation\b/, `${file} still loads domAnimation`);
      assert.doesNotMatch(src, /features=\{domMax\}/, `${file} loads domMax directly`);
      assert.match(src, /features=\{MOTION_FEATURES\}/, `${file} loads MOTION_FEATURES`);
    }
  });

  it("with reduced motion the tab pills fade in place (no shared layoutId slide)", () => {
    for (const [file, id] of [
      ["components/chat/ThumbDock.tsx", "dock-pill"],
      ["components/layout/DesktopHeader.tsx", "nav-desktop-pill"],
    ]) {
      const src = fs.readFileSync(path.join(ROOT, file), "utf8");
      assert.match(src, new RegExp(`layoutId=\\{reduced \\? undefined : "${id}"\\}`), `${file}: no slide when reduced`);
    }
  });
});

describe("reduced motion in the chat sidebar", () => {
  it("the sidebar's width change does not animate with reduced motion", () => {
    const src = fs.readFileSync(path.join(ROOT, "components/chat/ChatSidebar.tsx"), "utf8");
    const aside = src.match(/"hidden flex-none overflow-hidden border-r[^"]*"/)?.[0] ?? "";
    assert.match(aside, /motion-reduce:transition-none/);
  });
});
