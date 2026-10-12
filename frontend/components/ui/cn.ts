// `cn` for the design system: clsx + a tailwind-merge that knows the
// calm-presence tokens (docs/design-system-2026-09-25.md §2).
//
// Stock tailwind-merge does not know `text-body` is a font size, so it
// treats it as a colour and `cn("text-body", "text-text-primary")` silently
// drops the size. The same goes for `shadow-e1` (read as a shadow colour)
// and `duration-fast` (not a number). Teach it the scale.

import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

export const TYPE_SCALE = [
  "overline",
  "caption",
  "footnote",
  "body",
  "callout",
  "title3",
  "title2",
  "title1",
  "display",
] as const;

export const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: [...TYPE_SCALE] }],
      shadow: [{ shadow: ["e1", "e2", "e3", "glow"] }],
      duration: [{ duration: ["press", "fast", "base", "slow"] }],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
