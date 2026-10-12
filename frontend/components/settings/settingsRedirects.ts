// Settings URLs that outlived their panel (old pushes, notes, bookmarks).
// Each goes to where that work lives now instead of a 404. Pure; pinned in
// __tests__/settingsNotFound.test.tsx.
//
//   evolution → wishlist   (the self-coding loop became his wishlist, 2026-09-12)
//   dreams    → dreaming   (memory consolidation moved to Memory)
//   wakeup    → thoughts   (the wake-up ticks became the mind's wakes)

const MOVED: Readonly<Record<string, string>> = {
  evolution: "/settings/wishlist",
  dreams: "/settings/dreaming",
  wakeup: "/settings/thoughts",
};

export function movedTo(slug: string): string | null {
  return Object.prototype.hasOwnProperty.call(MOVED, slug) ? MOVED[slug] : null;
}
