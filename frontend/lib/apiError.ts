/** Turn a FastAPI error payload into text a human can read. Tested. */

export function errorText(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const loc = Array.isArray((d as { loc?: unknown[] }).loc) ? (d as { loc: unknown[] }).loc.join(".") : "";
          return loc ? `${loc}: ${(d as { msg: string }).msg}` : (d as { msg: string }).msg;
        }
        return typeof d === "string" ? d : JSON.stringify(d);
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  if (detail && typeof detail === "object") {
    const msg = (detail as { message?: unknown; msg?: unknown }).message ?? (detail as { msg?: unknown }).msg;
    if (typeof msg === "string" && msg) return msg;
    return JSON.stringify(detail);
  }
  return fallback;
}
