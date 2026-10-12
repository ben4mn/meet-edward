// Phone focus mode: while the composer on /chat has focus, the header and
// the bottom dock fold away so the keyboard plus the transcript get the
// screen. Desktop is unaffected (the callers gate on md:hidden).

export function shouldHideChrome(pathname: string, composing: boolean): boolean {
  if (!composing) return false;
  const path = pathname.split("?")[0];
  return path === "/chat";
}

export type PromptPlacement = "above-dock" | "top" | "hidden";

/** Where the notification prompt sits on the phone: never over the dock or
 *  the composer. Chat keeps its composer just above the dock, so there the
 *  prompt goes under the presence bar; while the owner is typing it waits. (On md+
 *  it is always top-right under the header — CSS, not this.) */
export function promptPlacement(pathname: string, composing: boolean): PromptPlacement {
  if (composing) return "hidden";
  const path = pathname.split("?")[0].split("#")[0];
  return path === "/chat" || path.startsWith("/chat/") ? "top" : "above-dock";
}

/** Only the layout owns the bottom inset (design system §5). The shell sets
 *  `--shell-inset-bottom` to this: nothing while the dock (which carries the
 *  home-indicator padding) is shown, the home indicator while it is folded
 *  away — so the composer pads by the variable and never doubles it. */
export function shellBottomInset(dockShown: boolean): string {
  return dockShown ? "0px" : "max(env(safe-area-inset-bottom), 8px)";
}
