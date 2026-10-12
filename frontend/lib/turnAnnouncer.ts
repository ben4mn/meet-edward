// What a screen reader hears about a chat turn (review 2026-09-26,
// aria-live-turn-chatter) — pure, pinned in __tests__/turnAnnouncer.test.tsx.
// One polite region in the chat view says each coarse phase once
// ("Thinking", "Working", "Writing" — never a tool, a query or a count),
// then how the turn ended: "Edward replied: <first sentence>" or
// "Edward stopped: …". The work rail's status is visual only.

import { isTerminalPhase, type Turn } from "./turnModel";

export type CoarsePhase = "Thinking" | "Working" | "Writing";

export function coarsePhase(turn: Turn | null | undefined): CoarsePhase | null {
  if (!turn || isTerminalPhase(turn.phase)) return null;
  switch (turn.phase) {
    case "working":
      return "Working";
    case "writing":
      return "Writing";
    default:
      return turn.tools.running > 0 ? "Working" : "Thinking";
  }
}

const MAX = 140;

/** The answer's first sentence, without markdown, at most 140 characters. */
export function firstSentence(text: string, max = MAX): string {
  const plain = (text ?? "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[*_`#>|~]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!plain) return "";
  const m = /^.+?[.!?…](?=\s|$)/.exec(plain);
  const sentence = (m ? m[0] : plain).trim();
  if (sentence.length <= max) return sentence;
  return sentence.slice(0, max - 1).replace(/\s+\S*$/, "").trimEnd() + "…";
}

/** How the turn ended, once; null while it is still running. */
export function endAnnouncement(turn: Turn | null | undefined, finalText: string): string | null {
  if (!turn || !isTerminalPhase(turn.phase)) return null;
  switch (turn.phase) {
    case "done": {
      const first = firstSentence(finalText);
      return first ? `Edward replied: ${first}` : "Edward replied.";
    }
    case "stopped":
      return "Edward stopped: you stopped me.";
    default:
      return turn.exhausted ? "Edward stopped: I ran out of steps." : "Edward stopped: I couldn't finish that.";
  }
}
