// Fitting a photo to the screensaver frame.
//
// A good share of a family album is portrait. `cover` on a 16:9 panel crops a
// tall photo to a middle band, which on family photos means cutting heads
// off. So each slide draws the photo `contain` (whole photo, always) over
// a blurred, dimmed `cover` copy of itself that fills the leftover margins
// — the Apple TV / Google Photos treatment.
//
// That gives the clock's ink sampler two possible backdrops, so it has to
// know which one is actually behind the clock.

/** The blurred backdrop is dimmed; ink sampled off it must be scaled. */
export const BACKDROP_BRIGHTNESS = 0.6;

export interface Rect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/**
 * Where a `contain`-fitted image lands in the frame, in normalised
 * viewport coordinates (0..1). Centred, preserving aspect.
 */
export function containRect(iw: number, ih: number, vw: number, vh: number): Rect {
  if (iw <= 0 || ih <= 0 || vw <= 0 || vh <= 0) {
    return { x0: 0, y0: 0, x1: 1, y1: 1 };
  }

  const scale = Math.min(vw / iw, vh / ih);
  const w = (iw * scale) / vw; // as a fraction of the viewport
  const h = (ih * scale) / vh;

  return {
    x0: (1 - w) / 2,
    y0: (1 - h) / 2,
    x1: (1 + w) / 2,
    y1: (1 + h) / 2,
  };
}

/** Which layer sits under a given point — the sharp photo, or the blur. */
export function layerBehind(
  pt: { x: number; y: number },
  r: Rect,
): "photo" | "backdrop" {
  const inside = pt.x >= r.x0 && pt.x <= r.x1 && pt.y >= r.y0 && pt.y <= r.y1;
  return inside ? "photo" : "backdrop";
}
