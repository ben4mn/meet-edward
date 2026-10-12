// A settings panel's sub-view frame (review 2026-09-26, nested-subview-back)
// — pure, pinned in __tests__/settingsPages.test.tsx. A sub-view has one
// way back, to its parent view ("‹ Listening", "‹ Documents"), not the
// room, and one title: the sub-view's (a document's own heading is its h1,
// so the page draws none). {} = the panel's own frame.

export interface PanelFrame {
  /** Replaces the panel title; null = no header (the view has its own h1). */
  title?: string | null;
  back?: { href: string; label: string };
}

type Params = { get(name: string): string | null } | null | undefined;

export function panelFrame(slug: string, params: Params): PanelFrame {
  if (slug === "heartbeat") {
    const view = params?.get("view");
    const back = { href: "/settings/heartbeat", label: "Listening" };
    if (view === "log") return { title: "Full log", back };
    if (view === "config") return { title: "Configuration", back };
    return {};
  }
  if (slug === "documents" && params?.get("id")) {
    return { title: null, back: { href: "/settings/documents", label: "Documents" } };
  }
  return {};
}
