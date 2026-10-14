// Canonical saved-report submission. These fields are caller constraints and
// recorded identities; they do not grant Review VIEW or approval authority.
export type ReviewSubmissionTarget = {
  expectedReportId: string;
  expectedReportVersion: number;
  expectedLatestReportId: string;
  expectedLatestReportVersion: number;
  decisionId: string | null;
  expectedDecisionVersion: number | null;
  expectedCandidateReviewId: string | null;
  expectedCandidateState: string;
  expectedCandidateDigest: string;
};
export type ReviewSubmissionContext = {
  source: "canonical";
  candidateId: string;
  listingId: string;
  addressId: string;
  tenantId: string;
  actorSubjectId: string;
  canSubmit: boolean;
  blockCode: "ALREADY_PENDING" | null;
  target: ReviewSubmissionTarget;
  previousReview?: { decisionId: string; decisionVersion: number; approvalId: string;
    approvalStatus: "approved" | "rejected"; reportId: string; reportVersion: number;
    modelVersion: string; tenantId: string };
  pendingReview: { decisionId: string; decisionVersion: number; approvalId: string; reportId: string; reportVersion: number;
    latestReportId: string; latestReportVersion: number; originalCreatorSubjectId: string; originalCreatorVerified: boolean;
    verifiedSubmitterSubjectId: string | null; submissionSummary: string | null; reviewerGroupId: string | null } | null;
  reviewerGroups: { id: string; label: string; source: "shared.auth.rbac.ROLE_PERMISSIONS" }[];
};
export type ReviewSubmissionForm = { submissionSummary: string; reviewerGroupId: string };
export type ReviewSubmissionBody = ReviewSubmissionTarget & ReviewSubmissionForm & {
  expectedActorSubjectId: string; expectedTenantId: string;
};
export type ReviewSubmissionReceipt = {
  source: "canonical";
  review: Record<string, unknown>;
  records: { candidateId: string; listingId: string; reportId: string; reportVersion: number;
    reviewId: string; decisionId: string; decisionVersion: number; approvalId: string; auditId: string };
  commandReceipt: { operation: "submit"; actorSubjectId: string; tenantId: string; idempotencyKey: string;
    correlationId: string; decisionId: string; submissionSummary: string; reviewerGroupId: string;
    originalCreatorSubjectId: string; originalCreatorVerified: boolean; verifiedSubmitterSubjectId: string;
    previousDecisionId?: string; candidateState?: string };
  idempotentReplay: boolean;
  correlationId: string;
};
export type CandidateReviewSubmissionReference = {
  decisionId: string; approvalId: string; reportId: string; reportVersion: number; decisionVersion: number;
  tenantId: string; submittedBy: string; submissionSummary: string; reviewerGroupId: string;
  originalCreatorSubjectId: string; originalCreatorVerified: boolean; latestReportId: string; latestReportVersion: number;
  previousDecisionId?: string;
};
export type ReviewSubmissionOffer = { candidateId: string; decisionId: string | null;
  newRound?: boolean; previousDecisionId?: string };
