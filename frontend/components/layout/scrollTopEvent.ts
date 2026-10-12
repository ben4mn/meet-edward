// The dock's "scroll back to the top" window event, on its own so a page
// that listens for it (Today) does not carry the nav config (2026-09-26 perf
// pass). ./navItems re-exports it.

/** Window event: "scroll this page back to the top" (the active tab, tapped). */
export const SCROLL_TOP_EVENT = "edward:scroll-top";
