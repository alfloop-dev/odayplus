const KNOWN_NON_PRODUCTION_SOURCES = new Set([
  "operator-shell-api-envelope",
  "r4",
  "seed-r4",
]);

const SOURCE_KEYS = new Set([
  "datamode",
  "datasource",
  "kind",
  "mode",
  "origin",
  "originkind",
  "source",
]);

const BOOLEAN_MARKER_KEYS = new Set([
  "fixture",
  "isfixture",
  "ismock",
  "isprototype",
  "isseed",
  "mock",
  "prototype",
  "seed",
]);

const NON_PRODUCTION_TOKEN =
  /(^|[^a-z0-9])(demo|fixture|mock|prototype|seed)([^a-z0-9]|$)/i;

function normalizedKey(value: string): string {
  return value.replace(/[_-]/g, "").toLowerCase();
}

export function isNonProductionDataSource(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const source = value.trim().toLowerCase();
  return (
    KNOWN_NON_PRODUCTION_SOURCES.has(source) ||
    NON_PRODUCTION_TOKEN.test(source)
  );
}

/**
 * Recursively inspect API payload metadata without scanning arbitrary business
 * copy. This blocks nested fixture markers while avoiding false positives such
 * as a legitimate "demographics-provider" source.
 */
export function payloadContainsNonProductionData(
  value: unknown,
  visited: WeakSet<object> = new WeakSet<object>(),
  metadataContext = false,
): boolean {
  if (Array.isArray(value)) {
    return value.some((item) =>
      payloadContainsNonProductionData(item, visited, metadataContext),
    );
  }

  if (!value || typeof value !== "object") return false;
  if (visited.has(value)) return false;
  visited.add(value);

  for (const [key, nestedValue] of Object.entries(
    value as Record<string, unknown>,
  )) {
    const markerKey = normalizedKey(key);
    const nestedMetadataContext =
      metadataContext || markerKey === "meta" || markerKey === "metadata";

    if (
      SOURCE_KEYS.has(markerKey) &&
      isNonProductionDataSource(nestedValue)
    ) {
      return true;
    }
    if (
      BOOLEAN_MARKER_KEYS.has(markerKey) &&
      (nestedValue === true || isNonProductionDataSource(nestedValue))
    ) {
      return true;
    }
    if (
      metadataContext &&
      markerKey === "description" &&
      isNonProductionDataSource(nestedValue)
    ) {
      return true;
    }
    if (
      payloadContainsNonProductionData(
        nestedValue,
        visited,
        nestedMetadataContext,
      )
    ) {
      return true;
    }
  }

  return false;
}

export function isEmptyApiPayload(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value).length === 0;
  return false;
}
