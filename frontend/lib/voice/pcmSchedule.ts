// Where the next PCM chunk should start on the AudioContext timeline so
// playback is gapless while the queue is ahead, and recovers cleanly
// after an underrun instead of scheduling into the past.

export function nextStartTime(args: { now: number; scheduledUntil: number; headroom: number }): number {
  const { now, scheduledUntil, headroom } = args;
  return scheduledUntil > now ? scheduledUntil : now + headroom;
}
