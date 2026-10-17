import { readId } from "./networkReadValidation";
import type { NetworkReviewsSnapshot } from "./networkReviewTypes";

const version = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value > 0;

/** Read-only evidence for a later review round. This never grants submission. */
export function hasApprovedReviewHistory(snapshot: NetworkReviewsSnapshot, candidateId: string,
  tenantId: string, previousId: unknown, newerReportVersion: number): boolean {
  const seen = new Set<string>();
  let nextVersion = newerReportVersion;
  while (previousId !== undefined && previousId !== null) {
    if (!readId(previousId) || seen.has(previousId)) return false;
    seen.add(previousId);
    const decisions = snapshot.decisions?.filter((row) => row.decision_id === previousId) ?? [];
    const rows = snapshot.reviews.filter((row) => row.id === previousId);
    const approvals = snapshot.approvals?.filter((row) => row.reviewId === previousId) ?? [];
    if (decisions.length !== 1 || rows.length !== 1 || approvals.length !== 1) return false;
    const decision = decisions[0], row = rows[0], approval = approvals[0];
    const terminal = decision.history?.at(-1);
    if (decision.candidate_site_id !== candidateId || decision.tenant_id !== tenantId
      || !version(decision.version) || !version(decision.report_version) || !version(nextVersion)
      || decision.report_version >= nextVersion || !readId(decision.report_id) || !readId(decision.model_version)
      || !["APPROVED", "REJECTED"].includes(decision.decision_status)
      || row.candidateId !== candidateId || row.reportId !== decision.report_id || row.reportVersion !== decision.report_version
      || row.modelVersion !== decision.model_version || row.canonicalDecisionStatus !== decision.decision_status
      || row.reportAvailability !== "available" || approval.recordSource !== "persisted"
      || !readId(approval.id) || approval.id === decision.decision_id || approval.id !== decision.approval_id
      || approval.approvalStatus !== (decision.decision_status === "APPROVED" ? "approved" : "rejected")
      || !terminal || !readId(terminal.actor) || approval.decidedBy !== terminal.actor || approval.decidedAt !== terminal.at) return false;
    if (terminal.to_status !== decision.decision_status || terminal.report_id !== decision.report_id
      || terminal.report_version !== decision.report_version || terminal.model_version !== decision.model_version
      || terminal.tenant_id !== decision.tenant_id || !version(terminal.version) || terminal.version !== decision.version
      || terminal.approval_id !== decision.approval_id || terminal.network_action !== decision.network_action
      || terminal.action !== (decision.decision_status === "APPROVED" ? "APPROVE" : "REJECT")) return false;
    if (decision.history!.some((item) => (item.previous_decision_id ?? null) !== (decision.previous_decision_id ?? null))) return false;
    if (decision.previous_decision_id === undefined || decision.previous_decision_id === null) {
      return decision.decision_status === "APPROVED";
    }
    previousId = decision.previous_decision_id;
    nextVersion = decision.report_version;
  }
  return false;
}
