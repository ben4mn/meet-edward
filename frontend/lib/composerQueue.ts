// Messages the owner types while Edward is still working on a turn.
//
// Plan Phase 5, "Queue while running" (2026-09-24). The composer used to
// show Stop while a turn streamed and refuse anything typed, so the next
// thought had to wait in the owner's head. Now it waits in a queue: a chip above
// the box (removable), sent automatically in the same conversation when the
// current turn ends — done or errored — one at a time. A turn the owner stopped
// pauses the queue instead (Stop means stop, 2026-09-26 review): the chips
// stay and go when they tap "Send now".
//
// Per conversation, in memory, mirrored into the tab's stash
// (lib/tabStash.ts): it survives re-renders, switching threads and a page
// load (restored paused, pre-deploy review 2026-09-27). Pure so the rules
// are pinned without React; the ChatProvider owns the state and runs
// `nextOnDone` in an effect.

export const NEW_CHAT_KEY = "__new__";

export interface QueuedMessage<F = File> {
  id: string;
  text: string;
  files?: F[];
}

export type ComposerQueues<F = File> = Readonly<Record<string, readonly QueuedMessage<F>[]>>;

/** When a queued message may be sent: the turn is over and nothing else owns the thread. */
export interface DrainGate {
  isLoading: boolean;              // a turn is streaming
  isLoadingConversation: boolean;  // history is loading; the new message goes after it
  interrupted: boolean;            // a hidden tab cut the stream; the server may still be working
  voiceMode: boolean;              // live voice runs its own turns in this conversation
  /** The owner pressed Stop with messages queued: they wait for their word (2026-09-26 review). */
  paused?: boolean;
}

const EMPTY: readonly never[] = Object.freeze([]);

export function queueKey(conversationId: string | null | undefined): string {
  return conversationId ? conversationId : NEW_CHAT_KEY;
}

export function queuedFor<F>(queues: ComposerQueues<F>, key: string): readonly QueuedMessage<F>[] {
  return queues[key] ?? EMPTY;
}

function withQueue<F>(queues: ComposerQueues<F>, key: string, items: readonly QueuedMessage<F>[]): ComposerQueues<F> {
  const next: Record<string, readonly QueuedMessage<F>[]> = { ...queues };
  if (items.length > 0) next[key] = items;
  else delete next[key];
  return next;
}

function hasFiles<F>(m: QueuedMessage<F>): boolean {
  return Boolean(m.files && m.files.length > 0);
}

function sameText(a: string, b: string): boolean {
  return a.trim() === b.trim();
}

export function enqueue<F>(
  queues: ComposerQueues<F>,
  key: string,
  item: QueuedMessage<F>,
): { queues: ComposerQueues<F>; added: boolean } {
  if (!item.text.trim() && !hasFiles(item)) return { queues, added: false };
  const current = queuedFor(queues, key);
  const last = current[current.length - 1];
  // A double Enter (or a double tap on the queue button) is one message.
  if (last && !hasFiles(last) && !hasFiles(item) && sameText(last.text, item.text)) {
    return { queues, added: false };
  }
  return { queues: withQueue(queues, key, [...current, item]), added: true };
}

export function removeQueued<F>(queues: ComposerQueues<F>, key: string, id: string): ComposerQueues<F> {
  const current = queuedFor(queues, key);
  if (!current.some((m) => m.id === id)) return queues;
  return withQueue(queues, key, current.filter((m) => m.id !== id));
}

export function dequeue<F>(
  queues: ComposerQueues<F>,
  key: string,
): { queues: ComposerQueues<F>; item: QueuedMessage<F> | null } {
  const current = queuedFor(queues, key);
  if (current.length === 0) return { queues, item: null };
  return { queues: withQueue(queues, key, current.slice(1)), item: current[0] };
}

export function clearQueue<F>(queues: ComposerQueues<F>, key: string): ComposerQueues<F> {
  if (!(key in queues)) return queues;
  return withQueue(queues, key, EMPTY);
}

/** A brand-new chat learns its id from the first stream event; its queue follows. */
export function rekeyQueue<F>(queues: ComposerQueues<F>, from: string, to: string): ComposerQueues<F> {
  const moving = queuedFor(queues, from);
  if (moving.length === 0 || from === to) return queues;
  const moved = withQueue(queues, to, [...queuedFor(queues, to), ...moving]);
  return withQueue(moved, from, EMPTY);
}

/** Queues kept from before a page load (lib/tabStash.ts) put back beside
 *  the ones made since: what was saved first, each message once (by id),
 *  empty queues left out. */
export function mergeQueues<F>(current: ComposerQueues<F>, saved: ComposerQueues<F>): ComposerQueues<F> {
  let out = current;
  for (const [key, items] of Object.entries(saved)) {
    if (!Array.isArray(items) || items.length === 0) continue;
    const now = queuedFor(out, key);
    const have = new Set(now.map((m) => m.id));
    const back = items.filter((m) => m && typeof m.id === "string" && typeof m.text === "string" && !have.has(m.id));
    if (back.length) out = withQueue(out, key, [...back, ...now]);
  }
  return out;
}

/**
 * The turn on screen has ended (done, stopped or error): take the next
 * queued message for this conversation, or nothing while anything still
 * owns the thread.
 */
export function nextOnDone<F>(
  queues: ComposerQueues<F>,
  key: string,
  gate: DrainGate,
): { queues: ComposerQueues<F>; item: QueuedMessage<F> | null } {
  if (gate.isLoading || gate.isLoadingConversation || gate.interrupted || gate.voiceMode || gate.paused) {
    return { queues, item: null };
  }
  return dequeue(queues, key);
}

function attachments(n: number): string {
  return `${n} attachment${n === 1 ? "" : "s"}`;
}

export function queueChipLabel<F>(m: QueuedMessage<F>): string {
  const text = m.text.replace(/\s+/g, " ").trim();
  const n = m.files?.length ?? 0;
  if (!text) return attachments(n);
  return n > 0 ? `${text} (+${attachments(n)})` : text;
}
