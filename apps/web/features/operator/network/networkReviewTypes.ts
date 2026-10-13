// Types for the R4 Network Review decision surface served by
// /api/v1/operator/network-reviews (ODP-OC-R4-007). They mirror
// NetworkReviewService's JSON so ReviewPanel / ReviewDecisionDialog render the
// queue, the review detail, and the recorded decision flow without
// re-deriving state client-side.

import type { ScoreRecommendation } from "./networkScoringTypes";

export type ReviewDecisionAction = "GO" | "WAIT" | "RETURN" | "REJECT";

export type ReviewDecision = {
  decision: ReviewDecisionAction;
  finalLabel: string;
  mappedStatus: string;
  reason: string;
  conditions?: string | null;
  requiredData?: string[] | null;
  override: boolean;
  decidedAt: string;
  decidedBy: string;
  decisionId?: string;
  approvalId?: string | null;
  overrideAck?: boolean | null;
  actorRoleId?: string | null;
  auditId?: string;
};

export type ReviewHistoryEntry = { t: string; v: string };

export type ReviewItem = {
  id: string;
  candidateId: string;
  candidateTitle: string;
  zoneLabel: string | null;
  recommendation: ScoreRecommendation;
  score: number | null;
  confidence?: number | null;
  confidenceStatus?: "measured" | "unmeasured";
  reportAvailability?: "available" | "unavailable" | "conflict";
  reportId?: string;
  reportVersion?: number;
  canonicalDecisionStatus?: "DRAFT" | "SYSTEM_RECOMMENDED" | "PENDING_REVIEW" | "APPROVED" | "REJECTED";
  risk: string;
  status: string; // pending | approved | onhold | needdata | rejected
  statusLabel: string;
  requestedBy: string;
  reviewerRole: string;
  submittedAt: string;
  dueAt: string | null;
  payback: string | null;
  m12P50: string | null;
  rentReasonableness: string | null;
  cannibalization: string | null;
  sourceListingId: string | null;
  fieldVisit: string | null;
  brokerContact: string | null;
  notes: string | null;
  modelVersion: string | null;
  datasetSnapshotId: string | null;
  compareText: string | null;
  candidateStatus?: string;
  candidateStatusLabel?: string;
  candidateMissingData?: string[];
  eventChips: string[];
  history: ReviewHistoryEntry[];
  decision: ReviewDecision | null;
  pending?: boolean;
  decided?: boolean;
};

export type NetworkReviewsSnapshot = {
  source?: "api" | "canonical";
  reviews: ReviewItem[];
  candidates?: { id: string; reviewId: string | null; score: number | null }[];
  approvals?: { id: string | null; candidateId: string; reviewId: string;
    recordSource?: "persisted" | "unavailable" | "conflict"; status?: string | null;
    approvalStatus?: string | null; decidedBy?: string | null; decidedAt?: string | null }[];
  decisions?: {
    decision_id: string;
    candidate_site_id: string;
    report_id: string;
    report_version: number;
    decision_status: string;
    recommendation?: ScoreRecommendation;
    model_version?: string | null;
    created_by?: string;
    created_at?: string;
    version?: number;
    tenant_id?: string;
    network_action?: ReviewDecisionAction | null;
    conditions?: string | null;
    required_data?: string[] | null;
    override_ack?: boolean | null;
    actor_role_id?: string | null;
    approval_id?: string | null;
    history?: { to_status: string; actor: string; reason: string; at: string; action: string;
      network_action?: ReviewDecisionAction | null; conditions?: string | null;
      required_data?: string[] | null; override_ack?: boolean | null;
      actor_role_id?: string | null; approval_id?: string | null;
      tenant_id?: string | null; report_id?: string | null; report_version?: number | null;
      model_version?: string | null; version?: number | null }[];
  }[];
  decisionMapping?: Record<string, string>;
  counts?: { reviews: number; pending: number; decided: number };
};

// Form payload a reviewer submits from ReviewDecisionDialog.
export type ReviewDecisionForm = {
  reason: string;
  conditions: string;
  requiredData: string;
  overrideAck: boolean;
};

// Decision → final governance label, mirroring the server mapping so the UI can
// caption the sync note deterministically.
export const DECISION_FINAL_LABEL: Record<ReviewDecisionAction, string> = {
  GO: "Approved",
  WAIT: "On Hold",
  RETURN: "Need Data",
  REJECT: "Rejected",
};

export const DECISION_BUTTON_LABEL: Record<ReviewDecisionAction, string> = {
  GO: "核准 GO",
  WAIT: "核准 WAIT",
  RETURN: "退回修改",
  REJECT: "駁回",
};

// A decision overrides the model when its verb differs from the SiteScore
// recommendation's natural verb. RETURN (defer for data) never overrides.
export function isOverride(action: ReviewDecisionAction, recommendation: ScoreRecommendation): boolean {
  if (action === "RETURN") return false;
  return action !== recommendation;
}

export function reviewStatusTone(status: string): "good" | "watch" | "risk" {
  if (status === "approved") return "good";
  if (status === "rejected") return "risk";
  return "watch";
}
