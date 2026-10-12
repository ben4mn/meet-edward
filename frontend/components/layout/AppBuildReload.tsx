"use client";

// The app's side of H3 (2026-09-27 follow-ups): reload onto a new build
// only while the owner is idle. Mounted inside the ChatProvider in the (app)
// layout, where the turn, live voice and the composer queue are readable;
// the composer itself holds reloads while it has text or attachments
// (useBuildReloadHold in InputBar). The rules: lib/buildReload.ts.

import { useChatPresence, useComposerQueue } from "@/lib/ChatContext";
import { isTurnLive } from "@/lib/turnModel";
import { hasDraftFiles } from "@/lib/draftFiles";
import { useBuildReload, type BuildReloadDeps } from "@/lib/buildReload";

export function AppBuildReload({ deps }: { deps?: BuildReloadDeps }) {
  const presence = useChatPresence();
  const { queued, anyQueued } = useComposerQueue();
  const turn = isTurnLive(presence?.turn);
  const voice = Boolean(presence?.isVoiceMode);
  // Every thread's queue, not only the one on screen: a queue left paused
  // in another thread lives only in memory too (2026-09-27 review).
  const waiting = anyQueued || queued.length > 0;
  // Asked at the moment of each check: attachments kept for a thread the
  // owner is not looking at, with no composer to hold them. Both they and the
  // queues are mirrored into the tab's stash too (lib/tabStash.ts), which is
  // what saves them from Next's own page load after a deploy; this hold
  // stays for a browser that keeps no site data.
  useBuildReload("app", () => ({ turn, voice, composer: waiting || hasDraftFiles() }), deps);
  return null;
}
