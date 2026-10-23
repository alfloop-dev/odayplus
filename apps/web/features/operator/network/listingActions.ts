import type { Listing } from "../types";
import type { OperatorRoleId } from "../navigation";
import { canMergeListing } from "./listingPermissions";

export type ListingWriteContext = { ready: boolean; fixturesAllowed: boolean; candidates: unknown };
export type ListingAction = {
  kind: "merge" | "archive";
  sourceListingId: string;
  targetListingId?: string;
  sourceLabel: string;
  targetLabel?: string;
  sourceTenantId?: string | null;
  targetTenantId?: string | null;
  ifMatch?: string;
  targetIfMatch?: string;
  fingerprint: string;
};

function revision(record: Listing, fixturesAllowed: boolean): string | null | undefined {
  if (record.version == null && fixturesAllowed) return undefined;
  return typeof record.version === "number" && Number.isSafeInteger(record.version) && record.version > 0
    ? `W/"${record.version}"` : null;
}

function retained(record: Listing, context: ListingWriteContext): boolean {
  if (record.status === "archived" || record.mergedIntoId || record.candidateId) return true;
  const holds = [record.hasLegalHold, record.legalHold];
  if (holds.some((value) => value === true || value != null && typeof value !== "boolean")) return true;
  if (!context.fixturesAllowed && !holds.some((value) => typeof value === "boolean")) return true;
  if (!Array.isArray(context.candidates) || context.candidates.some((row) => !row || typeof row !== "object"
    || typeof row.id !== "string" || !row.id || typeof row.listingId !== "string" || !row.listingId)) return true;
  return context.candidates.some((row) => row.listingId === record.id);
}

/** The recorded duplicate target is the only target; array order is not authority. */
export function resolveListingAction(
  listingId: string, listings: Listing[], activeRoleId: OperatorRoleId, context?: ListingWriteContext,
): ListingAction | null {
  if (!context?.ready || !canMergeListing(activeRoleId) || !Array.isArray(listings)) return null;
  const matches = listings.filter((row) => row && row.id === listingId);
  if (matches.length !== 1) return null;
  const source = matches[0];
  if (retained(source, context) || !Array.isArray(source.hardRuleFailures)
    || source.hardRuleFailures.some((value) => typeof value !== "string" || !value)) return null;
  const ifMatch = revision(source, context.fixturesAllowed);
  if (ifMatch === null) return null;
  const base = { sourceListingId: source.id, sourceLabel: source.address, sourceTenantId: source.tenantId, ifMatch };
  if (source.duplicateOfId) {
    const targets = listings.filter((row) => row && row.id === source.duplicateOfId);
    if (targets.length !== 1 || targets[0].id === source.id || targets[0].duplicateOfId || retained(targets[0], context)) return null;
    const target = targets[0];
    const targetIfMatch = revision(target, context.fixturesAllowed);
    if (targetIfMatch === null || source.tenantId && target.tenantId && source.tenantId !== target.tenantId) return null;
    return { ...base, kind: "merge", targetListingId: target.id, targetLabel: target.address, targetTenantId: target.tenantId, targetIfMatch,
      fingerprint: JSON.stringify([source, target, context.candidates]) };
  }
  return source.hardRuleFailures.length ? { ...base, kind: "archive",
    fingerprint: JSON.stringify([source, context.candidates]) } : null;
}

export type ListingReceiptExpectation = {
  actorSubjectId: string | null;
  idempotencyKey: string;
  fixturesAllowed: boolean;
  riskSummary?: string;
  riskAcknowledged?: boolean;
};

function receiptObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}
function receiptVersion(value: unknown, ifMatch: string | undefined, fixturesAllowed: boolean): boolean {
  if (ifMatch === undefined) return fixturesAllowed && value == null;
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 && `W/"${value}"` === ifMatch;
}

/** Admit only the actual producer receipt for the frozen operator intent. */
export function listingWriteReceiptMatches(
  action: ListingAction, receipt: unknown, reason: string, expected: ListingReceiptExpectation,
): boolean {
  if (!receiptObject(receipt) || !expected || !expected.actorSubjectId && !expected.fixturesAllowed
    || !expected.idempotencyKey || receipt.idempotencyKey !== expected.idempotencyKey) return false;
  const audit = receipt.auditEvent;
  if (!receiptObject(audit) || typeof audit.id !== "string" || !audit.id || audit.action !== `listing.${action.kind}`
    || audit.targetId !== (action.kind === "merge" ? action.targetListingId : action.sourceListingId)
    || typeof receipt.actorSubjectId !== "string" || !receipt.actorSubjectId.trim()
    || audit.actorName !== receipt.actorSubjectId || expected.actorSubjectId && receipt.actorSubjectId !== expected.actorSubjectId
    || typeof receipt.actorRoleId !== "string" || !receipt.actorRoleId.trim() || audit.actorRoleId !== receipt.actorRoleId
    || !receiptObject(audit.metadata) || audit.metadata.reason !== reason
    || !receiptObject(audit.metadata.before) || !receiptObject(audit.metadata.after)) return false;
  const metadata = audit.metadata;
  const before = metadata.before; const after = metadata.after;
  if (!receiptObject(before) || !receiptObject(after)) return false;
  const version = (value: unknown, match: string | undefined) => receiptVersion(value, match, expected.fixturesAllowed);
  if (action.kind === "archive") {
    const listing = receipt.listing;
    return receiptObject(listing) && listing.id === action.sourceListingId && listing.status === "archived"
      && listing.archivedReason === reason && version(listing.version, action.ifMatch)
      && (!action.sourceTenantId || listing.tenantId === action.sourceTenantId)
      && version(before.version, action.ifMatch) && version(after.version, action.ifMatch);
  }
  const source = receipt.source; const target = receipt.target;
  return receiptObject(source) && receiptObject(target) && source.id === action.sourceListingId && target.id === action.targetListingId
    && source.status === "duplicate" && source.mergedIntoId === action.targetListingId && source.mergeReason === reason
    && (!action.sourceTenantId || source.tenantId === action.sourceTenantId) && (!action.targetTenantId || target.tenantId === action.targetTenantId)
    && metadata.sourceListingId === action.sourceListingId && metadata.targetListingId === action.targetListingId
    && expected.riskAcknowledged === true && metadata.riskAcknowledged === true
    && typeof expected.riskSummary === "string" && expected.riskSummary.trim().length > 0 && metadata.riskSummary === expected.riskSummary
    && version(source.version, action.ifMatch) && version(target.version, action.targetIfMatch)
    && version(before.sourceVersion, action.ifMatch) && version(after.sourceVersion, action.ifMatch)
    && version(before.targetVersion, action.targetIfMatch) && version(after.targetVersion, action.targetIfMatch);
}

/** Retry is the existing key's receipt lookup, never a fresh terminal mutation. */
export function listingWriteTerminalMatches(action: ListingAction, listings: Listing[], reason: string): boolean {
  const sources = listings.filter((row) => row.id === action.sourceListingId);
  if (sources.length !== 1) return false;
  const source = sources[0] as Listing & { archivedReason?: unknown; mergeReason?: unknown };
  if (action.kind === "archive") return source.status === "archived" && source.archivedReason === reason;
  return listings.filter((row) => row.id === action.targetListingId).length === 1
    && source.status === "duplicate" && source.mergedIntoId === action.targetListingId && source.mergeReason === reason;
}
