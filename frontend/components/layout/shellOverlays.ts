// The shell's overlays — the presence sheet, the ⌘K palette, the sign-out
// confirm and the notification prompt — gathered into one async chunk
// (2026-09-26 perf pass). None of them is on screen at first paint, so
// ClientLayout loads them with lib/lazyComponent: the fetch starts as the
// layout's JS runs and they mount the moment it lands, a beat after
// hydration. Their open state lives in module stores (shellStores,
// presenceHero), so an overlay asked for before it mounted opens when it
// does. Reach this module only through `import()`.

export { PresenceSheet } from "./PresenceSheet";
export { CommandPalette } from "./CommandPalette";
export { SignOutSheet } from "./SignOutSheet";
export { NotificationPrompt } from "@/components/NotificationPrompt";
