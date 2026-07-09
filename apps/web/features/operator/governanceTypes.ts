export type GovernanceModule = "Store Ops" | "Growth" | "Network" | "Govern" | (string & {});

export type GovernanceApprovalStatus =
  | "pending"
  | "approved"
  | "returned"
  | "rejected"
  | "escalated";

export type GovernancePriority = "low" | "medium" | "high" | "critical" | (string & {});

export type GovernanceRole = "營運主管" | "行銷經理" | "展店經理" | "PM／稽核" | (string & {});

export type GovernanceEvidence = {
  id: string;
  label: string;
  type?: string;
  href?: string;
  state?: "ready" | "missing" | "stale" | (string & {});
};

export type GovernanceApproval = {
  id: string;
  module: GovernanceModule;
  title: string;
  requestor: string;
  submittedAt: string;
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
};

export type GovernanceDecisionRow = {
  id: string;
  module: GovernanceModule;
  item: string;
  systemRecommendation: string;
  finalDecision: string;
  reason: string;
  actor: string;
  decidedAt: string;
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

export type GovernanceAuditRow = {
  id: string;
  category: GovernanceAuditCategory;
  timestamp: string;
  actor: string;
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
