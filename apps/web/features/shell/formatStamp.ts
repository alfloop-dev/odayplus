/** ISO to a stable, locale-independent stamp without hydration drift. */
export function formatStamp(iso: string): string {
  return iso.replace("T", " ").slice(0, 16) + " UTC";
}
