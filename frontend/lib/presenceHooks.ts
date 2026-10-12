// Reading Edward's presence, apart from the provider (2026-09-26 perf pass).
// lib/presence.tsx owns EdwardPresenceProvider — which reads the chat turn,
// so it imports ChatContext — and re-exports these. A component that only
// reads presence (Today's hero) imports them from here and leaves the
// provider and ChatContext off its route's first load; the (app) layout
// mounts the provider either way.

import { useContext, useRef } from "react";
import type { EdwardPresence, PresenceBeat } from "./presence";
import { PresenceContext } from "./presenceContext";
import { deriveMood, statusLabel, statusLine, type Beat } from "./edwardMood";
import { presenceSignals } from "./presenceModel";

const noop = () => {};

/** Outside a provider (/login, a test): resting, online, no beats, nothing polled. */
function fallbackPresence(): EdwardPresence {
  const signals = presenceSignals({ chat: null, online: true, mindNow: null, hour: new Date().getHours() });
  return {
    signals,
    mood: deriveMood(signals),
    status: statusLine(signals),
    label: statusLabel(signals),
    beat: null,
    mindNow: null,
    online: true,
    available: false,
    refresh: noop,
  };
}

export function useEdwardPresence(): EdwardPresence {
  const context = useContext(PresenceContext);
  return context ?? fallbackPresence();
}

/**
 * The presence beat an avatar should play: only one that arrives while it
 * is active (the live one, the open sheet). A beat already current when the
 * avatar appears, or one that arrived while it was inactive (not live, the
 * sheet closed, the bar folded), is spent — never replayed (2026-09-26: the
 * morning wave, a wince or the "acted" sonar replayed on every sheet open,
 * Edward-tab visit and composer blur). Call it where the component stays
 * mounted (above an early return) so the memory survives an unmount below.
 * Pass `beatKey={beat?.key}` alongside so two nods in a row are two beats.
 */
export function useFreshBeat(beat: PresenceBeat | null, active: boolean): Beat | null {
  const seen = useRef(beat?.key ?? 0);
  // Idempotent under a double render: it only records what is current.
  if (!active) seen.current = beat?.key ?? 0;
  return active && beat && beat.key > seen.current ? beat.beat : null;
}
