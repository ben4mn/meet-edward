// Pull to refresh (UX pass 2026-09-26): the finger's drag becomes the
// indicator's offset — half speed to the trigger, then resisting, then a
// ceiling. Pure; pinned in __tests__/todayPull.test.ts.

export const PULL_TRIGGER_PX = 64;
export const PULL_MAX_PX = 96;

export function pullOffset(dy: number): number {
  if (!(dy > 0)) return 0;
  let d = dy * 0.5;
  if (d > PULL_TRIGGER_PX) d = PULL_TRIGGER_PX + (d - PULL_TRIGGER_PX) * 0.4;
  return Math.min(PULL_MAX_PX, d);
}

export function pullProgress(offset: number): number {
  return Math.max(0, Math.min(1, offset / PULL_TRIGGER_PX));
}

export function releaseRefreshes(offset: number): boolean {
  return offset >= PULL_TRIGGER_PX;
}

// Review 2026-09-26 (ptr-pip-overlaps-greeting): while the refresh runs the
// content holds a gap at the top and the pip sits in it, instead of the
// content snapping back under a pip that then covered the greeting.

/** The gap the content keeps at the top while a refresh runs. */
export const PULL_HOLD_PX = 48;

/** How far down the content sits: the finger's offset, and while
 *  refreshing at least the hold gap. */
export function pullContentOffset(offset: number, refreshing: boolean): number {
  return refreshing ? Math.max(offset, PULL_HOLD_PX) : offset;
}

/** The pip's top: 36 px above the content's top edge (it is 32 px tall and
 *  the content has its own 16 px of padding), never above the scroller. */
export function pullPipY(offset: number, refreshing: boolean): number {
  return Math.max(4, pullContentOffset(offset, refreshing) - 36);
}
