export type AuditOutcome = "allow" | "deny" | "success" | "failure";
export type EvidenceStatus = "齊備" | "待補" | "不適用";

export type AuditDecision = {
  decisionId: string;
  eventId: string;
  module: string;
  eventType: string;
  action: "approve" | "execute" | "publish" | "override" | "rollback" | "export";
  actor: string;
  role: string;
  resource: string;
  outcome: AuditOutcome;
  occurredAt: string;
  correlationId: string;
  overrideReason?: string;
  evidenceCompleteness: string;
  entity: string;
  modelVersion: string;
  policyVersion: string;
  featureSnapshotTime: string;
  decisionTime: string;
  reason: string;
  executionStatus: string;
  outcomeStatus: string;
  auditStatus: string;
  systemRecommendation: string;
  humanDecisionStatus: string;
  riskConfidence: string;
  requiredApproval: string;
  primaryAction: string;
  before?: string;
  after?: string;
};

export type MatrixRow = {
  program: string;
  claimItem: string;
  cells: Record<string, { status: EvidenceStatus; ref?: string; missing?: string }>;
};

export const auditDecisions: AuditDecision[] = [
  {
    decisionId: "decision-lh-240",
    eventId: "audit-lh-9001",
    module: "Learning Hub",
    eventType: "learninghub.model_release.v1",
    action: "publish",
    actor: "model-review-board",
    role: "approver",
    resource: "model/sitescore-propensity/2.4.0",
    outcome: "success",
    occurredAt: "2026-06-26 10:44",
    correlationId: "corr-lh-sitescore-240",
    evidenceCompleteness: "6/7 timeline nodes; outcome pending",
    entity: "sitescore-propensity:2.4.0",
    modelVersion: "sitescore-propensity 2.4.0",
    policyVersion: "learninghub-release-policy-v1",
    featureSnapshotTime: "2026-06-25 08:00",
    decisionTime: "2026-06-26 10:44",
    reason: "Canary release after validation pass; monitor west suburban calibration.",
    executionStatus: "canary running",
    outcomeStatus: "watch window open",
    auditStatus: "restricted export audited",
    systemRecommendation: "Release as CANARY with rollback target 2.3.1.",
    humanDecisionStatus: "Approved by model-review-board with reason.",
    riskConfidence: "R3 risk, confidence 0.84, one segment warning.",
    requiredApproval: "model-review-board approval required for R3 release.",
    primaryAction: "View canary audit trail or export masked evidence bundle.",
  },
  {
    decisionId: "decision-avm-118",
    eventId: "audit-avm-7780",
    module: "AVM",
    eventType: "avm.dataroom_exported.v1",
    action: "export",
    actor: "finance-auditor",
    role: "finance_legal",
    resource: "avm/case-118/dataroom",
    outcome: "success",
    occurredAt: "2026-06-25 15:16",
    correlationId: "corr-avm-118",
    evidenceCompleteness: "7/7 timeline nodes",
    entity: "case-118",
    modelVersion: "avm-range 1.5.2",
    policyVersion: "avm-dataroom-policy-v2",
    featureSnapshotTime: "2026-06-25 09:30",
    decisionTime: "2026-06-25 14:55",
    reason: "Finance diligence package for subsidy claim.",
    executionStatus: "dataroom exported",
    outcomeStatus: "ready",
    auditStatus: "export audit recorded",
    systemRecommendation: "Export finance diligence package with PII masking.",
    humanDecisionStatus: "Export approved by finance-auditor.",
    riskConfidence: "Restricted data, confidence governed by valuation interval.",
    requiredApproval: "finance_legal approval and export reason.",
    primaryAction: "Download masked evidence bundle.",
  },
  {
    decisionId: "decision-netplan-404",
    eventId: "audit-netplan-404",
    module: "NetPlan",
    eventType: "netplan.approved.v1",
    action: "override",
    actor: "ops-director",
    role: "ops_manager",
    resource: "netplan/scenario-404",
    outcome: "allow",
    occurredAt: "2026-06-24 18:02",
    correlationId: "corr-netplan-404",
    overrideReason: "Keep flagship location despite solver MOVE recommendation due to lease negotiation evidence.",
    evidenceCompleteness: "5/7 timeline nodes; outcome and feedback pending",
    entity: "scenario-404",
    modelVersion: "netplan-solver 3.1.0",
    policyVersion: "network-approval-policy-v4",
    featureSnapshotTime: "2026-06-24 12:00",
    decisionTime: "2026-06-24 18:02",
    reason: "Override with lease-side evidence attached.",
    executionStatus: "approval recorded",
    outcomeStatus: "pending",
    auditStatus: "override reason present",
    systemRecommendation: "MOVE store to nearby candidate.",
    humanDecisionStatus: "Override approved; KEEP current store.",
    riskConfidence: "R4 operational risk, confidence 0.77.",
    requiredApproval: "ops director + finance legal review.",
    primaryAction: "Inspect before/after override evidence.",
    before: "MOVE to candidate-NP-77",
    after: "KEEP current flagship location",
  },
];

export const matrixColumns = [
  "決策核准",
  "執行紀錄",
  "結果觀察",
  "資料快照",
  "模型卡/版本",
  "匯出紀錄",
];

export const subsidyMatrix: MatrixRow[] = [
  {
    program: "Urban Growth Subsidy",
    claimItem: "New store qualification",
    cells: {
      "決策核准": { status: "齊備", ref: "decision-lh-240" },
      "執行紀錄": { status: "齊備", ref: "audit-lh-9001" },
      "結果觀察": { status: "待補", missing: "Outcome observed node" },
      "資料快照": { status: "齊備", ref: "ds-sitescore-2026w25" },
      "模型卡/版本": { status: "齊備", ref: "sitescore-propensity 2.4.0" },
      "匯出紀錄": { status: "待補", missing: "Evidence export event" },
    },
  },
  {
    program: "Finance Diligence",
    claimItem: "Valuation data room",
    cells: {
      "決策核准": { status: "齊備", ref: "decision-avm-118" },
      "執行紀錄": { status: "齊備", ref: "audit-avm-7780" },
      "結果觀察": { status: "不適用" },
      "資料快照": { status: "齊備", ref: "feature-snapshot-avm-118" },
      "模型卡/版本": { status: "齊備", ref: "avm-range 1.5.2" },
      "匯出紀錄": { status: "齊備", ref: "corr-avm-118" },
    },
  },
  {
    program: "Network Optimization",
    claimItem: "Flagship retention",
    cells: {
      "決策核准": { status: "齊備", ref: "decision-netplan-404" },
      "執行紀錄": { status: "待補", missing: "Execution started node" },
      "結果觀察": { status: "待補", missing: "Outcome observed node" },
      "資料快照": { status: "齊備", ref: "netplan-scenario-404" },
      "模型卡/版本": { status: "不適用" },
      "匯出紀錄": { status: "待補", missing: "Gap list export" },
    },
  },
];

export function selectedDecision(decisionId?: string): AuditDecision {
  return auditDecisions.find((decision) => decision.decisionId === decisionId) ?? auditDecisions[0];
}
