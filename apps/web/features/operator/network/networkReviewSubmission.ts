import type { OperatorRoleId } from "../navigation";
import { hasCandidateReviewSubmission, hasNetworkListingsRenderShape, hasNetworkScoringRenderShape, readId, readObject } from "./networkReadValidation";
import { inspectReviewsReadProjection } from "./networkReviewProjection";
import type { CandidateNativeWorkflowReads, NetworkScoringSnapshot } from "./networkScoringTypes";
import type { ReviewSubmissionOffer } from "./networkReviewSubmissionTypes";
import type { NetworkReviewsSnapshot } from "./networkReviewTypes";
import { hasApprovedReviewHistory } from "./networkReviewRounds";
import { hasCurrentPendingSubmissionHistory } from "./networkPendingReviewHistory";
import type { ReviewSubmissionTarget, ReviewSubmissionContext, ReviewSubmissionBody, ReviewSubmissionReceipt } from "./networkReviewSubmissionTypes";

const version = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value > 0;
const keys = (value: Record<string, unknown>, names: string[]) => names.every((name) => readId(value[name]));
export function hasReviewSubmissionContext(value: unknown): value is ReviewSubmissionContext {
  if (!readObject(value) || value.source !== "canonical"
    || !keys(value, ["candidateId", "listingId", "addressId", "tenantId", "actorSubjectId"])
    || typeof value.canSubmit !== "boolean" || value.blockCode !== (value.canSubmit ? null : "ALREADY_PENDING")
    || !readObject(value.target) || !Array.isArray(value.reviewerGroups) || !value.reviewerGroups.length) return false;
  const target = value.target;
  const previous = value.previousReview;
  if (previous !== undefined && (!readObject(previous)
    || !keys(previous, ["decisionId", "approvalId", "reportId", "modelVersion", "tenantId"])
    || !version(previous.decisionVersion) || !version(previous.reportVersion)
    || previous.approvalId === previous.decisionId || previous.tenantId !== value.tenantId
    || previous.approvalStatus !== "approved" && previous.approvalStatus !== "rejected"
    || previous.reportId === target.expectedReportId || !version(target.expectedReportVersion)
    || previous.reportVersion >= target.expectedReportVersion)) return false;
  const pending = value.pendingReview;
  if (value.canSubmit ? pending !== null : !readObject(pending)
    || !keys(pending, ["decisionId", "approvalId", "reportId", "latestReportId", "originalCreatorSubjectId"])
    || !version(pending.decisionVersion) || !version(pending.reportVersion) || !version(pending.latestReportVersion)
    || pending.approvalId === pending.decisionId || typeof pending.originalCreatorVerified !== "boolean"
    || !["verifiedSubmitterSubjectId", "submissionSummary", "reviewerGroupId"].every((key) => pending[key] === null || readId(pending[key]))
    || pending.decisionId !== target.decisionId || pending.decisionVersion !== target.expectedDecisionVersion
    || pending.reportId !== target.expectedReportId || pending.reportVersion !== target.expectedReportVersion
    || pending.latestReportId !== target.expectedLatestReportId || pending.latestReportVersion !== target.expectedLatestReportVersion) return false;
  return keys(target, ["expectedReportId", "expectedLatestReportId", "expectedCandidateState"])
    && version(target.expectedReportVersion) && version(target.expectedLatestReportVersion)
    && (target.decisionId === null && target.expectedDecisionVersion === null
      || readId(target.decisionId) && version(target.expectedDecisionVersion))
    && (target.expectedCandidateReviewId === null || readId(target.expectedCandidateReviewId))
    && (target.expectedCandidateReviewId === null || target.expectedCandidateReviewId === target.decisionId
      || target.decisionId === null && target.expectedDecisionVersion === null && value.canSubmit === true
      && ["approved", "opened"].includes(String(target.expectedCandidateState))
      && readObject(previous) && previous.decisionId === target.expectedCandidateReviewId
      && target.expectedReportId === target.expectedLatestReportId
      && target.expectedReportVersion === target.expectedLatestReportVersion)
    && typeof target.expectedCandidateDigest === "string" && /^[0-9a-f]{64}$/.test(target.expectedCandidateDigest)
    && value.reviewerGroups.every((group) => readObject(group) && keys(group, ["id", "label"])
      && group.source === "shared.auth.rbac.ROLE_PERMISSIONS")
    && new Set(value.reviewerGroups.map((group) => group.id)).size === value.reviewerGroups.length;
}

export function reviewSubmissionTargetsMatch(left: ReviewSubmissionTarget, right: ReviewSubmissionTarget): boolean {
  return (Object.keys(left) as (keyof ReviewSubmissionTarget)[]).every((key) => left[key] === right[key])
    && (Object.keys(right) as (keyof ReviewSubmissionTarget)[]).every((key) => right[key] === left[key]);
}

/** A native Decision is checked explicitly; it is never converted to ReviewItem. */
export function reviewSubmissionReceiptMatches(value: unknown, context: ReviewSubmissionContext,
  body: ReviewSubmissionBody, key: string, correlation: string): value is ReviewSubmissionReceipt {
  if (!readObject(value) || value.source !== "canonical" || typeof value.idempotentReplay !== "boolean"
    || value.correlationId !== correlation || !readObject(value.records) || !readObject(value.commandReceipt)
    || !readObject(value.review)) return false;
  const record = value.records, command = value.commandReceipt, decision = value.review;
  return keys(record, ["candidateId", "listingId", "reportId", "reviewId", "decisionId", "approvalId", "auditId"])
    && record.candidateId === context.candidateId && record.listingId === context.listingId
    && record.reportId === body.expectedReportId && record.reportVersion === body.expectedReportVersion
    && record.reviewId === record.decisionId && record.approvalId !== record.decisionId
    && version(record.decisionVersion) && (body.decisionId === null
      ? record.decisionVersion === 2 : record.decisionId === body.decisionId && record.decisionVersion === body.expectedDecisionVersion! + 1)
    && command.operation === "submit" && command.actorSubjectId === body.expectedActorSubjectId
    && command.tenantId === body.expectedTenantId && command.idempotencyKey === key
    && command.correlationId === correlation && command.decisionId === record.decisionId
    && command.submissionSummary === body.submissionSummary && command.reviewerGroupId === body.reviewerGroupId
    && readId(command.originalCreatorSubjectId) && command.verifiedSubmitterSubjectId === body.expectedActorSubjectId
    && decision.decision_id === record.decisionId && decision.candidate_site_id === record.candidateId
    && decision.report_id === record.reportId && decision.report_version === record.reportVersion
    && decision.version === record.decisionVersion && decision.approval_id === record.approvalId
    && decision.decision_status === "PENDING_REVIEW" && decision.tenant_id === command.tenantId
    && typeof command.originalCreatorVerified === "boolean" && decision.created_by_verified === command.originalCreatorVerified
    && decision.created_by === command.originalCreatorSubjectId && decision.submitted_by === command.verifiedSubmitterSubjectId
    && decision.submission_actor_verified === true && decision.submission_summary === body.submissionSummary
    && decision.reviewer_group_id === body.reviewerGroupId && Array.isArray(decision.history)
    && (context.previousReview ? decision.previous_decision_id === context.previousReview.decisionId
      && command.previousDecisionId === context.previousReview.decisionId && command.candidateState === body.expectedCandidateState
      : decision.previous_decision_id === undefined || decision.previous_decision_id === null)
    && hasCurrentPendingSubmissionHistory(decision);
}

/** Requires a fresh authorized, complete canonical graph and the producer's
 * physical-pointer-validated pending reference. A count or status is insufficient. */
export function reviewSubmissionReadbackMatches(network: unknown, reviews: unknown, receipt: ReviewSubmissionReceipt): boolean {
  if (!readObject(network) || network.source !== "api" || !Array.isArray(network.candidates)
    || !Array.isArray(network.listings) || !readObject(reviews) || reviews.source !== "canonical"
    || inspectReviewsReadProjection(reviews as NetworkReviewsSnapshot) !== "ready") return false;
  const records = receipt.records, command = receipt.commandReceipt;
  const candidates = network.candidates.filter((row) => readObject(row) && row.id === records.candidateId);
  const listings = network.listings.filter((row) => readObject(row) && row.id === records.listingId);
  const graph = reviews as NetworkReviewsSnapshot;
  const rows = graph.reviews.filter((row) => row.id === records.decisionId);
  const decisions = graph.decisions?.filter((row) => row.decision_id === records.decisionId) ?? [];
  const approvals = graph.approvals?.filter((row) => row.reviewId === records.decisionId) ?? [];
  const candidate = candidates[0];
  if (candidates.length !== 1 || listings.length !== 1 || !readObject(candidate)
    || candidate.listingId !== records.listingId || !readObject(listings[0])
    || listings[0].tenantId !== command.tenantId || rows.length !== 1 || decisions.length !== 1 || approvals.length !== 1
    || candidate.reviewId != null && candidate.reviewId !== records.decisionId
    || !hasCandidateReviewSubmission(candidate.reviewSubmission)) return false;
  const pointer = candidate.reviewSubmission, row = rows[0], decision = decisions[0], approval = approvals[0];
  const previous = receipt.review.previous_decision_id;
  if (previous !== undefined && previous !== null && (!readId(previous)
    || decision.previous_decision_id !== previous || pointer.previousDecisionId !== previous
    || command.previousDecisionId !== previous || command.candidateState !== (candidate.canonicalSiteStatus ?? candidate.status)
    || decision.history?.at(-1)?.previous_decision_id !== previous
    || !["approved", "opened"].includes(String(candidate.canonicalSiteStatus ?? candidate.status))
    || !hasApprovedReviewHistory(graph, records.candidateId, command.tenantId, previous, records.reportVersion))) return false;
  if ((previous === undefined || previous === null)
    && (decision.previous_decision_id != null || pointer.previousDecisionId != null || command.previousDecisionId != null)) return false;
  return pointer.decisionId === records.decisionId && pointer.approvalId === records.approvalId
    && pointer.decisionVersion === records.decisionVersion && pointer.reportId === records.reportId
    && pointer.reportVersion === records.reportVersion && pointer.tenantId === command.tenantId
    && pointer.submittedBy === command.verifiedSubmitterSubjectId && pointer.submissionSummary === command.submissionSummary
    && pointer.reviewerGroupId === command.reviewerGroupId && row.candidateId === records.candidateId
    && (row.sourceListingId === null || row.sourceListingId === records.listingId) && row.reportId === records.reportId && row.reportVersion === records.reportVersion
    && row.reportAvailability === "available" && row.canonicalDecisionStatus === "PENDING_REVIEW"
    && decision.version === records.decisionVersion && decision.tenant_id === command.tenantId
    && decision.approval_id === records.approvalId && readObject(decision)
    && decision.submitted_by === command.verifiedSubmitterSubjectId && decision.submission_actor_verified === true
    && decision.submission_summary === command.submissionSummary && decision.reviewer_group_id === command.reviewerGroupId
    && hasCurrentPendingSubmissionHistory(decision)
    && decision.created_by === command.originalCreatorSubjectId && decision.created_by_verified === command.originalCreatorVerified
    && pointer.originalCreatorSubjectId === command.originalCreatorSubjectId && pointer.originalCreatorVerified === command.originalCreatorVerified
    && approval.id === records.approvalId
    && approval.recordSource === "persisted" && approval.approvalStatus === "pending" && approval.status === "pending";
}


/** An affordance to request authoritative context, never submission authority. */
export function reviewSubmissionOffers(network: unknown, scoring: NetworkScoringSnapshot | null,
  reviews: NetworkReviewsSnapshot | null, native: CandidateNativeWorkflowReads | undefined): Record<string, ReviewSubmissionOffer> {
  const offers: Record<string, ReviewSubmissionOffer> = {};
  if (!native || !readObject(network) || network.source !== "api" || !hasNetworkListingsRenderShape(network)
    || !Array.isArray(network.candidates) || scoring?.source !== "canonical" || !hasNetworkScoringRenderShape(scoring)
    || reviews?.source !== "canonical" || inspectReviewsReadProjection(reviews) === "error") return offers;
  for (const candidate of scoring.candidates) {
    const read = native[candidate.id];
    const raw = network.candidates.filter((row) => readObject(row) && row.id === candidate.id);
    const reports = scoring.scorecards.filter((row) => row.id === candidate.id);
    if (!read || read.availability === "conflict" || raw.length !== 1 || !readObject(raw[0])
      || String(raw[0].canonicalSiteStatus ?? raw[0].status) === "rejected"
      || read.stage === "pendingreview"
      || reports.length !== 1 || !readId(reports[0].reportId) || !version(reports[0].reportVersion)
      || !readId(reports[0].modelVersion) || !readId(reports[0].sitescoreRunId)) continue;
    const own = reviews.reviews.filter((row) => row.candidateId === candidate.id);
    const operational = ["approved", "opened"].includes(String(raw[0].canonicalSiteStatus ?? raw[0].status));
    const pointer = read.review?.reviewId ?? raw[0].reviewId;
    if (operational && readId(pointer) && !own.some((row) => row.canonicalDecisionStatus === "PENDING_REVIEW")) {
      const previous = reviews.decisions?.find((row) => row.decision_id === pointer);
      if (previous && readId(previous.tenant_id)
        && hasApprovedReviewHistory(reviews, candidate.id, previous.tenant_id, pointer, reports[0].reportVersion!)) {
        offers[candidate.id] = { candidateId: candidate.id, decisionId: null, newRound: true, previousDecisionId: pointer };
        continue;
      }
    }
    if (!operational && ["approved", "rejected", "onhold"].includes(String(read.stage))) continue;
    if (!own.length && read.stage === "evaluated" && read.availability === "recorded") {
      offers[candidate.id] = { candidateId: candidate.id, decisionId: null }; continue;
    }
    const eligible = own.filter((row) => ["DRAFT", "SYSTEM_RECOMMENDED"].includes(row.canonicalDecisionStatus ?? "")
      && row.reportAvailability === "available" && (pointer ? row.id === pointer
        : own.length === 1));
    if (eligible.length !== 1 || !pointer && own.some((row) => ["APPROVED", "REJECTED", "PENDING_REVIEW"].includes(row.canonicalDecisionStatus ?? ""))) continue;
    const decision = reviews.decisions?.find((row) => row.decision_id === eligible[0].id);
    if (!decision || !version(decision.version)) continue;
    offers[candidate.id] = { candidateId: candidate.id, decisionId: eligible[0].id,
      ...(readId(decision.previous_decision_id) ? { previousDecisionId: decision.previous_decision_id } : {}) };
  }
  return offers;
}

// Presentation mirrors the existing console-to-RBAC mapping (expansion_user /
// site_reviewer have sitescore EXECUTE). The API still checks actual grants.
export function canOfferReviewSubmission(roleId: OperatorRoleId): boolean {
  return roleId === "expansion-manager" || roleId === "expansion-staff";
}
