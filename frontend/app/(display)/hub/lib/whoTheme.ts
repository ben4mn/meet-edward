// Per-person theming for the hub.
//
// The household shares one screen and the who-toggle is a small control in
// a corner. Tinting the whole panel makes the active person readable at a
// glance from across the room — the failure this guards against is one
// person logging a pet's dose under someone else's name because they
// didn't notice the toggle.
//
// Themes go by position in the household (EDWARD_HOUSEHOLD, owner first),
// not by name: the first person gets the first palette, and so on.
//
// Only the IDENTITY accent changes. Status colours (good / late / missed /
// repaired) stay fixed across themes: a colour that means "repaired" must
// not mean something else depending on who is standing at the screen.

export type ThemeSlug = "first" | "second" | "third";

/** One palette per position, in household order (hub.css: [data-who]). */
export const THEMES: readonly ThemeSlug[] = ["first", "second", "third"];

/** Used for anyone past the last palette or not in the household — a pet
 * sitter should still get a usable panel rather than one missing its
 * accent entirely. */
export const DEFAULT_THEME: ThemeSlug = "first";

export function themeFor(who: string, people: readonly string[]): ThemeSlug {
  if (!who || typeof who !== "string" || !Array.isArray(people)) return DEFAULT_THEME;
  // Values round-trip through localStorage, so normalise before matching.
  const key = who.trim().toLowerCase();
  const index = people.findIndex((p) => typeof p === "string" && p.trim().toLowerCase() === key);
  return index >= 0 && index < THEMES.length ? THEMES[index] : DEFAULT_THEME;
}
