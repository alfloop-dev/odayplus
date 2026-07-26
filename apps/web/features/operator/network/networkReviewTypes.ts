// Types for the R4 Network Review decision surface served by
// /api/v1/operator/network-reviews (ODP-OC-R4-007). They mirror
// NetworkReviewService's JSON so ReviewPanel / ReviewDecisionDialog render the
// queue, the review detail, and the atomic-sync decision flow without
// re-deriving state client-side.

import type { ScoreRecommendation } from "./networkScoringTypes";

export type ReviewDecisionAction = "GO" | "WAIT" | "RETURN" | "REJECT";

export type ReviewDecision = {
  decision: ReviewDecisionAction;
  finalLabel: string;
  mappedStatus: string;
  reason: string;
  conditions?: string;
  requiredData?: string[];
  override: boolean;
  decidedAt: string;
  decidedBy: string;
  decisionId?: string;
  approvalId?: string;
  auditId?: string;
};

export type ReviewHistoryEntry = { t: string; v: string };

export type ReviewItem = {
  id: string;
  candidateId: string;
  candidateTitle: string;
  zoneLabel: string;
  recommendation: ScoreRecommendation;
  score: number;
  risk: string;
  status: string; // pending | approved | onhold | needdata | rejected
  statusLabel: string;
  requestedBy: string;
  reviewerRole: string;
  submittedAt: string;
  dueAt: string;
  payback: string;
  m12P50: string;
  rentReasonableness: string;
  cannibalization: string;
  sourceListingId: string;
  fieldVisit: string;
  brokerContact: string;
  notes: string;
  modelVersion: string;
  datasetSnapshotId: string;
  compareText: string;
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
  source?: "api";
  reviews: ReviewItem[];
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
