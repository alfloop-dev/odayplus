import { readId, readObject } from "./networkReadValidation";
import { hasRecordedReviewReport, inspectReviewsReadProjection, isReviewPending } from "./networkReviewProjection";
import type { NetworkReviewsSnapshot, ReviewDecisionAction, ReviewDecisionForm, ReviewItem } from "./networkReviewTypes";

export type ReviewDecisionTarget = {
  expectedCandidateId: string; expectedCandidateDigest: string; expectedDecisionVersion: number;
  expectedReportId: string; expectedReportVersion: number; expectedApprovalId: string;
};
export type ReviewDecisionContext = {
  source: "canonical"; actorSubjectId: string; tenantId: string; canDecide: boolean;
  refusalReason: string | null; target: ReviewDecisionTarget;
};
export type BoundReviewDecisionBody = ReviewDecisionTarget & {
  decision: ReviewDecisionAction; reason: string; conditions: string; requiredData: string[]; overrideAck: boolean;
  expectedActorSubjectId: string; expectedTenantId: string;
};
const version = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value > 0;
export function hasReviewDecisionContext(value: unknown): value is ReviewDecisionContext {
  if (!readObject(value) || value.source !== "canonical" || !readId(value.actorSubjectId) || !readId(value.tenantId)
    || typeof value.canDecide !== "boolean" || !(value.refusalReason === null || typeof value.refusalReason === "string")
    || !readObject(value.target)) return false;
  const t = value.target;
  return readId(t.expectedCandidateId) && typeof t.expectedCandidateDigest === "string" && /^[0-9a-f]{64}$/.test(t.expectedCandidateDigest)
    && version(t.expectedDecisionVersion) && readId(t.expectedReportId) && version(t.expectedReportVersion) && readId(t.expectedApprovalId);
}
export function reviewDecisionContextMatches(context: ReviewDecisionContext, snapshot: NetworkReviewsSnapshot, row: ReviewItem): boolean {
  const t = context.target;
  const decisions = snapshot.decisions?.filter((d) => d.decision_id === row.id) ?? [];
  const approvals = snapshot.approvals?.filter((a) => a.reviewId === row.id) ?? [];
  return snapshot.source === "canonical" && inspectReviewsReadProjection(snapshot) === "ready" && isReviewPending(row)
    && hasRecordedReviewReport(row) && context.canDecide && t.expectedCandidateId === row.candidateId
    && t.expectedReportId === row.reportId && t.expectedReportVersion === row.reportVersion
    && decisions.length === 1 && decisions[0].version === t.expectedDecisionVersion && decisions[0].tenant_id === context.tenantId
    && decisions[0].approval_id === t.expectedApprovalId && approvals.length === 1
    && approvals[0].id === t.expectedApprovalId && approvals[0].recordSource === "persisted" && approvals[0].approvalStatus === "pending";
}
export function boundReviewDecisionBody(context: ReviewDecisionContext, action: ReviewDecisionAction, form: ReviewDecisionForm): BoundReviewDecisionBody {
  return { ...context.target, expectedActorSubjectId: context.actorSubjectId, expectedTenantId: context.tenantId,
    decision: action, reason: form.reason.trim(), conditions: form.conditions.trim(),
    requiredData: form.requiredData.split(/[、,]/).map((item) => item.trim()).filter(Boolean), overrideAck: form.overrideAck };
}
export function reviewDecisionReceiptMatches(value: unknown, id: string, body: BoundReviewDecisionBody, key: string, correlation: string): boolean {
  if (!readObject(value) || !readObject(value.review) || !readObject(value.records) || !readObject(value.commandContext)) return false;
  const r = value.review, c = value.commandContext, rows = value.records;
  return r.decision_id === id && r.candidate_site_id === body.expectedCandidateId && r.report_id === body.expectedReportId
    && r.report_version === body.expectedReportVersion && r.version === body.expectedDecisionVersion + 1
    && r.approval_id === body.expectedApprovalId && r.tenant_id === body.expectedTenantId && r.network_action === body.decision
    && rows.decisionId === id && rows.reviewId === id && rows.candidateId === body.expectedCandidateId
    && rows.approvalId === body.expectedApprovalId && readId(rows.auditId)
    && c.idempotencyKey === key && c.correlationId === correlation && c.actorSubjectId === body.expectedActorSubjectId
    && c.tenantId === body.expectedTenantId && readObject(c.request)
    && Object.keys(body).every((field) => JSON.stringify((c.request as Record<string, unknown>)[field]) === JSON.stringify((body as unknown as Record<string, unknown>)[field]));
}
export function reviewDecisionReadbackMatches(value: unknown, id: string, body: BoundReviewDecisionBody): value is NetworkReviewsSnapshot {
  if (!readObject(value) || value.source !== "canonical" || inspectReviewsReadProjection(value as NetworkReviewsSnapshot) !== "ready") return false;
  const snapshot = value as NetworkReviewsSnapshot;
  const rows = snapshot.reviews.filter((row) => row.id === id);
  const decisions = snapshot.decisions?.filter((row) => row.decision_id === id) ?? [];
  const row = rows[0], decision = decisions[0];
  return rows.length === 1 && decisions.length === 1 && Boolean(row && decision)
    && row.candidateId === body.expectedCandidateId && row.reportId === body.expectedReportId && row.reportVersion === body.expectedReportVersion
    && !isReviewPending(row) && row.decision?.decision === body.decision && row.decision.decidedBy === body.expectedActorSubjectId
    && row.decision.reason === body.reason && row.decision.approvalId === body.expectedApprovalId
    && decision.version === body.expectedDecisionVersion + 1 && decision.tenant_id === body.expectedTenantId
    && decision.network_action === body.decision && decision.conditions === body.conditions
    && JSON.stringify(decision.required_data) === JSON.stringify(body.requiredData) && decision.override_ack === body.overrideAck;
}
