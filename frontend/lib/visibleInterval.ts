// A poll that rests while the tab is hidden (review 2026-09-26,
// pollers-not-visibility-gated): ticks are skipped while hidden, and one
// catch-up runs when the page comes back. Pinned in
// __tests__/visibleInterval.test.ts. Returns the stop function.

interface VisibilityDoc {
  visibilityState: string;
  addEventListener(type: "visibilitychange", fn: () => void): void;
  removeEventListener(type: "visibilitychange", fn: () => void): void;
}

export function setVisibleInterval(fn: () => void, ms: number, doc?: VisibilityDoc): () => void {
  const d: VisibilityDoc | undefined = doc ?? (typeof document === "undefined" ? undefined : (document as VisibilityDoc));
  const id = setInterval(() => {
    if (d?.visibilityState !== "hidden") fn();
  }, ms);
  const onVisible = () => {
    if (d?.visibilityState === "visible") fn();
  };
  d?.addEventListener("visibilitychange", onVisible);
  return () => {
    clearInterval(id);
    d?.removeEventListener("visibilitychange", onVisible);
  };
}
