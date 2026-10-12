"use client";

import { averageLuminance, inkForLuminance } from "./clockInk";
import { BACKDROP_BRIGHTNESS, containRect, layerBehind } from "./photoLayout";

// The clock's corner, in normalised viewport coordinates. Only this patch
// is sampled — averaging a whole photo turns a bright sky and a dark
// treeline into mud and gets the corner wrong.
const CLOCK_REGION = { x0: 0.015, y0: 0.74, x1: 0.32, y1: 1.0 };

// Tiny raster: enough pixels to average, cheap enough to run on a Pi 4.
const W = 80;
const H = 45;

/**
 * Decide light or dark ink for the clock over a given photo.
 *
 * Reproduces `background-size: cover` when drawing so the sampled patch is
 * the part of the image actually behind the clock, not the raw top-left of
 * the file. Same-origin photos, so the canvas is never tainted.
 */
export async function inkForPhoto(src: string): Promise<"light" | "dark"> {
  const img = await loadImage(src);

  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return "light";

  // Slides draw the photo `contain` over a blurred, dimmed `cover` copy of
  // itself, so work out which of the two is actually behind the clock.
  const view = typeof window !== "undefined"
    ? { w: window.innerWidth, h: window.innerHeight }
    : { w: W, h: H };
  const fit = containRect(img.width, img.height, view.w, view.h);
  const clockCentre = {
    x: (CLOCK_REGION.x0 + CLOCK_REGION.x1) / 2,
    y: (CLOCK_REGION.y0 + CLOCK_REGION.y1) / 2,
  };
  const layer = layerBehind(clockCentre, fit);

  // Either way the pixels come from the same image drawn `cover` — the
  // blurred backdrop is a cover-scaled copy of the photo, and blur barely
  // moves a regional average. Only the dimming has to be applied.
  const scale = Math.max(W / img.width, H / img.height);
  const sw = W / scale;
  const sh = H / scale;
  const sx = (img.width - sw) / 2;
  const sy = (img.height - sh) / 2;
  ctx.drawImage(img, sx, sy, sw, sh, 0, 0, W, H);

  const rx = Math.floor(CLOCK_REGION.x0 * W);
  const ry = Math.floor(CLOCK_REGION.y0 * H);
  const rw = Math.max(1, Math.ceil((CLOCK_REGION.x1 - CLOCK_REGION.x0) * W));
  const rh = Math.max(1, Math.ceil((CLOCK_REGION.y1 - CLOCK_REGION.y0) * H));

  try {
    const { data } = ctx.getImageData(rx, ry, Math.min(rw, W - rx), Math.min(rh, H - ry));
    const lum = averageLuminance(data);
    return inkForLuminance(layer === "backdrop" ? lum * BACKDROP_BRIGHTNESS : lum);
  } catch {
    // Never let a sampling failure blank the clock.
    return "light";
  }
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}
