// Attachment previews: one object URL per file, made once, revoked when
// the file leaves the composer (2026-09-26). Pure — the create/revoke
// functions are passed in — so the rule is pinned without a DOM. Keyed by
// the File object itself: two photos both called IMG_0001.jpg are two
// previews.

export function syncObjectUrls<F extends object>(
  prev: ReadonlyMap<F, string>,
  files: readonly F[],
  create: (f: F) => string,
  revoke: (url: string) => void,
  wants: (f: F) => boolean = () => true,
): Map<F, string> {
  const next = new Map<F, string>();
  for (const f of files) {
    if (!wants(f) || next.has(f)) continue;
    next.set(f, prev.get(f) ?? create(f));
  }
  prev.forEach((url, f) => {
    if (!next.has(f)) revoke(url);
  });
  return next;
}
