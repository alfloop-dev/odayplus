export type GovernanceModule = "Store Ops" | "Growth" | "Network" | "Govern" | (string & {});

/**
 * Canonical approval states plus the raw statuses the Governance API may emit
 * for canonical modules (NetPlan / PriceOps forward their own decision value).
 * The union stays open so an unrecognised status can be carried through as-is:
 * coercing it to `pending` would misreport the governance state of the row.
 */
export type GovernanceApprovalStatus =
  | "pending"
  | "approved"
  | "returned"
  | "rejected"
  | "escalated"
  | (string & {});

export type GovernancePriority = "low" | "medium" | "high" | "critical" | (string & {});

export type GovernanceRole = "營運主管" | "行銷經理" | "展店經理" | "PM／稽核" | (string & {});

export type GovernanceEvidence = {
  id: string;
  label: string;
  type?: string;
  href?: string;
  state?: "ready" | "missing" | "stale" | (string & {});
};

/**
 * A governance approval row.  `id`, `module`, `title` and `status` identify and
 * classify the row and are required — a record without them is not a governance
 * approval and must be dropped at the boundary rather than patched up.
 * `requestor` and `submittedAt` are optional because the Governance API's
 * canonical modules (SiteScore / AVM / NetPlan / PriceOps) do not always carry
 * them; the workspace renders an explicit unavailable marker for absent fields
 * instead of inventing a requestor or a submission time.
 */
export type GovernanceApproval = {
  id: string;
  module: GovernanceModule;
  title: string;
  requestor?: string;
  submittedAt?: string;
  status: GovernanceApprovalStatus;
  priority?: GovernancePriority;
  owner?: string;
  sla?: string;
  entityRef?: string;
  summary?: string;
  systemRecommendation?: string;
  risk?: string;
  roleNote?: string;
  evidence?: GovernanceEvidence[];
  reason?: string;
};

/**
 * A Decision Log row.  `id`, `module` and `item` are the row identity; the
 * decision content is optional because canonical Governance API rows may omit
 * it or carry a non-textual value, and an absent field is displayed as
 * unavailable rather than filled with an invented decision, actor or time.
 */
export type GovernanceDecisionRow = {
  id: string;
  module: GovernanceModule;
  item: string;
  systemRecommendation?: string;
  finalDecision?: string;
  reason?: string;
  actor?: string;
  decidedAt?: string;
  model?: string;
  datasetSnapshot?: string;
  approvalId?: string;
};

export type GovernanceAuditCategory =
  | "issue"
  | "camera"
  | "approval"
  | "growth"
  | "network"
  | "export"
  | "system"
  | (string & {});

/**
 * An Audit Trail row.  `id` and `action` state which event happened; canonical
 * Governance API rows carry no `category` and timestamp their transitions with
 * `occurredAt`, so those fields stay optional and are rendered as explicitly
 * unavailable instead of being defaulted to a fabricated category or time.
 */
export type GovernanceAuditRow = {
  id: string;
  category?: GovernanceAuditCategory;
  timestamp?: string;
  actor?: string;
  action: string;
  module?: GovernanceModule;
  entityRef?: string;
  summary?: string;
  reason?: string;
  correlationId?: string;
};

export type GovernanceDecisionAction = "approve" | "return" | "reject";

export type GovernanceDecisionPayload = {
  approvalId: string;
  action: GovernanceDecisionAction;
  reason?: string;
  role?: GovernanceRole;
  approval?: GovernanceApproval;
};

export type GovernanceWorkspaceCallbacks = {
  onApprove?: (payload: GovernanceDecisionPayload) => void;
  onReturn?: (payload: GovernanceDecisionPayload) => void;
  onReject?: (payload: GovernanceDecisionPayload) => void;
  onSelectApproval?: (approval: GovernanceApproval) => void;
};
