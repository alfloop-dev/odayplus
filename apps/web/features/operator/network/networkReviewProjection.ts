import { DECISION_FINAL_LABEL, type NetworkReviewsSnapshot, type ReviewItem } from "./networkReviewTypes";
import { readObject } from "./networkReadValidation";
import { hasCurrentPendingSubmissionHistory } from "./networkPendingReviewHistory";

const decisionStates = new Set(["DRAFT", "SYSTEM_RECOMMENDED", "PENDING_REVIEW", "APPROVED", "REJECTED"]);
const availableReports = new Set(["available", "unavailable", "conflict"]);
const nonempty = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const recommendations = new Set(["GO", "WAIT", "REJECT", "INVESTIGATE"]);
const textFields = ["candidateTitle", "risk", "status", "statusLabel", "requestedBy", "reviewerRole", "submittedAt"] as const;
const nullableTextFields = ["zoneLabel", "dueAt", "payback", "m12P50", "rentReasonableness", "cannibalization",
  "sourceListingId", "fieldVisit", "brokerContact", "notes", "modelVersion", "datasetSnapshotId", "compareText"] as const;

const finiteNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const stringList = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");
const optionalNullableText = (value: unknown): boolean => value === undefined || value === null || typeof value === "string";

// This shared renderer check validates values, not legacy action/status mapping
// or canonical authority. Canonical graph/measurement rules remain below.
function hasRenderableDecision(value: unknown): boolean {
  if (value === undefined || value === null) return true;
  return readObject(value) && ["decision", "finalLabel", "mappedStatus", "reason", "decidedAt", "decidedBy"]
    .every((key) => typeof value[key] === "string") && typeof value.override === "boolean"
    && optionalNullableText(value.conditions)
    && (value.requiredData === undefined || value.requiredData === null || stringList(value.requiredData))
    && ["decisionId", "approvalId", "auditId"].every((key) => optionalNullableText(value[key]));
}
function hasRenderableReviewFields(value: unknown, legacy = false): value is ReviewItem {
  if (!readObject(value) || !nonempty(value.id) || !nonempty(value.candidateId)) return false;
  return textFields.every((key) => typeof value[key] === "string") &&
    nullableTextFields.every((key) => value[key] === null || typeof value[key] === "string" || legacy && value[key] === undefined) &&
    typeof value.recommendation === "string" && recommendations.has(value.recommendation) &&
    (value.score === null || finiteNumber(value.score) || legacy && value.score === undefined) &&
    (value.confidence === undefined || value.confidence === null || finiteNumber(value.confidence)) &&
    ["confidenceStatus", "reportAvailability", "canonicalDecisionStatus", "reportId"].every((key) => optionalNullableText(value[key])) &&
    (value.reportVersion === undefined || value.reportVersion === null || finiteNumber(value.reportVersion)) &&
    stringList(value.eventChips) && Array.isArray(value.history) && value.history.every((entry) =>
      readObject(entry) && typeof entry.t === "string" && typeof entry.v === "string") &&
    hasRenderableDecision(value.decision) &&
    (value.candidateStatusLabel === undefined || typeof value.candidateStatusLabel === "string") &&
    (value.candidateStatus === undefined || typeof value.candidateStatus === "string") &&
    (value.candidateMissingData === undefined || stringList(value.candidateMissingData));
}

/** Admit the recorded canonical relationship graph, not an arbitrary source label. */
export function inspectReviewsReadProjection(snapshot: NetworkReviewsSnapshot): "ready" | "empty" | "error" {
  if (!readObject(snapshot) || !Array.isArray(snapshot.reviews)) return "error";
  if (snapshot.source === "api") {
    if (!snapshot.reviews.every((row) => hasRenderableReviewFields(row, true))) return "error";
    return snapshot.reviews.length ? "ready" : "empty";
  }
  if (snapshot.source !== "canonical" || !Array.isArray(snapshot.decisions) ||
      !Array.isArray(snapshot.candidates) || !Array.isArray(snapshot.approvals)) return "error";
  const decisions = new Map(snapshot.decisions.map((row) => [row?.decision_id, row]));
  const candidates = new Map(snapshot.candidates.map((row) => [row?.reviewId, row]));
  const approvals = new Map(snapshot.approvals.map((row) => [row?.reviewId, row]));
  if (decisions.size !== snapshot.decisions.length || candidates.size !== snapshot.candidates.length ||
      approvals.size !== snapshot.approvals.length || snapshot.reviews.length !== decisions.size ||
      snapshot.reviews.length !== candidates.size || snapshot.reviews.length !== approvals.size) return "error";
  const seen = new Set<string>();
  for (const row of snapshot.reviews) {
    if (!row || !nonempty(row.id) || seen.has(row.id) || !nonempty(row.candidateId) ||
        !nonempty(row.reportId) || !Number.isInteger(row.reportVersion) || (row.reportVersion ?? 0) < 1 ||
        !decisionStates.has(row.canonicalDecisionStatus ?? "") || !availableReports.has(row.reportAvailability ?? "") ||
        row.score !== null || !Array.isArray(row.eventChips) || !row.eventChips.every((chip) => typeof chip === "string") ||
        !Array.isArray(row.history) || !row.history.every((entry) => entry && typeof entry.t === "string" && typeof entry.v === "string") ||
        !hasRenderableReviewFields(row)) return "error";
    if (row.confidence === null) {
      if (row.confidenceStatus !== "unmeasured") return "error";
    } else if (typeof row.confidence !== "number" || !Number.isFinite(row.confidence) ||
               row.confidence < 0 || row.confidence > 1 || row.confidenceStatus !== "measured" ||
               row.reportAvailability !== "available") return "error";
    const decision = decisions.get(row.id), candidate = candidates.get(row.id), approval = approvals.get(row.id);
    if (!decision || decision.candidate_site_id !== row.candidateId || decision.report_id !== row.reportId ||
        decision.report_version !== row.reportVersion || decision.decision_status !== row.canonicalDecisionStatus ||
        decision.recommendation !== row.recommendation || decision.created_by !== row.requestedBy ||
        decision.created_at !== row.submittedAt ||
        (decision.model_version !== null && typeof decision.model_version !== "string") ||
        (decision.model_version?.trim() ? decision.model_version : null) !== row.modelVersion ||
        !Array.isArray(decision.history) ||
        !decision.history.every((entry) => entry && typeof entry.to_status === "string" && typeof entry.actor === "string" &&
          typeof entry.reason === "string" && typeof entry.at === "string" && typeof entry.action === "string") ||
        !candidate || candidate.id !== row.candidateId || candidate.score !== null ||
        !approval || approval.candidateId !== row.candidateId) return "error";
    const action = decision.network_action ?? (row.canonicalDecisionStatus === "REJECTED" &&
      decision.version === undefined && decision.history.at(-1)?.action === "REJECT" ? "REJECT" : null);
    if (decision.network_action !== undefined && decision.network_action !== null &&
        !["GO", "WAIT", "RETURN", "REJECT"].includes(decision.network_action)) return "error";
    const actionState = { GO: "APPROVED", WAIT: "APPROVED", RETURN: "DRAFT", REJECT: "REJECTED" };
    if (action && actionState[action] !== row.canonicalDecisionStatus) return "error";
    const displayStatus = action === "WAIT" ? "onhold" : action === "RETURN" ? "needdata"
      : action === "GO" ? "approved" : row.canonicalDecisionStatus === "REJECTED" ? "rejected"
      : ["DRAFT", "APPROVED"].includes(row.canonicalDecisionStatus ?? "") ? "unknown" : "pending";
    if (decision.version !== undefined && (!Number.isInteger(decision.version) || decision.version < 0)) return "error";
    if (row.canonicalDecisionStatus === "PENDING_REVIEW" && decision.version !== undefined
      && decision.submission_actor_verified === true && (!hasCurrentPendingSubmissionHistory(decision)
        || approval.recordSource !== "persisted" || approval.id !== decision.approval_id
        || approval.approvalStatus !== "pending" || approval.status !== "pending")) return "error";
    if ((decision.conditions != null && typeof decision.conditions !== "string") ||
        (decision.required_data != null && (!Array.isArray(decision.required_data) || !decision.required_data.every(nonempty))) ||
        (decision.override_ack != null && typeof decision.override_ack !== "boolean") ||
        (decision.actor_role_id != null && !nonempty(decision.actor_role_id)) ||
        (decision.approval_id != null && !nonempty(decision.approval_id))) return "error";
    if (approval.recordSource !== undefined) {
      if (approval.recordSource === "conflict") return "error";
      if (approval.recordSource === "persisted") {
        if (!nonempty(decision.approval_id) || approval.id !== decision.approval_id || approval.id === row.id) return "error";
        const formalStatus = row.canonicalDecisionStatus === "DRAFT" ? "returned" : row.canonicalDecisionStatus === "REJECTED" ? "rejected"
          : row.canonicalDecisionStatus === "APPROVED" ? "approved" : "pending";
        if (approval.approvalStatus !== formalStatus) return "error";
        const envelopeStatus = action === "WAIT" ? "on_hold" : action === "RETURN" ? "need_data"
          : action === "GO" ? "approved" : action === "REJECT" ? "rejected" : formalStatus === "pending" ? "pending" : null;
        if (approval.status !== envelopeStatus) return "error";
        if (action && (approval.decidedBy !== decision.history.at(-1)?.actor || approval.decidedAt !== decision.history.at(-1)?.at)) return "error";
      } else if (approval.recordSource !== "unavailable" || approval.id !== null || decision.approval_id != null ||
                 approval.approvalStatus !== null) return "error";
    } else if (approval.id !== row.id) return "error";
    if (row.status !== displayStatus) return "error";
    if (row.pending !== undefined && row.pending !== (row.canonicalDecisionStatus === "PENDING_REVIEW")) return "error";
    if (row.decided !== undefined && row.decided !== Boolean(action)) return "error";
    if (action) {
      const last = decision.history.at(-1), display = row.decision;
      if (!last || last.to_status !== row.canonicalDecisionStatus || !display || display.decision !== action ||
          display.finalLabel !== DECISION_FINAL_LABEL[action] || display.mappedStatus !== displayStatus ||
          display.reason !== last.reason || display.decidedBy !== last.actor || display.decidedAt !== last.at ||
          typeof display.override !== "boolean" || (display.conditions != null && typeof display.conditions !== "string") ||
          (display.requiredData != null && (!Array.isArray(display.requiredData) || !display.requiredData.every((value) => typeof value === "string"))) ||
          (display.decisionId !== undefined && display.decisionId !== row.id)) return "error";
      if (decision.network_action !== undefined) {
        if (last.network_action !== action || display.conditions !== decision.conditions ||
            JSON.stringify(display.requiredData) !== JSON.stringify(decision.required_data) ||
            last.conditions !== decision.conditions || JSON.stringify(last.required_data) !== JSON.stringify(decision.required_data) ||
            display.overrideAck !== decision.override_ack || display.actorRoleId !== decision.actor_role_id ||
            last.override_ack !== decision.override_ack || last.actor_role_id !== decision.actor_role_id ||
            display.approvalId !== decision.approval_id || last.approval_id !== decision.approval_id ||
            last.report_id !== decision.report_id || last.report_version !== decision.report_version ||
            last.model_version !== decision.model_version || last.tenant_id !== decision.tenant_id ||
            last.version !== decision.version ||
            (action === "WAIT" && !nonempty(decision.conditions)) ||
            (action === "RETURN" && (!Array.isArray(decision.required_data) || !decision.required_data.length ||
              !decision.required_data.every(nonempty)))) return "error";
      }
    } else if (row.decision !== null) return "error";
    seen.add(row.id);
  }
  return snapshot.reviews.length ? "ready" : "empty";
}

export function isReviewPending(review: ReviewItem): boolean {
  return review.canonicalDecisionStatus
    ? review.canonicalDecisionStatus === "PENDING_REVIEW"
    : review.status === "pending";
}

export function hasRecordedReviewReport(review: ReviewItem): boolean {
  return !review.canonicalDecisionStatus || review.reportAvailability === "available";
}

export function formatReviewScore(score: number | null): string {
  return typeof score === "number" && Number.isFinite(score) ? String(score) : "—";
}

export function formatReviewConfidence(review: ReviewItem): string {
  return review.confidenceStatus === "measured" && typeof review.confidence === "number" && Number.isFinite(review.confidence)
    ? String(review.confidence)
    : "未量測";
}
