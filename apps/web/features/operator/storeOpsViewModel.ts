import type {
  AuditEvent,
  EvidenceItem,
  EvidenceKind,
  EvidencePolarity,
  Issue,
  IssueStatus,
  OperatorRoleId,
  Severity,
  Store,
  StoreLightStatus,
} from "./types";

export type StoreOpsTone = "neutral" | "info" | "success" | "warning" | "danger" | "accent";
export type StoreOpsSource = Issue["source"];
export type StoreOpsEvidenceTab = {
  id: EvidenceKind;
  label: string;
  shortLabel: string;
};

export type StoreOpsIssueFilters = {
  search: string;
  statuses: IssueStatus[];
  sources: StoreOpsSource[];
  mineOnly: boolean;
};

export type StoreOpsProgressStep = {
  id: IssueStatus;
  label: string;
  state: "complete" | "active" | "pending" | "exception";
};

export type StoreOpsTrendPoint = {
  label: string;
  value: number;
  tone: StoreOpsTone;
};

export type StoreOpsRelatedItem = {
  id: string;
  label: string;
  value: string;
  tone: StoreOpsTone;
};

export const STORE_OPS_STABLE_ISSUE_IDS = ["ISS-1024", "ISS-1021", "ISS-1008"] as const;

export const STORE_OPS_STATUS_ORDER: IssueStatus[] = [
  "new",
  "triaged",
  "assigned",
  "inprogress",
  "executed",
  "observing",
  "outcomeready",
  "closed",
  "waitingevidence",
  "waitingapproval",
  "escalated",
];

export const STORE_OPS_LIFECYCLE_STATUSES: IssueStatus[] = [
  "new",
  "triaged",
  "assigned",
  "inprogress",
  "executed",
  "observing",
  "outcomeready",
  "closed",
];

export const STORE_OPS_EXCEPTION_STATUSES: IssueStatus[] = ["waitingevidence", "waitingapproval", "escalated"];

export const STORE_OPS_EVIDENCE_TABS: StoreOpsEvidenceTab[] = [
  { id: "googleReview", label: "Google review", shortLabel: "Review" },
  { id: "csCase", label: "CS cases", shortLabel: "CS" },
  { id: "camera", label: "Camera", shortLabel: "Camera" },
  { id: "iot", label: "IoT", shortLabel: "IoT" },
  { id: "payment", label: "Payment", shortLabel: "Pay" },
  { id: "forecastOps", label: "ForecastOps", shortLabel: "Four-light" },
  { id: "cleaning", label: "Cleaning", shortLabel: "Clean" },
];

export const STORE_OPS_STATUS_LABELS: Record<IssueStatus, string> = {
  new: "New",
  triaged: "Triaged",
  assigned: "Assigned",
  inprogress: "In progress",
  executed: "Executed",
  observing: "Observing",
  outcomeready: "Outcome ready",
  closed: "Closed",
  waitingevidence: "Waiting evidence",
  waitingapproval: "Waiting approval",
  escalated: "Escalated",
};

export const STORE_OPS_SOURCE_LABELS: Record<StoreOpsSource, string> = {
  googleReview: "Google Review",
  csCase: "CS Case",
  camera: "Camera",
  iot: "IoT",
  payment: "Payment",
  forecastOps: "ForecastOps",
  cleaning: "Cleaning",
  multiSignal: "Multi-signal",
};

export const STORE_OPS_SEVERITY_LABELS: Record<Severity, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
};

export const STORE_OPS_LIGHT_LABELS: Record<keyof Store["lights"], string> = {
  demand: "Demand",
  operations: "Operations",
  staffing: "Staffing",
  margin: "Margin",
};

const STORE_OPS_STABLE_ISSUE_SET = new Set<string>(STORE_OPS_STABLE_ISSUE_IDS);

export function getStatusLabel(status: IssueStatus): string {
  return STORE_OPS_STATUS_LABELS[status];
}

export function getSourceLabel(source: StoreOpsSource): string {
  return STORE_OPS_SOURCE_LABELS[source];
}

export function getSeverityLabel(severity: Severity): string {
  return STORE_OPS_SEVERITY_LABELS[severity];
}

export function getStatusTone(status: IssueStatus): StoreOpsTone {
  switch (status) {
    case "closed":
      return "success";
    case "observing":
    case "outcomeready":
      return "info";
    case "waitingapproval":
    case "waitingevidence":
      return "warning";
    case "escalated":
      return "danger";
    case "new":
      return "danger";
    case "inprogress":
    case "executed":
      return "accent";
    default:
      return "neutral";
  }
}

export function getSeverityTone(severity: Severity): StoreOpsTone {
  switch (severity) {
    case "critical":
      return "danger";
    case "high":
      return "warning";
    case "medium":
      return "info";
    default:
      return "success";
  }
}

export function getPolarityTone(polarity: EvidencePolarity): StoreOpsTone {
  switch (polarity) {
    case "supporting":
      return "success";
    case "contrary":
      return "warning";
    default:
      return "neutral";
  }
}

export function getLightTone(light: StoreLightStatus): StoreOpsTone {
  switch (light) {
    case "green":
      return "success";
    case "yellow":
      return "warning";
    case "red":
      return "danger";
    default:
      return "neutral";
  }
}

export function getSourceTone(source: StoreOpsSource): StoreOpsTone {
  switch (source) {
    case "multiSignal":
      return "danger";
    case "camera":
    case "iot":
      return "accent";
    case "forecastOps":
      return "info";
    case "cleaning":
      return "warning";
    default:
      return "neutral";
  }
}

export function getIssueEvidence(issue: Issue | undefined, evidence: EvidenceItem[]): EvidenceItem[] {
  if (!issue) return [];
  const evidenceById = new Map(evidence.map((item) => [item.id, item]));
  return issue.evidenceIds.map((id) => evidenceById.get(id)).filter((item): item is EvidenceItem => Boolean(item));
}

export function getStoreForIssue(issue: Issue | undefined, stores: Store[]): Store | undefined {
  if (!issue) return undefined;
  return stores.find((store) => store.id === issue.storeId);
}

export function getEvidenceByKind(evidence: EvidenceItem[], kind: EvidenceKind): EvidenceItem[] {
  return evidence.filter((item) => item.kind === kind);
}

export function hasLockedCameraEvidence(evidence: EvidenceItem[]): boolean {
  return evidence.some((item) => item.kind === "camera" && Boolean(item.lockedReason));
}

export function filterStoreOpsIssues(issues: Issue[], filters: StoreOpsIssueFilters, roleId: OperatorRoleId): Issue[] {
  const query = filters.search.trim().toLowerCase();
  const statusSet = new Set(filters.statuses);
  const sourceSet = new Set(filters.sources);

  return issues
    .filter((issue) => {
      if (filters.mineOnly && issue.ownerRoleId !== roleId) return false;
      if (statusSet.size > 0 && !statusSet.has(issue.status)) return false;
      if (sourceSet.size > 0 && !sourceSet.has(issue.source)) return false;
      if (!query) return true;

      return [issue.id, issue.title, issue.storeName, issue.summary, issue.ownerName, getStatusLabel(issue.status)]
        .join(" ")
        .toLowerCase()
        .includes(query);
    })
    .sort((first, second) => {
      const firstStable = STORE_OPS_STABLE_ISSUE_SET.has(first.id) ? 0 : 1;
      const secondStable = STORE_OPS_STABLE_ISSUE_SET.has(second.id) ? 0 : 1;
      if (firstStable !== secondStable) return firstStable - secondStable;

      const statusDelta = STORE_OPS_STATUS_ORDER.indexOf(first.status) - STORE_OPS_STATUS_ORDER.indexOf(second.status);
      if (statusDelta !== 0) return statusDelta;

      return new Date(first.slaDueAt).getTime() - new Date(second.slaDueAt).getTime();
    });
}

export function resolveSelectedIssue(
  issues: Issue[],
  selectedIssueId: string | undefined,
  filteredIssues: Issue[],
): Issue | undefined {
  return (
    issues.find((issue) => issue.id === selectedIssueId) ??
    issues.find((issue) => issue.id === "ISS-1024") ??
    filteredIssues[0] ??
    issues[0]
  );
}

export function getProgressSteps(status: IssueStatus): StoreOpsProgressStep[] {
  if (STORE_OPS_EXCEPTION_STATUSES.includes(status)) {
    return STORE_OPS_LIFECYCLE_STATUSES.map((step) => ({
      id: step,
      label: getStatusLabel(step),
      state: (step === "triaged" || step === "assigned" ? "complete" : "pending") as StoreOpsProgressStep["state"],
    })).concat({
      id: status,
      label: getStatusLabel(status),
      state: "exception",
    });
  }

  const activeIndex = STORE_OPS_LIFECYCLE_STATUSES.indexOf(status);
  return STORE_OPS_LIFECYCLE_STATUSES.map((step, index) => ({
    id: step,
    label: getStatusLabel(step),
    state: index < activeIndex ? "complete" : index === activeIndex ? "active" : "pending",
  }));
}

export function getEvidenceStrength(evidence: EvidenceItem[]) {
  const supporting = evidence.filter((item) => item.polarity === "supporting");
  const contrary = evidence.filter((item) => item.polarity === "contrary");
  const neutral = evidence.filter((item) => item.polarity === "neutral");
  const averageConfidence =
    evidence.length > 0
      ? Math.round((evidence.reduce((total, item) => total + item.confidence, 0) / evidence.length) * 100)
      : 0;

  return {
    supportingCount: supporting.length,
    contraryCount: contrary.length,
    neutralCount: neutral.length,
    averageConfidence,
  };
}

export function getTrendPoints(issue: Issue | undefined, evidence: EvidenceItem[]): StoreOpsTrendPoint[] {
  if (!issue) return [];

  if (issue.id === "ISS-1024") {
    return [
      { label: "Reviews", value: 91, tone: "danger" },
      { label: "CS", value: 86, tone: "warning" },
      { label: "Clean", value: 80, tone: "danger" },
      { label: "Queue", value: 78, tone: "warning" },
    ];
  }

  if (issue.id === "ISS-1021") {
    return [
      { label: "HVAC", value: 94, tone: "danger" },
      { label: "Payment", value: 28, tone: "success" },
      { label: "Approval", value: 82, tone: "warning" },
      { label: "Peak", value: 64, tone: "info" },
    ];
  }

  if (issue.id === "ISS-1008") {
    return [
      { label: "Staff", value: 84, tone: "danger" },
      { label: "CS wait", value: 54, tone: "warning" },
      { label: "Shift", value: 48, tone: "info" },
      { label: "Lunch", value: 69, tone: "accent" },
    ];
  }

  return evidence.slice(0, 4).map((item) => ({
    label: item.sourceLabel,
    value: Math.round(item.confidence * 100),
    tone: getPolarityTone(item.polarity),
  }));
}

export function getAiRecommendation(issue: Issue | undefined): string {
  if (!issue) return "No issue selected.";

  switch (issue.status) {
    case "new":
      return "Complete triage, record camera purpose if needed, then assign field owner before the SLA window.";
    case "waitingapproval":
      return "Review approval dependency and keep field action staged until the required approver decides.";
    case "observing":
      return "Keep the issue in observation through the next demand window and compare CS trend before outcome review.";
    case "closed":
      return "Issue is closed. Retain audit evidence and reopen only if a fresh signal crosses threshold.";
    default:
      return "Continue lifecycle action based on the current owner, evidence strength, and SLA pressure.";
  }
}

export function getPrimaryActionLabel(issue: Issue | undefined): string {
  if (!issue) return "No action";

  switch (issue.status) {
    case "new":
      return "完成 Triage";
    case "triaged":
      return "指派 Owner";
    case "assigned":
    case "inprogress":
      return "建立 Field Action";
    case "executed":
      return "開始 Observation";
    case "observing":
      return "檢視 Outcome";
    case "outcomeready":
      return "關閉 Issue";
    case "waitingapproval":
      return "查看 Approval";
    case "waitingevidence":
      return "Request Evidence";
    case "escalated":
      return "升級處理";
    case "closed":
      return "Reopen Review";
    default:
      return "Update Issue";
  }
}

export function getSecondaryActionLabels(issue: Issue | undefined): string[] {
  if (!issue) return [];

  const shared = ["Assign owner", "Create action", "Add audit note"];
  if (issue.status === "new") return ["Reply review", "Request camera purpose", ...shared];
  if (issue.status === "waitingapproval") return ["Open approval", "Escalate", "Add audit note"];
  if (issue.status === "observing") return ["Open field report", "Outcome review", "Transfer"];
  if (issue.status === "closed") return ["View packet", "Export audit"];
  return [...shared, "Escalate"];
}

export function getRelatedItems(issue: Issue | undefined): StoreOpsRelatedItem[] {
  if (!issue) return [];

  const items: StoreOpsRelatedItem[] = [];

  if (issue.relatedApprovalId) {
    items.push({
      id: issue.relatedApprovalId,
      label: "Approval",
      value: issue.relatedApprovalId,
      tone: issue.status === "waitingapproval" ? "warning" : "info",
    });
  }

  if (issue.relatedGrowthId) {
    items.push({
      id: issue.relatedGrowthId,
      label: "Growth",
      value: issue.relatedGrowthId,
      tone: "accent",
    });
  }

  items.push(
    {
      id: `${issue.id}-action`,
      label: "Action",
      value: issue.status === "new" ? "Triage pending" : "Field action staged",
      tone: issue.status === "new" ? "warning" : "info",
    },
    {
      id: `${issue.id}-observation`,
      label: "Observation",
      value: issue.status === "observing" ? "Active window" : "Not active",
      tone: issue.status === "observing" ? "success" : "neutral",
    },
    {
      id: `${issue.id}-outcome`,
      label: "Outcome",
      value: issue.status === "outcomeready" || issue.status === "closed" ? "Ready" : "Pending",
      tone: issue.status === "closed" ? "success" : "neutral",
    },
  );

  return items;
}

export function getLocalAuditEvents(issue: Issue | undefined, auditEvents: AuditEvent[]): AuditEvent[] {
  if (!issue) return [];

  return auditEvents
    .filter((event) => {
      const metadataIssueId = event.metadata?.issueId;
      return (
        event.targetId === issue.id ||
        event.targetId === issue.relatedApprovalId ||
        metadataIssueId === issue.id ||
        event.message.includes(issue.id)
      );
    })
    .sort((first, second) => new Date(second.occurredAt).getTime() - new Date(first.occurredAt).getTime());
}

export function formatCompactDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  const hour = String(date.getUTCHours()).padStart(2, "0");
  const minute = String(date.getUTCMinutes()).padStart(2, "0");

  return `${month}/${day} ${hour}:${minute}Z`;
}

export function formatSla(value: string, nowValue = "2026-07-05T08:00:00.000Z"): string {
  const dueAt = new Date(value).getTime();
  const now = new Date(nowValue).getTime();
  if (Number.isNaN(dueAt) || Number.isNaN(now)) return value;

  const deltaMinutes = Math.round((dueAt - now) / 60000);
  const absMinutes = Math.abs(deltaMinutes);
  const hours = Math.floor(absMinutes / 60);
  const minutes = absMinutes % 60;
  const compact = hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;

  return deltaMinutes >= 0 ? `${compact} left` : `${compact} overdue`;
}
