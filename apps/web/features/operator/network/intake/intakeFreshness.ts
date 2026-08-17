/** A snapshot older than 24 hours is stale for intake review. */
export function isSnapshotStale(capturedAt: string | null): boolean {
  if (!capturedAt) return false;
  const captured = Date.parse(capturedAt);
  if (Number.isNaN(captured)) return false;
  return Date.now() - captured > 24 * 60 * 60 * 1000;
}
