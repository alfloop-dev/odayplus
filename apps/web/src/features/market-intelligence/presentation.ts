export function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Missing";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return "Missing";
}

export function stringList(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.filter((item): item is string => typeof item === "string");
}
