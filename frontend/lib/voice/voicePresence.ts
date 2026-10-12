// Edward in the live voice view (2026-09-26 chat chrome). Pure: the voice
// machine's state in, his mood, the beat to play, and his one-line status
// out. Pinned in __tests__/voicePresence.test.ts.
//
// The animation is a claim (design system §1.2): he listens only while
// the mic is really open (hands-free, or the button held), thinks while
// the turn runs, works while a tool runs, speaks while his audio plays —
// including the tail of playback after the server has moved back to
// listening. An error is a wince (a beat), not a mood; a dropped link is
// offline. A handed-off job finishing is an act.

import type { Beat, Mood } from "../edwardMood";
import type { VoicePhase, VoiceToolActivity, VoiceViewState } from "./voiceMachine";

/** What the person sees: playback can outlive the server's "listening". */
export function visiblePhase(s: Pick<VoiceViewState, "phase" | "awaitingPlaybackEnd">): VoicePhase {
  if (s.phase === "listening" && s.awaitingPlaybackEnd) return "speaking";
  return s.phase;
}

function runningTools(list: readonly VoiceToolActivity[]): number {
  let n = 0;
  for (const a of list) n += a.status === "started" ? 1 : -1;
  return Math.max(0, n);
}

function runningTool(list: readonly VoiceToolActivity[]): VoiceToolActivity | null {
  if (runningTools(list) === 0) return null;
  for (let i = list.length - 1; i >= 0; i--) if (list[i].status === "started") return list[i];
  return null;
}

export function voiceMood(s: VoiceViewState): Mood {
  if (s.phase === "error") return s.lostConnection ? "offline" : "idle";
  switch (visiblePhase(s)) {
    case "speaking":
      return "speak";
    case "thinking":
      return runningTools(s.toolActivity) > 0 ? "work" : "think";
    case "listening":
      return s.mode === "ptt" && !s.pttHeld ? "idle" : "listen";
    default:
      return "idle";
  }
}

export function voiceBeat(prev: VoiceViewState, next: VoiceViewState): Beat | null {
  if (next.phase === "error" && prev.phase !== "error") return "wince";
  // Only the server saying a handed-off job is done is an act; a reconnect
  // (or the session ending) clearing the line is not (2026-09-26 review).
  if (next.handoffsDone > prev.handoffsDone) return "acted";
  if (next.toolActivity.length > prev.toolActivity.length && next.toolActivity.length > 0) {
    const fresh = next.toolActivity.slice(prev.toolActivity.length);
    if (fresh.some((a) => a.status === "error")) return "wince";
    if (fresh.some((a) => a.status === "completed")) return "nod";
  }
  if (prev.phase === "connecting" && next.phase === "listening") return "perk";
  return null;
}

function humanTool(name: string): string {
  return `Using ${name.replace(/[_-]+/g, " ").trim()}`;
}

/** His status line, first person and short. */
export function voiceStatus(s: VoiceViewState): string {
  switch (s.phase) {
    case "connecting":
      return "Getting ready…";
    case "listening":
      if (s.awaitingPlaybackEnd) return "Speaking";
      return s.mode === "ptt" && !s.pttHeld ? "Hold to talk" : "I'm listening";
    case "thinking": {
      const tool = runningTool(s.toolActivity);
      if (tool) return `${(tool.message || humanTool(tool.tool_name)).replace(/[.…]+$/, "")}…`;
      return "Thinking…";
    }
    case "speaking":
      return "Speaking";
    case "error":
      // L14 (2026-09-27): "Something went wrong" was neither his voice nor
      // what happened. A mic that would not start (useMicStream's
      // micErrorMessage) is "I can't hear you"; the Notice below carries
      // the specific line and the Try again button sits under it.
      if (s.lostConnection) return "I lost the connection";
      if (/\bmic(rophone)?\b/i.test(s.errorMessage ?? "")) return "I can't hear you";
      return "I lost the voice line — tap to try again";
    default:
      return "Not connected";
  }
}

const ATTACK = 0.55;
const RELEASE = 0.12;
const FLOOR = 0.002;

/** One step of a meter: fast attack, slow release, clamped to 0..1. */
export function smoothLevel(prev: number, target: number): number {
  const t = Math.min(1, Math.max(0, Number.isFinite(target) ? target : 0));
  const k = t > prev ? ATTACK : RELEASE;
  const v = prev + (t - prev) * k;
  return v < FLOOR ? 0 : Math.min(1, v);
}
