import { readId, readObject } from "./networkReadValidation";

const version = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value > 0;

/** The final actual submit transition for a current verified pending Decision.
 * Legacy unknown records remain readable; they cannot confirm a submission. */
export function hasCurrentPendingSubmissionHistory(decision: unknown): boolean {
  if (!readObject(decision) || decision.decision_status !== "PENDING_REVIEW" || !version(decision.version)
    || !readId(decision.decision_id) || !readId(decision.report_id) || !version(decision.report_version)
    || !readId(decision.model_version) || !readId(decision.tenant_id) || !readId(decision.approval_id)
    || decision.approval_id === decision.decision_id || !readId(decision.submitted_by)
    || decision.submission_actor_verified !== true || !readId(decision.submission_summary)
    || !readId(decision.reviewer_group_id) || !Array.isArray(decision.history)) return false;
  const last: unknown = decision.history.at(-1);
  return readObject(last) && ["SYSTEM_RECOMMENDED", "DRAFT"].includes(String(last.from_status))
    && last.to_status === decision.decision_status && last.action === "submit" && last.version === decision.version
    && last.report_id === decision.report_id && last.report_version === decision.report_version
    && last.model_version === decision.model_version && last.tenant_id === decision.tenant_id
    && last.approval_id === decision.approval_id && last.actor === decision.submitted_by
    && last.submitted_by === decision.submitted_by && last.submission_actor_verified === true
    && last.reason === decision.submission_summary && last.submission_summary === decision.submission_summary
    && last.reviewer_group_id === decision.reviewer_group_id && readId(last.at)
    && (last.previous_decision_id ?? null) === (decision.previous_decision_id ?? null);
}
