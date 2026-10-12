// Pure helpers for "is this panel still fresh?" — used to render the
// small grey 'stale Xs ago' badge in panel headers when polls have
// been failing or paused.

const STALE_MULTIPLIER = 2.5;

export function isStale(
  lastSuccessAt: number | null,
  intervalMs: number,
  now: number = Date.now(),
): boolean {
  if (lastSuccessAt === null) return false; // still on first poll — not stale
  return now - lastSuccessAt > intervalMs * STALE_MULTIPLIER;
}

export function staleAgeLabel(
  lastSuccessAt: number | null,
  now: number = Date.now(),
): string | null {
  if (lastSuccessAt === null) return null;
  const ageMs = now - lastSuccessAt;
  if (ageMs < 5_000) return null;
  if (ageMs < 60_000) return `${Math.round(ageMs / 1_000)}s ago`;
  if (ageMs < 60 * 60_000) return `${Math.round(ageMs / 60_000)}m ago`;
  return `${Math.round(ageMs / (60 * 60_000))}h ago`;
}
