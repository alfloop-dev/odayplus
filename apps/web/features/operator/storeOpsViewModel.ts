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
  severities: Severity[];
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
  new: "新進",
  triaged: "已分類",
  assigned: "已指派",
  inprogress: "處置中",
  executed: "已執行",
  observing: "觀察中",
  outcomeready: "成效待判斷",
  closed: "已結案",
  waitingevidence: "待補證據",
  waitingapproval: "待核准",
  escalated: "已升級",
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
  low: "低",
  medium: "中",
  high: "高",
  critical: "嚴重",
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

const SEVERITY_WEIGHTS: Record<Severity, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
};

export function filterStoreOpsIssues(issues: Issue[], filters: StoreOpsIssueFilters, roleId: OperatorRoleId): Issue[] {
  const query = filters.search.trim().toLowerCase();
  const statusSet = new Set(filters.statuses);
  const sourceSet = new Set(filters.sources);
  const severitySet = new Set(filters.severities);

  return issues
    .filter((issue) => {
      if (filters.mineOnly && issue.ownerRoleId !== roleId) return false;
      if (statusSet.size > 0 && !statusSet.has(issue.status)) return false;
      if (sourceSet.size > 0 && !sourceSet.has(issue.source)) return false;
      if (severitySet.size > 0 && !severitySet.has(issue.severity)) return false;
      if (!query) return true;

      return [issue.id, issue.title, issue.storeName, issue.summary, issue.ownerName, getStatusLabel(issue.status)]
        .join(" ")
        .toLowerCase()
        .includes(query);
    })
    .sort((first, second) => {
      const firstWeight = SEVERITY_WEIGHTS[first.severity] ?? 0;
      const secondWeight = SEVERITY_WEIGHTS[second.severity] ?? 0;
      if (firstWeight !== secondWeight) {
        return secondWeight - firstWeight;
      }
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
  if (!issue) return "尚未選擇事件。";

  switch (issue.status) {
    case "new":
      return "完成根因分類，必要時記錄影像調閱目的，並在期限內指派現場負責人。";
    case "waitingapproval":
      return "確認核准依賴；核准者決策前，保留現場處置並暫緩執行。";
    case "observing":
      return "持續觀察至下一個需求時段，比較客服趨勢後再判斷成效。";
    case "closed":
      return "事件已結案，保留稽核證據；新訊號超過閾值時才重新開啟審查。";
    default:
      return "依目前負責人、證據強度與期限壓力，繼續下一步處置。";
  }
}

export function getPrimaryActionLabel(issue: Issue | undefined): string {
  if (!issue) return "No action";

  switch (issue.status) {
    case "new":
      return "完成 Triage";
    case "triaged":
      return "指派負責人";
    case "assigned":
      return "建立處置";
    case "inprogress":
      return "提交現場回報";
    case "executed":
      return "開始觀察";
    case "observing":
      return "判斷成效";
    case "outcomeready":
      return "成效結案審查";
    case "waitingapproval":
      return "查看核准";
    case "waitingevidence":
      return "要求補證據";
    case "escalated":
      return "升級處理";
    case "closed":
      return "重新開啟審查";
    default:
      return "更新事件";
  }
}

export function getSecondaryActionLabels(issue: Issue | undefined): string[] {
  if (!issue) return [];

  const shared = ["指派負責人", "建立處置", "新增稽核備註"];
  if (issue.status === "new") return ["回覆審查", "填寫影像調閱目的", ...shared];
  if (issue.status === "waitingapproval") return ["查看核准", "升級處理", "新增稽核備註"];
  if (issue.status === "observing") return ["檢視現場回報", "判斷成效", "轉交事件"];
  if (issue.status === "closed") return ["檢視稽核資料", "匯出稽核資料"];
  return [...shared, "升級處理"];
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
