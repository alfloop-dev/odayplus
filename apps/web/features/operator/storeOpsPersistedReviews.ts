import { compareNativeInstants } from "./interventions/interventionContract";

/** Saved manual submissions, never measurement, model, provider-delivery or current-principal proof. */
export type StoredReviewSource = {
  id: string; issueId: string; storeId: string;
  actorSubjectId: string | null; actorName: string; actorRoleId: string;
  actorEvidence: "authenticated_principal" | "caller_supplied";
  recordedAt: string; correlationId: string; workflowAuditId: string; auditEventId: string;
  sourceIssueUpdatedAt: string; evidenceIds: string[];
};
export type OutcomeReviewSubmission = {
  outcome: "effective" | "ineffective" | "inconclusive";
  impactSummary: string; evidenceSummary: string; closeIssue: boolean;
  followUpTarget: "storeOps" | "growth" | "network" | "govern" | null;
  followUpAction: string | null;
};
export type ReplyReviewSubmission = {
  channel: "google" | "customerService"; decision: "approve" | "return" | "reject";
  draftReply: string; reviewerNote: string | null; publishAfterApproval: boolean;
};
export type OutcomeReviewRecord = StoredReviewSource & { submission: OutcomeReviewSubmission };
export type ReplyReviewRecord = StoredReviewSource & { submission: ReplyReviewSubmission; deliveryStatus: "not_sent" };
export type ReviewHistory<T extends StoredReviewSource> =
  | { status: "absent"; latest: null }
  | { status: "unavailable"; latest: null }
  | { status: "available"; latest: T };
type ObjectValue = Record<string, unknown>;
const object = (v: unknown): v is ObjectValue => !!v && typeof v === "object" && !Array.isArray(v);
// The actual Pydantic/Rust submission normalizer uses Unicode White_Space.
// It preserves C0 U+001C–001F and FEFF, and strips NEL; neither Python nor JS strip is equivalent.
const nativeStrip = (v: string) => v.replace(/^[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g, "");
// Source identities have separate native Python .strip() guards; preserve them.
const sourceStrip = (v: string) => v.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g, "");
const text = (v: unknown): v is string => typeof v === "string" && sourceStrip(v).length > 0;
const submissionText = (v: unknown): v is string => typeof v === "string" && nativeStrip(v).length > 0;
const enumValue = (v: unknown, values: string[]): v is string => typeof v === "string" && values.includes(v);
const nullableText = (v: unknown): v is string | null => v === null || typeof v === "string";
const exact = (v: ObjectValue, keys: string[]) => Object.keys(v).length === keys.length && keys.every(key => Object.hasOwn(v, key));
const sourceKeys = ["id", "issueId", "storeId", "actorSubjectId", "actorName", "actorRoleId", "actorEvidence", "recordedAt", "correlationId", "workflowAuditId", "auditEventId", "sourceIssueUpdatedAt", "evidenceIds", "submission"];
const outcomeKeys = ["outcome", "impactSummary", "evidenceSummary", "closeIssue", "followUpTarget", "followUpAction"];
const replyKeys = ["channel", "decision", "draftReply", "reviewerNote", "publishAfterApproval"];

function source(v: ObjectValue, owner: { id: string; storeId: string }): boolean {
  return v.issueId === owner.id && v.storeId === owner.storeId && text(v.id) && v.id === v.workflowAuditId
    && text(v.auditEventId) && text(v.correlationId) && typeof v.actorName === "string" && text(v.actorRoleId)
    && compareNativeInstants(v.recordedAt, v.recordedAt) === 0 && compareNativeInstants(v.sourceIssueUpdatedAt, v.sourceIssueUpdatedAt) === 0
    && Array.isArray(v.evidenceIds) && v.evidenceIds.every(text)
    && (v.actorEvidence === "authenticated_principal" ? text(v.actorSubjectId)
      : v.actorEvidence === "caller_supplied" && v.actorSubjectId === null);
}
function outcome(v: unknown): v is OutcomeReviewSubmission {
  if (!object(v) || !exact(v, outcomeKeys) || !enumValue(v.outcome, ["effective", "ineffective", "inconclusive"])
    || !submissionText(v.impactSummary) || typeof v.evidenceSummary !== "string" || typeof v.closeIssue !== "boolean"
    || !(v.followUpTarget === null || enumValue(v.followUpTarget, ["storeOps", "growth", "network", "govern"])) || !nullableText(v.followUpAction)) return false;
  return v.outcome === "effective" || (v.closeIssue === false && text(v.followUpTarget) && submissionText(v.followUpAction));
}
function reply(v: unknown): v is ReplyReviewSubmission {
  return object(v) && exact(v, replyKeys) && enumValue(v.channel, ["google", "customerService"])
    && enumValue(v.decision, ["approve", "return", "reject"]) && submissionText(v.draftReply) && nullableText(v.reviewerNote)
    && typeof v.publishAfterApproval === "boolean" && (v.decision === "approve" || submissionText(v.reviewerNote))
    && (!v.publishAfterApproval || v.decision === "approve");
}
function latest<T extends StoredReviewSource>(owner: { id: string; storeId: string }, value: unknown, kind: "outcome" | "reply"): ReviewHistory<T> {
  if (value === undefined) return { status: "absent", latest: null };
  if (!Array.isArray(value)) return { status: "unavailable", latest: null };
  if (!value.length) return { status: "absent", latest: null };
  const seen = new Set<string>();
  // The native repository verifies aggregate audit digests. Here validate the entire delivered
  // history and owner; never skip a corrupt newest record or sort timestamps into invented order.
  for (const record of value) {
    if (!object(record) || !exact(record, kind === "reply" ? [...sourceKeys, "deliveryStatus"] : sourceKeys)
      || !source(record, owner) || seen.has(String(record.id))
      || !(kind === "reply" ? record.deliveryStatus === "not_sent" && reply(record.submission) : outcome(record.submission))) {
      return { status: "unavailable", latest: null };
    }
    seen.add(String(record.id));
  }
  return { status: "available", latest: value[value.length - 1] as T };
}
export function latestOutcomeReview(issue: { id: string; storeId: string; outcomeReviews?: unknown }): ReviewHistory<OutcomeReviewRecord> {
  return latest(issue, issue.outcomeReviews, "outcome");
}
export function latestReplyReview(issue: { id: string; storeId: string; replyReviews?: unknown }): ReviewHistory<ReplyReviewRecord> {
  return latest(issue, issue.replyReviews, "reply");
}

function normalizedSubmission(kind: "outcome" | "replyReview", payload: ObjectValue): unknown {
  const trim = (v: unknown) => typeof v === "string" ? nativeStrip(v) : v;
  return kind === "outcome" ? {
    outcome: payload.outcome, impactSummary: trim(payload.impactSummary), evidenceSummary: trim(payload.evidenceSummary), closeIssue: payload.closeIssue,
    followUpTarget: payload.followUpTarget ?? null, followUpAction: payload.followUpAction === undefined ? null : trim(payload.followUpAction),
  } : {
    channel: payload.channel, decision: payload.decision, draftReply: trim(payload.draftReply),
    reviewerNote: payload.reviewerNote === undefined ? null : trim(payload.reviewerNote), publishAfterApproval: payload.publishAfterApproval,
  };
}
function canonicalJSON(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonicalJSON).join(",") + "]";
  if (object(value)) return "{" + Object.keys(value).sort().map(key => JSON.stringify(key) + ":" + canonicalJSON(value[key])).join(",") + "}";
  return JSON.stringify(value);
}
/** Validate the accepted operation, not whichever record happens to be latest after replay. */
export async function confirmsSavedReview(body: unknown, kind: "outcome" | "replyReview", payload: ObjectValue, requestCorrelationId: string, idempotencyKey: string): Promise<boolean> {
  if (!object(body) || !object(body.issue) || body.issue.id !== payload.issueId || body.issue.storeId !== payload.storeId
    || typeof body.idempotentReplay !== "boolean" || !object(body.auditEvent)) return false;
  const owner = { id: String(payload.issueId), storeId: String(payload.storeId) };
  const history = kind === "outcome" ? latestOutcomeReview({ ...owner, outcomeReviews: body.issue.outcomeReviews })
    : latestReplyReview({ ...owner, replyReviews: body.issue.replyReviews });
  if (history.status !== "available") return false;
  const rows = (kind === "outcome" ? body.issue.outcomeReviews : body.issue.replyReviews) as Array<OutcomeReviewRecord | ReplyReviewRecord>;
  const audit = body.auditEvent, metadata = audit.metadata;
  const found = rows.filter(row => row.workflowAuditId === audit.id);
  if (found.length !== 1 || audit.targetType !== "issue" || audit.targetId !== owner.id
    || audit.action !== (kind === "outcome" ? "issue.outcome" : "issue.reply-review") || !object(metadata)
    || metadata.issueId !== owner.id || metadata.workflowAuditId !== audit.id || metadata.idempotencyKey !== idempotencyKey) return false;
  const record = found[0];
  if (record.id !== audit.id || record.actorName !== audit.actorName || record.actorRoleId !== audit.actorRoleId
    || record.auditEventId === record.workflowAuditId || (!body.idempotentReplay && record.correlationId !== requestCorrelationId)
    || canonicalJSON(record.submission) !== canonicalJSON(normalizedSubmission(kind, payload))) return false;
  // Digest is a delivered-record consistency check; native scoped repository owns audit authority.
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalJSON(record)));
  const actual = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
  return metadata.reviewRecordHash === actual;
}

// Reuse the admitted normalization/identity/serialization guards for other native manual records.
export { nativeStrip as nativeSubmissionStrip, source as validStoredReviewSource, canonicalJSON as canonicalStoredReviewJSON };
