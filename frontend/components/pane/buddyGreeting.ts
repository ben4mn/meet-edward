// Canned one-liners for when the user pokes the Pane buddy. Tone borrowed
// from PaneStreet's click reactions — friendly, not chatty. Kept tiny so
// the buddy doesn't talk on every other tap.

export const BUDDY_GREETINGS: readonly string[] = [
  "Hey.",
  "Right here.",
  "What's up?",
  "Mm?",
  "Still here.",
  "Ready when you are.",
] as const;

export function pickBuddyGreeting(seed?: number): string {
  if (typeof seed === "number") {
    return BUDDY_GREETINGS[Math.abs(seed) % BUDDY_GREETINGS.length];
  }
  const idx = Math.floor(Math.random() * BUDDY_GREETINGS.length);
  return BUDDY_GREETINGS[idx];
}
