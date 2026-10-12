// When the phone must not sleep. Why (2026-09-07): the iPhone PWA
// dimmed and slept mid-turn while Edward was still working in a chat, and
// the stream died with the screen. Hold the lock only while a turn is
// running on a visible page — never on an idle chat, never in the background.

export function shouldHoldWakeLock(input: { working: boolean; visible: boolean }): boolean {
  return input.working && input.visible;
}
