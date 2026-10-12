// Settings → Memory → Files (2026-09-26). Pure; pinned in
// __tests__/storageView.test.ts.

import { formatRelative } from "./time";

export function formatFileSize(bytes: number): string {
  if (!bytes || bytes < 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const v = bytes / Math.pow(1024, i);
  return `${i === 0 ? v : parseFloat(v.toFixed(1))} ${units[i]}`;
}

export type FileKind = "image" | "audio" | "video" | "pdf" | "data" | "text" | "other";

function ext(filename: string | null | undefined): string {
  const m = (filename || "").toLowerCase().match(/\.([a-z0-9]+)$/);
  return m ? m[1] : "";
}

export function fileKind(mime: string, filename?: string | null): FileKind {
  const m = (mime || "").toLowerCase();
  const e = ext(filename);
  if (m.startsWith("image/")) return "image";
  if (m.startsWith("audio/")) return "audio";
  if (m.startsWith("video/")) return "video";
  if (m === "application/pdf" || e === "pdf") return "pdf";
  if (m === "text/csv" || m === "application/json" || ["csv", "json", "xlsx", "tsv"].includes(e)) return "data";
  if (m.startsWith("text/") || ["md", "txt", "log"].includes(e)) return "text";
  return "other";
}

export function fileKindLabel(mime: string, filename?: string | null): string {
  const kind = fileKind(mime, filename);
  const e = ext(filename);
  switch (kind) {
    case "image": {
      const sub = (mime.split("/")[1] || e || "").replace("svg+xml", "svg").toUpperCase();
      return sub ? `${sub} image` : "Image";
    }
    case "audio":
      return "Audio";
    case "video":
      return "Video";
    case "pdf":
      return "PDF";
    case "data":
      return (e || mime.split("/")[1] || "data").toUpperCase();
    case "text":
      return mime.includes("markdown") || e === "md" ? "Markdown" : "Text";
    default:
      return e ? e.toUpperCase() : "File";
  }
}

export type FileFilter = "all" | "images" | "docs" | "audio";

export function fileMatches(file: { mime_type: string; filename: string }, filter: FileFilter): boolean {
  if (filter === "all") return true;
  const k = fileKind(file.mime_type, file.filename);
  if (filter === "images") return k === "image";
  if (filter === "audio") return k === "audio" || k === "video";
  return k === "pdf" || k === "text" || k === "data";
}

export function madeBy(source: string | null | undefined): "you" | "edward" {
  return source === "user" || source === "upload" ? "you" : "edward";
}

export function fileMeta(
  f: { size_bytes: number; mime_type: string; filename: string; source: string; created_at: string | null },
  opts: { now?: Date; tz?: string } = {},
): string {
  const parts = [formatFileSize(f.size_bytes), fileKindLabel(f.mime_type, f.filename), madeBy(f.source) === "you" ? "from you" : "made by Edward"];
  if (f.created_at) parts.push(formatRelative(f.created_at, opts));
  return parts.join(" · ");
}
