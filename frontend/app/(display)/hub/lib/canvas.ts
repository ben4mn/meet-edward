/** Pure helpers for the hub canvas — Edward's expression surface. Tested in __tests__/canvas.test.ts. */

export type CanvasKind = "image" | "video" | "alert" | "text" | "html";

export interface CanvasContent {
  kind: CanvasKind;
  title: string | null;
  source: string | null;
  body: string | null;
  embed_url: string | null;
  shown_at: string;
  expires_at: string | null;
  shown_by: string;
}

export type Renderer = "img" | "iframe-video" | "video" | "alert" | "text" | "iframe-html";

export function rendererFor(c: CanvasContent): Renderer {
  switch (c.kind) {
    case "image":
      return "img";
    case "video":
      return c.embed_url ? "iframe-video" : "video";
    case "alert":
      return "alert";
    case "text":
      return "text";
    case "html":
      return "iframe-html";
  }
}

/** True once the content's own expiry has passed (the backend also drops it; this covers the poll gap). */
export function isExpired(c: CanvasContent, nowMs: number): boolean {
  if (!c.expires_at) return false;
  const t = Date.parse(c.expires_at);
  return Number.isFinite(t) && nowMs >= t;
}

/** Same content across polls → don't remount (a playing movie must not restart). */
export function contentKey(c: CanvasContent | null): string {
  return c ? `${c.kind}|${c.shown_at}|${c.source ?? ""}|${(c.body ?? "").length}` : "";
}
