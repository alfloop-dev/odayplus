type Row = Record<string, unknown>;
const readObject = (value: unknown): value is Row => typeof value === "object" && value !== null && !Array.isArray(value);
const readId = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;

export function hasCanonicalScoreSession(value: unknown): value is { subject: string; expiresAt: number } {
  return readObject(value) && readId(value.subject) && typeof value.expiresAt === "number"
    && Number.isSafeInteger(value.expiresAt) && value.expiresAt > Math.floor(Date.now() / 1000);
}
