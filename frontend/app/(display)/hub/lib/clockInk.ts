// Adaptive clock colour for the photo screensaver.
//
// The clock sits over arbitrary family photos. White-on-snow and
// black-on-night are both unreadable, so the overlay samples only the
// region of the image actually behind the clock and picks its ink from
// that region's luminance. Sampling the whole photo would average a bright
// sky and a dark treeline into mud and get the corner wrong.

/** Above this, the backdrop is bright enough to need dark ink. */
export const LUMINANCE_SWITCH = 0.55;

/**
 * Perceived brightness, 0..1, using the sRGB coefficients — green carries
 * most of the perceived brightness, blue almost none.
 *
 * Deliberately the simple (non-gamma-expanded) form: this picks between
 * two ink colours, not a contrast ratio for compliance, and the cheap
 * version is stable enough while running per frame on a Pi 4.
 */
export function relativeLuminance(r: number, g: number, b: number): number {
  const l = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  // Guards against out-of-range channel values; luminance outside 0..1 is
  // meaningless. (Note this does NOT make white exactly 1 — the sRGB
  // coefficients sum to 0.9999999999999999 — so compare white approximately.)
  return Math.min(1, Math.max(0, l));
}

/** Mean luminance of an RGBA buffer, skipping transparent pixels. */
export function averageLuminance(data: Uint8ClampedArray): number {
  let sum = 0;
  let counted = 0;

  for (let i = 0; i < data.length; i += 4) {
    // A transparent pixel has no colour to read; counting it as black
    // would drag a bright sample down and flip the ink for no reason.
    if (data[i + 3] === 0) continue;
    sum += relativeLuminance(data[i], data[i + 1], data[i + 2]);
    counted += 1;
  }

  if (counted === 0) return 0.5;
  return sum / counted;
}

export function inkForLuminance(l: number): "light" | "dark" {
  // Ties go to light: there is a dark scrim under the clock, so light ink
  // is the safer side of a coin-flip.
  return l > LUMINANCE_SWITCH ? "dark" : "light";
}
