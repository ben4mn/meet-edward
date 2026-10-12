// The chat URL is the source of truth for which conversation is on
// screen. This decides what the page does when the URL and the state
// disagree — on first load, on a sidebar tap (which pushes a URL), and on
// the browser's back/forward.

export type ChatRouteAction = "load" | "skip" | "new";

export function decideChatRoute({
  cid,
  currentId,
}: {
  /** The ?c= param, or null when the URL is bare /chat. */
  cid: string | null;
  /** The conversation the context currently holds. */
  currentId: string | undefined;
}): ChatRouteAction {
  if (cid) return cid === currentId ? "skip" : "load";
  return currentId ? "new" : "skip";
}

// /chat?voice=1 opens live voice (the home-screen "Talk live" shortcut,
// 2026-09-26). Read once, then dropped from the URL so back or a reload
// does not open the mic again.

export function readVoiceParam(params: { get(name: string): string | null } | null | undefined): boolean {
  const v = (params?.get("voice") ?? "").trim().toLowerCase();
  return v === "1" || v === "true";
}

/** The query string without ``voice`` ("?c=abc", or "" when empty). */
export function withoutVoiceParam(search: string): string {
  const params = new URLSearchParams((search ?? "").replace(/^\?/, ""));
  params.delete("voice");
  const rest = params.toString();
  return rest ? `?${rest}` : "";
}
