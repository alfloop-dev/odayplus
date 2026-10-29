// Shared path/locator operations stay independent of the lazy detail serializer.
export function intakePathSelection(pathname: string | null | undefined): string | null | undefined {
  const match = /^\/intake\/([^/]+)$/.exec(pathname ?? "");
  if (!match) return undefined;
  try { return decodeURIComponent(match[1]); } catch { return null; }
}

/** A different business target keeps filters, not the prior Intake locator. */
export function clearIntakeSelection(params: URLSearchParams): URLSearchParams {
  for (const key of ["selected", "dialog", "section", "field", "decision", "receipt", "compare", "intakeKind", "canonicalCursor"]) params.delete(key);
  return params;
}

export function intakeNavigationHash(hash: string): string {
  return hash.startsWith("#intake/") ? "" : hash;
}

