// Slide advance for the photo screensaver.
//
// Two layers, alternating. The rule that keeps it from glitching:
//
//   **A photo in a visible slot is never changed.**
//
// New photos are written only into the hidden slot; visibility flips
// afterwards. The previous implementation derived both layers' images from
// a single `index`, so advancing swapped the outgoing photo at the same
// instant its opacity began to fall — the fade ran on the wrong picture
// and the album appeared to skip a frame.

export interface SlideshowState {
  /** slots[0] and slots[1] are the two stacked layers. */
  slots: [string | null, string | null];
  /** Which slot is currently on screen. */
  active: 0 | 1;
  /** Album position of the photo in the active slot. */
  index: number;
  /**
   * The slot holding the photo that just left the screen, if any.
   *
   * The two hidden states are not the same and the CSS has to tell them
   * apart: a slot that just went hidden must slide out to the LEFT, while
   * a slot parked with the upcoming photo waits off-screen RIGHT. Without
   * the distinction both slides travel the same direction and cross over
   * each other.
   */
  exiting: 0 | 1 | null;
}

const other = (a: 0 | 1): 0 | 1 => (a === 0 ? 1 : 0);

export function initSlideshow(photos: string[]): SlideshowState {
  return {
    slots: [photos[0] ?? null, photos[1] ?? null],
    active: 0,
    index: 0,
    exiting: null,
  };
}

export function advance(s: SlideshowState, photos: string[]): SlideshowState {
  // Nothing to cross-fade to.
  if (photos.length < 2) return s;

  const nextIndex = (s.index + 1) % photos.length;
  const nextActive = other(s.active);

  // The hidden slot already holds photos[nextIndex] (preloaded last time),
  // so flipping is enough to show it. The slot we are leaving keeps the
  // outgoing photo untouched so it can fade out correctly; the upcoming
  // photo is written there only once it is off screen.
  const slots: [string | null, string | null] = [...s.slots] as [
    string | null,
    string | null,
  ];
  slots[nextActive] = photos[nextIndex];

  return { slots, active: nextActive, index: nextIndex, exiting: s.active };
}

/**
 * The photo to write into the hidden slot *after* a transition settles.
 * Kept separate from `advance` so the component can delay it until the
 * fade has finished — writing it during the fade is what caused the
 * original glitch.
 */
export function queueUpcoming(s: SlideshowState, photos: string[]): SlideshowState {
  if (photos.length < 2) return s;
  const upcoming = photos[(s.index + 1) % photos.length];
  const hidden = other(s.active);
  if (s.slots[hidden] === upcoming) return { ...s, exiting: null };

  const slots: [string | null, string | null] = [...s.slots] as [
    string | null,
    string | null,
  ];
  slots[hidden] = upcoming;
  // No longer leaving — it is parked off-screen waiting to come in.
  return { ...s, slots, exiting: null };
}

export function visiblePhoto(s: SlideshowState): string | null {
  return s.slots[s.active];
}

export function hiddenPhoto(s: SlideshowState): string | null {
  return s.slots[other(s.active)];
}
