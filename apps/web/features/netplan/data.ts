import type { StatusTone } from "@oday-plus/domain-types";

/**
 * NetPlan store-network scenario view model. Vocabulary is authoritative to
 * modules/netplan/domain/planning.py and solver/netplan/{model,optimizer}.py;
 * see docs/design/ODAY_PLUS_ASSET_AND_NETPLAN_UI_SPEC.md Part B. The frontend
 * never invents states — these mirror NetPlanScenarioStatus (9 values),
 * VALID_TRANSITIONS, and the solve-result fields.
 */

export type NetPlanScenarioStatus =
  | "draft"
  | "solved"
  | "infeasible"
  | "pending_approval"
  | "approved"
  | "rejected"
  | "executed"
  | "outcome_observed"
  | "closed";

export type SolverStatus = "optimal" | "feasible" | "infeasible";

export type NetworkAction = "OPEN" | "KEEP" | "IMPROVE" | "MOVE" | "EXIT";

export type NetPlanRouteKey = "overview" | "scenarios" | "scenarioDetail";

/** Allowed forward transitions — mirrors planning.VALID_TRANSITIONS. */
export const VALID_TRANSITIONS: Record<NetPlanScenarioStatus, NetPlanScenarioStatus[]> = {
  draft: ["solved", "infeasible"],
  solved: ["pending_approval", "rejected"],
  infeasible: [],
  pending_approval: ["approved", "rejected"],
  approved: ["executed"],
  rejected: [],
  executed: ["outcome_observed"],
  outcome_observed: ["closed"],
  closed: [],
};

export const TERMINAL_STATUSES: NetPlanScenarioStatus[] = ["infeasible", "rejected", "closed"];

export type SelectedAction = {
  entityId: string;
  action: NetworkAction;
  expectedGm: number;
  budgetCost: number;
  riskScore: number;
  capacityDelta: number;
  notes: string;
};

export type ActionCounts = Record<NetworkAction, number>;

export type Alternative = {
  id: string;
  deltaObjective: number;
  deltaBudget: number;
  deltaRisk: number;
  actionDiff: string;
};

export type BindingConstraint = {
  constraint: string;
  usagePct: number;
};

export type InfeasibilityDiagnosis = {
  violatedConstraint: string;
  affectedStores: string[];
  requiredRelaxation: string;
  businessImpact: string;
  suggestedAction: string;
};

export type Constraints = {
  maxBudget: number;
  minExpectedGrossMargin?: number;
  minCapacityDelta?: number;
  maxAverageRisk?: number;
  minActionCounts: Partial<ActionCounts>;
  maxActionCounts: Partial<ActionCounts>;
  policyVersion: string;
};

export type ActionOption = {
  entityId: string;
  action: NetworkAction;
  expectedGm: number;
  budgetCost: number;
  riskScore: number;
  capacityDelta: number;
  notes: string;
};

export type StatusTransition = {
  from: NetPlanScenarioStatus | "—";
  to: NetPlanScenarioStatus;
  actor: string;
  reason: string;
  at: string;
  correlationId: string;
};

export type ApprovalRecord = {
  approvalId: string;
  actorId: string;
  decision: "approved" | "rejected";
  reason: string;
  decidedAt: string;
  policyVersion: string;
  correlationId: string;
};

export type ExecutionRecord = {
  executionId: string;
  actions: number;
  executedBy: string;
  executedAt: string;
};

export type OutcomeRecord = {
  expectedGrossMargin: number;
  actualGrossMargin: number;
  variance: number;
  variancePct: number;
  observedAt: string;
};

export type SolveResult = {
  solverStatus: SolverStatus;
  objectiveValue: number;
  expectedGrossMargin: number;
  budgetUsage: number;
  averageRisk: number;
  capacityDelta: number;
  actionCounts: ActionCounts;
  selectedActions: SelectedAction[];
  bindingConstraints: BindingConstraint[];
  alternatives: Alternative[];
  diagnostics: InfeasibilityDiagnosis[];
  solverVersion: string;
};

export type NetPlanScenario = {
  scenarioId: string;
  scenarioName: string;
  planningHorizon: string;
  status: NetPlanScenarioStatus;
  constraints: Constraints;
  optionsByEntity: ActionOption[];
  solveResult: SolveResult | null;
  approval: ApprovalRecord | null;
  execution: ExecutionRecord | null;
  outcome: OutcomeRecord | null;
  statusHistory: StatusTransition[];
  modelVersion: string;
  featureVersion: string;
  solverVersion: string;
  policyVersion: string;
  correlationId: string;
};

export const NETPLAN_MODEL_VERSION = "netplan-network-baseline-v1";
export const NETPLAN_FEATURE_VERSION = "network-plan-view-v1";
export const NETPLAN_SOLVER_VERSION = "netplan-exhaustive-cpsat-compatible-v1";
export const NETPLAN_POLICY_VERSION = "netplan-network-policy-v1";

export const freshness = {
  updatedAt: "2026-06-28 09:24",
  modelVersion: NETPLAN_MODEL_VERSION,
  solverVersion: NETPLAN_SOLVER_VERSION,
  sourceSnapshotId: "snap-netplan-20260628-0100",
};

const baseVersions = {
  modelVersion: NETPLAN_MODEL_VERSION,
  featureVersion: NETPLAN_FEATURE_VERSION,
  solverVersion: NETPLAN_SOLVER_VERSION,
  policyVersion: NETPLAN_POLICY_VERSION,
};

export const scenarios: NetPlanScenario[] = [
  {
    scenarioId: "np-6201",
    scenarioName: "北區 2026H2 擴張",
    planningHorizon: "2026H2",
    status: "outcome_observed",
    constraints: {
      maxBudget: 12000,
      minExpectedGrossMargin: 8000,
      minCapacityDelta: 2,
      maxAverageRisk: 0.3,
      minActionCounts: { KEEP: 1 },
      maxActionCounts: { OPEN: 2, EXIT: 1 },
      policyVersion: NETPLAN_POLICY_VERSION,
    },
    optionsByEntity: [
      { entityId: "store-021", action: "KEEP", expectedGm: 3200, budgetCost: 0, riskScore: 0.1, capacityDelta: 0, notes: "成熟門市維持" },
      { entityId: "store-077", action: "IMPROVE", expectedGm: 2600, budgetCost: 1800, riskScore: 0.25, capacityDelta: 1, notes: "翻新提升毛利" },
      { entityId: "cand-301", action: "OPEN", expectedGm: 3400, budgetCost: 5200, riskScore: 0.28, capacityDelta: 2, notes: "信義新點" },
    ],
    solveResult: {
      solverStatus: "optimal",
      objectiveValue: 8420,
      expectedGrossMargin: 9200,
      budgetUsage: 7000,
      averageRisk: 0.21,
      capacityDelta: 3,
      actionCounts: { OPEN: 1, KEEP: 1, IMPROVE: 1, MOVE: 0, EXIT: 0 },
      selectedActions: [
        { entityId: "store-021", action: "KEEP", expectedGm: 3200, budgetCost: 0, riskScore: 0.1, capacityDelta: 0, notes: "成熟門市維持" },
        { entityId: "store-077", action: "IMPROVE", expectedGm: 2600, budgetCost: 1800, riskScore: 0.25, capacityDelta: 1, notes: "翻新提升毛利" },
        { entityId: "cand-301", action: "OPEN", expectedGm: 3400, budgetCost: 5200, riskScore: 0.28, capacityDelta: 2, notes: "信義新點" },
      ],
      bindingConstraints: [{ constraint: "min_expected_gross_margin", usagePct: 100 }],
      alternatives: [
        { id: "alt-1", deltaObjective: -260, deltaBudget: -1800, deltaRisk: -0.03, actionDiff: "store-077 IMPROVE → KEEP" },
      ],
      diagnostics: [],
      solverVersion: NETPLAN_SOLVER_VERSION,
    },
    approval: {
      approvalId: "apr-6201",
      actorId: "strategy-lead-01",
      decision: "approved",
      reason: "毛利達標、風險可控，核准執行。",
      decidedAt: "2026-06-28T03:10:00Z",
      policyVersion: NETPLAN_POLICY_VERSION,
      correlationId: "corr-np-6201",
    },
    execution: {
      executionId: "exe-6201",
      actions: 3,
      executedBy: "ops-pm-04",
      executedAt: "2026-06-28T04:00:00Z",
    },
    outcome: {
      expectedGrossMargin: 9200,
      actualGrossMargin: 8740,
      variance: -460,
      variancePct: -5,
      observedAt: "2026-06-28T08:00:00Z",
    },
    statusHistory: [
      { from: "—", to: "draft", actor: "strategy-lead-01", reason: "建立情境", at: "2026-06-28T02:00:00Z", correlationId: "corr-np-6201-1" },
      { from: "draft", to: "solved", actor: "system/netplan", reason: "解算最佳計畫", at: "2026-06-28T02:30:00Z", correlationId: "corr-np-6201-2" },
      { from: "solved", to: "pending_approval", actor: "strategy-lead-01", reason: "送審", at: "2026-06-28T02:45:00Z", correlationId: "corr-np-6201-3" },
      { from: "pending_approval", to: "approved", actor: "strategy-lead-01", reason: "核准", at: "2026-06-28T03:10:00Z", correlationId: "corr-np-6201-4" },
      { from: "approved", to: "executed", actor: "ops-pm-04", reason: "執行", at: "2026-06-28T04:00:00Z", correlationId: "corr-np-6201-5" },
      { from: "executed", to: "outcome_observed", actor: "system/netplan", reason: "記錄結果", at: "2026-06-28T08:00:00Z", correlationId: "corr-np-6201-6" },
    ],
    ...baseVersions,
    correlationId: "corr-np-6201",
  },
  {
    scenarioId: "np-6202",
    scenarioName: "中區重整 2026H2",
    planningHorizon: "2026H2",
    status: "pending_approval",
    constraints: {
      maxBudget: 9000,
      minExpectedGrossMargin: 6000,
      maxAverageRisk: 0.32,
      minActionCounts: {},
      maxActionCounts: { EXIT: 2 },
      policyVersion: NETPLAN_POLICY_VERSION,
    },
    optionsByEntity: [
      { entityId: "store-145", action: "EXIT", expectedGm: -400, budgetCost: 1200, riskScore: 0.2, capacityDelta: -1, notes: "長期虧損退場" },
      { entityId: "store-152", action: "MOVE", expectedGm: 2400, budgetCost: 3600, riskScore: 0.35, capacityDelta: 0, notes: "遷址改善人流" },
      { entityId: "store-160", action: "KEEP", expectedGm: 2800, budgetCost: 0, riskScore: 0.1, capacityDelta: 0, notes: "維持" },
    ],
    solveResult: {
      solverStatus: "feasible",
      objectiveValue: 6120,
      expectedGrossMargin: 6800,
      budgetUsage: 4800,
      averageRisk: 0.28,
      capacityDelta: -1,
      actionCounts: { OPEN: 0, KEEP: 1, IMPROVE: 0, MOVE: 1, EXIT: 1 },
      selectedActions: [
        { entityId: "store-145", action: "EXIT", expectedGm: -400, budgetCost: 1200, riskScore: 0.2, capacityDelta: -1, notes: "長期虧損退場" },
        { entityId: "store-152", action: "MOVE", expectedGm: 2400, budgetCost: 3600, riskScore: 0.35, capacityDelta: 0, notes: "遷址改善人流" },
        { entityId: "store-160", action: "KEEP", expectedGm: 2800, budgetCost: 0, riskScore: 0.1, capacityDelta: 0, notes: "維持" },
      ],
      bindingConstraints: [
        { constraint: "max_average_risk", usagePct: 88 },
        { constraint: "max_budget", usagePct: 53 },
      ],
      alternatives: [
        { id: "alt-1", deltaObjective: -540, deltaBudget: -3600, deltaRisk: -0.09, actionDiff: "store-152 MOVE → KEEP" },
        { id: "alt-2", deltaObjective: -180, deltaBudget: 0, deltaRisk: 0.0, actionDiff: "store-145 EXIT → IMPROVE" },
      ],
      diagnostics: [],
      solverVersion: NETPLAN_SOLVER_VERSION,
    },
    approval: null,
    execution: null,
    outcome: null,
    statusHistory: [
      { from: "—", to: "draft", actor: "strategy-lead-02", reason: "建立情境", at: "2026-06-28T05:00:00Z", correlationId: "corr-np-6202-1" },
      { from: "draft", to: "solved", actor: "system/netplan", reason: "解算最佳計畫", at: "2026-06-28T05:30:00Z", correlationId: "corr-np-6202-2" },
      { from: "solved", to: "pending_approval", actor: "strategy-lead-02", reason: "送審", at: "2026-06-28T05:50:00Z", correlationId: "corr-np-6202-3" },
    ],
    ...baseVersions,
    correlationId: "corr-np-6202",
  },
  {
    scenarioId: "np-6203",
    scenarioName: "南區激進擴張 2026H2",
    planningHorizon: "2026H2",
    status: "infeasible",
    constraints: {
      maxBudget: 6000,
      minExpectedGrossMargin: 12000,
      minCapacityDelta: 6,
      maxAverageRisk: 0.2,
      minActionCounts: { OPEN: 3 },
      maxActionCounts: {},
      policyVersion: NETPLAN_POLICY_VERSION,
    },
    optionsByEntity: [
      { entityId: "cand-401", action: "OPEN", expectedGm: 3200, budgetCost: 4800, riskScore: 0.3, capacityDelta: 2, notes: "高租金點" },
      { entityId: "cand-402", action: "OPEN", expectedGm: 2900, budgetCost: 4200, riskScore: 0.28, capacityDelta: 2, notes: "次級商圈" },
      { entityId: "cand-403", action: "OPEN", expectedGm: 3000, budgetCost: 4500, riskScore: 0.32, capacityDelta: 2, notes: "新興區" },
    ],
    solveResult: {
      solverStatus: "infeasible",
      objectiveValue: 0,
      expectedGrossMargin: 0,
      budgetUsage: 0,
      averageRisk: 0,
      capacityDelta: 0,
      actionCounts: { OPEN: 0, KEEP: 0, IMPROVE: 0, MOVE: 0, EXIT: 0 },
      selectedActions: [],
      bindingConstraints: [],
      alternatives: [],
      diagnostics: [
        {
          violatedConstraint: "max_budget vs min_action_counts[OPEN]≥3",
          affectedStores: ["cand-401", "cand-402", "cand-403"],
          requiredRelaxation: "max_budget 需自 6000 提高至 ≥13500，或 OPEN 下限降至 1",
          businessImpact: "預算僅能開 1 店，無法同時滿足 3 店與毛利門檻。",
          suggestedAction: "提高預算或拆成兩期，回到 Scenario Builder 建立新 draft。",
        },
        {
          violatedConstraint: "max_average_risk 0.20",
          affectedStores: ["cand-401", "cand-403"],
          requiredRelaxation: "平均風險上限需放寬至 ≥0.30",
          businessImpact: "候選點風險偏高，現有上限下無可行組合。",
          suggestedAction: "重新評估候選點風險或放寬風險上限。",
        },
      ],
      solverVersion: NETPLAN_SOLVER_VERSION,
    },
    approval: null,
    execution: null,
    outcome: null,
    statusHistory: [
      { from: "—", to: "draft", actor: "strategy-lead-03", reason: "建立情境", at: "2026-06-28T06:00:00Z", correlationId: "corr-np-6203-1" },
      { from: "draft", to: "infeasible", actor: "system/netplan", reason: "限制衝突，無可行解", at: "2026-06-28T06:20:00Z", correlationId: "corr-np-6203-2" },
    ],
    ...baseVersions,
    correlationId: "corr-np-6203",
  },
];

export function scenarioStatusTone(status: NetPlanScenarioStatus): StatusTone {
  if (status === "approved" || status === "executed" || status === "outcome_observed" || status === "closed") return "green";
  if (status === "pending_approval" || status === "solved") return "blue";
  if (status === "infeasible") return "orange";
  if (status === "rejected") return "red";
  return "gray";
}

export function solverStatusTone(status: SolverStatus): StatusTone {
  if (status === "optimal") return "green";
  if (status === "feasible") return "blue";
  return "orange";
}

export function approvalLabel(s: NetPlanScenario): string {
  if (s.approval) return s.approval.decision === "approved" ? "已核准" : "退回";
  if (s.status === "pending_approval") return "待核准";
  if (s.status === "solved") return "未送審";
  return "—";
}

export function formatActionCounts(counts: ActionCounts): string {
  return (["OPEN", "KEEP", "IMPROVE", "MOVE", "EXIT"] as NetworkAction[])
    .map((a) => `${a} ${counts[a]}`)
    .join(" · ");
}

export function selectedFromQuery(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
