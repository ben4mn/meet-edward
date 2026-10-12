// Pure mapping from Edward's behavior state to the Pane SVG's activity
// class. CSS in globals.css owns the actual animation per class; this
// module just decides which class to slap on the root SVG.

import type { Gaze, Mood } from "@/lib/edwardMood";

export type EdwardState =
  | "idle"
  | "thinking"
  | "speaking"
  | "listening"
  | "looking-left"
  | "looking-right"
  | "looking-up";

export type PaneActivityClass =
  | "act-stand"
  | "act-think"
  | "act-speak"
  | "act-listen"
  | "act-look";

export function stateToActivityClass(state: EdwardState): PaneActivityClass {
  switch (state) {
    case "thinking":
      return "act-think";
    case "speaking":
      return "act-speak";
    case "listening":
      return "act-listen";
    case "looking-left":
    case "looking-right":
    case "looking-up":
      return "act-look";
    case "idle":
    default:
      return "act-stand";
  }
}

// ---------------------------------------------------------------------------
// The same states as moods and gazes (lib/edwardMood.ts) — how /screen's
// Edward is drawn now. The act-* class stays for CSS that still reads it.
// ---------------------------------------------------------------------------

export function stateToMood(state: EdwardState): Mood {
  switch (state) {
    case "thinking":
      return "think";
    case "speaking":
      return "speak";
    case "listening":
      return "listen";
    default:
      return "idle";
  }
}

export function stateToGaze(state: EdwardState): Gaze | null {
  switch (state) {
    case "looking-left":
      return "left";
    case "looking-right":
      return "right";
    case "looking-up":
      return "up";
    default:
      return null;
  }
}
