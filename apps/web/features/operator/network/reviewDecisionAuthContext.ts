import { readId, readObject } from "./networkReadValidation";

export type ReviewDecisionIdentity = { actorSubjectId: string; tenantId: string; expiresAt: number };
export function hasCurrentReviewDecisionIdentity(value: unknown): value is ReviewDecisionIdentity {
  return readObject(value) && readId(value.actorSubjectId) && readId(value.tenantId)
    && typeof value.expiresAt === "number" && Number.isSafeInteger(value.expiresAt)
    && value.expiresAt > Math.floor(Date.now() / 1000);
}
/** Durable identity.sessions actor/tenant, never display persona or storage. */
export async function readReviewDecisionIdentity(isCurrent: () => boolean): Promise<ReviewDecisionIdentity | null> {
  try {
    if (!isCurrent()) return null;
    const response = await fetch("/auth/operator-context", { cache: "no-store", headers: { accept: "application/json" } });
    if (!isCurrent()) return null;
    const value: unknown = await response.json();
    if (!isCurrent() || response.status !== 200 || !hasCurrentReviewDecisionIdentity(value)) return null;
    return value;
  } catch { return null; }
}
