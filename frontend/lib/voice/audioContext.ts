// One AudioContext for the whole voice path. iOS only unlocks audio
// output inside a user gesture, and it unlocks per context, so the mic
// capture graph and the PCM player share this one and the view calls
// unlockAudio() from a tap when the context is still suspended.

let shared: AudioContext | null = null;

export function getSharedAudioContext(): AudioContext {
  if (!shared || shared.state === "closed") {
    const AC = (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext);
    shared = new AC();
  }
  return shared;
}

export async function unlockAudio(): Promise<boolean> {
  const ctx = getSharedAudioContext();
  if (ctx.state === "suspended") {
    try {
      await ctx.resume();
    } catch {
      /* stays suspended; the view shows a tap-to-enable prompt */
    }
  }
  return ctx.state === "running";
}

export function isAudioUnlocked(): boolean {
  return shared !== null && shared.state === "running";
}
