/** Pure bootstrap admission loads after the authenticated HTTP/JSON read. */
export { inspectOperatorShellPayload } from "./operatorDataMode";
import {
  normalizeGovernanceApprovals,
  normalizeGovernanceAuditRows,
  normalizeGovernanceDecisionRows,
} from "./governance/governanceEnvelope";

/** Bootstrap side channels use the same governance admission as its API.
 * Shell `approvals` are Today cards and never enter this projection. Absent
 * channels clear their earlier rows; normalizers retain missing fields and
 * current server refusals instead of inventing governance values. */
export function normalizeBootstrapGovernance(record: Record<string, unknown> | null) {
  return {
    approvals: normalizeGovernanceApprovals(record?.governanceApprovals),
    decisions: normalizeGovernanceDecisionRows(record?.governanceDecisions),
    auditRows: normalizeGovernanceAuditRows(record?.governanceAuditRows),
  };
}
