"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { createOdpApiClient } from "@oday-plus/openapi-client";
import dynamic from "next/dynamic";
import {
  Button,
  Chip,
  SectionPanel,
  StatusBadge,
  TabBar,
  type Tone,
} from "./components";
import type { GovernanceWorkspaceProps } from "./GovernanceWorkspace";
import {
  normalizeGovernanceApprovals,
  normalizeGovernanceAuditRows,
  normalizeGovernanceDecisionRows,
} from "./governance/governanceEnvelope";
import { readGovernanceViewState, serializeGovernanceViewState, type GovernanceViewState } from "./governance/governanceUrlState";
import type { NetworkFindAreasWorkspaceProps } from "./NetworkFindAreasWorkspace";
import type { ColdIntakeDetailProps } from "./network/intake/ColdIntakeDetail";
import type { InterventionWorkspaceProps } from "./interventions/InterventionWorkspace";
import { NETWORK_TAB_IDS, parseNetworkTabIndex } from "./network/networkUrlState";
import { ISSUE_FIXTURES } from "./fixtures";
import {
  DEFAULT_OPERATOR_ROLE_ID,
  DEFAULT_WORKSPACE_ID,
  OPERATOR_ROLES,
  WORKSPACES,
  getOperatorRole,
  getWorkspace,
  isWorkspaceAllowed,
  type OperatorRoleId,
  type WorkspaceId,
} from "./navigation";
import type { NetworkFindAreasBindings } from "./network/networkFindAreasLoader";
import { OperatorDataUnavailableGate } from "./OperatorDataUnavailableGate";
import {
  classifyLoadFailure,
  inspectOperatorShellPayload,
  operatorFixturesAllowed,
  payloadContainsSeedData,
  toUnavailableOperatorStatus,
  type OperatorDataAvailability,
  type OperatorLoadFailure,
} from "./operatorDataMode";
import { resolveOperatorEnvironment } from "./operatorEnvironment";
import styles from "./operator.module.css";
import type { StoreOpsWorkflowDialogType } from "./storeOpsWorkflowTypes";
import {
  TodayWorkspace as ApiTodayWorkspace,
  normalizeShellEnvelope,
  type OperatorShellEnvelope,
  type ShellTarget,
} from "./TodayWorkspace";
import type { Issue } from "./types";
import { hasCanonicalScoreSession } from "./network/networkReadValidation";
import { operatorSecurityHeaders } from "./operatorSecurityHeaders";
import {
  ADMIN_PATH,
  PASSWORD_CHANGE_PATH,
  classifyAccessDenial,
  signOutOperator,
} from "./operatorSession";

// Business owners load only when their original admitted branch is rendered.
// Full-page Task/Notification routes share this session shell without loading
// dormant Store Ops, Growth/PriceOps, or workflow dialog implementations.
const DesignStoreOpsWorkspace = dynamic<ComponentProps<typeof import("./DesignAlignedWorkspaces").DesignStoreOpsWorkspace>>(
  () => import("./DesignAlignedWorkspaces").then((mod) => mod.DesignStoreOpsWorkspace),
  { ssr: false, loading: () => <div aria-live="polite" role="status">門市營運工作區載入中…</div> },
);

const GrowthWorkspace = dynamic<ComponentProps<typeof import("./GrowthWorkspace").GrowthWorkspace>>(
  () => import("./GrowthWorkspace").then((mod) => mod.GrowthWorkspace),
  { ssr: false, loading: () => <div aria-live="polite" role="status">營收成長工作區載入中…</div> },
);

const StoreOpsWorkflowDialogs = dynamic<ComponentProps<typeof import("./StoreOpsWorkflowDialogs").StoreOpsWorkflowDialogs>>(
  () => import("./StoreOpsWorkflowDialogs").then((mod) => mod.StoreOpsWorkflowDialogs),
  { ssr: false, loading: () => <div aria-live="polite" role="status">營運工作流程載入中…</div> },
);

const ShellNotificationCenter = dynamic<ComponentProps<typeof import("./ShellNotificationCenter").ShellNotificationCenter>>(
  () => import("./ShellNotificationCenter").then((mod) => mod.ShellNotificationCenter),
  { ssr: false, loading: () => <div aria-live="polite" role="status">通知載入中…</div> },
);

const ShellTaskCenter = dynamic<ComponentProps<typeof import("./ShellTaskCenter").ShellTaskCenter>>(
  () => import("./ShellTaskCenter").then((mod) => mod.ShellTaskCenter),
  { ssr: false, loading: () => <div aria-live="polite" role="status">任務中心載入中…</div> },
);

const ShellNotificationPage = dynamic<ComponentProps<typeof import("./ShellNotificationPage").ShellNotificationPage>>(
  () => import("./ShellNotificationPage").then((mod) => mod.ShellNotificationPage),
  { ssr: false, loading: () => <div aria-live="polite" role="status">通知中心載入中…</div> },
);

// Governance (including its admin controllers) is dormant on Today/Network.
// Load it only when selected, without changing its server-verified authority
// or any of its account/config controls. The shell is client-data driven.
const GovernanceWorkspace = dynamic<GovernanceWorkspaceProps>(
  () => import("./GovernanceWorkspace").then((mod) => mod.GovernanceWorkspace),
  {
    ssr: false,
    loading: () => <div aria-live="polite" role="status">治理工作區載入中…</div>,
  },
);

const NetworkFindAreasWorkspace = dynamic<NetworkFindAreasWorkspaceProps>(
  () => import("./NetworkFindAreasWorkspace").then((mod) => mod.NetworkFindAreasWorkspace),
  {
    ssr: false,
    loading: () => <div aria-live="polite" role="status">展店與店網工作區載入中…</div>,
  },
);

const ColdIntakeDetail = dynamic<ColdIntakeDetailProps>(
  () => import("./network/intake/ColdIntakeDetail").then((mod) => mod.ColdIntakeDetail),
  { ssr: false, loading: () => <div role="status">收件詳情載入中…</div> },
);

const InterventionWorkspace = dynamic<InterventionWorkspaceProps>(
  () => import("./interventions/InterventionWorkspace").then((mod) => mod.InterventionWorkspace),
  { ssr: false, loading: () => <div role="status">干預生命週期載入中…</div> },
);

const roleStorageKey = "oday.operator.role";
const workspaceStorageKey = "oday.operator.workspace";
// The browser budget must exceed the Web BFF's own 10s upstream timeout plus
// its session lookup and service-identity work, so a slow API surfaces as the
// BFF's structured 504 (with correlation id) instead of a bare client abort.
const operatorBootstrapTimeoutMs = 20_000;
// Retries absorb a scale-to-zero cold start. Dev Cloud Run logs show the API
// container needs ~24-29s before uvicorn serves, while the BFF gives up after
// 10s per request, so four attempts (pauses 0.3s, 2s, 2s) cover ~45s. Every
// attempt reuses the correlation id so they trace as one operator request.
const operatorBootstrapRetryableStatuses = new Set([502, 503, 504]);
/** Pauses before each retry; exported so tests can shorten them. */
export const operatorBootstrapRetryPolicy = { delaysMs: [300, 2_000, 2_000] };
const operatorBootstrapWarmingUpDetail =
  "營運資料服務正在啟動（閒置一段時間後第一次使用約需 30 秒），請稍候，不需要重新整理。";

class RetryableBootstrapStatus extends Error {
  constructor(readonly status: number) {
    super(`Operator bootstrap returned ${status}`);
    this.name = "RetryableBootstrapStatus";
  }
}

/**
 * Transport failures worth one more attempt: a timeout or network error while
 * fetching *or* while streaming the body (the BFF streams the upstream body,
 * so headers can arrive before the body fails), or a retryable gateway status.
 * Malformed JSON, permission denials and other statuses are not retried.
 */
function isRetryableBootstrapFailure(error: unknown): boolean {
  if (error instanceof RetryableBootstrapStatus) return true;
  if (error instanceof TypeError) return true;
  return error instanceof Error && error.name === "TimeoutError";
}

const notifications = [
  {
    title: "SLA 即將到期",
    detail: "ISS-1024 需在 58 分鐘內完成 Triage。",
    tone: "danger" as Tone,
  },
  {
    title: "核准中心新增",
    detail: "SiteScore APR-501 已送出複審。",
    tone: "warning" as Tone,
  },
  {
    title: "模型快照更新",
    detail: "ForecastOps v2.6 完成 06:00 refresh。",
    tone: "info" as Tone,
  },
];

type TaskCenterLoadState = "idle" | "loading" | "ready" | "fallback" | "empty" | "error";
type TaskCenterSource = "api" | "fixture";

type OperatorTask = {
  dueLabel: string;
  href: string;
  id: string;
  owner: string;
  priority: string;
  source: TaskCenterSource;
  status: string;
  summary: string;
  title: string;
  tone: Tone;
  workspace?: WorkspaceId;
};

type CommandGroup = "pages" | "entities" | "actions";

type CommandPaletteItem = {
  execute: () => void;
  group: CommandGroup;
  id: string;
  keywords: string[];
  subtitle: string;
  title: string;
  tone?: Tone;
};

const taskCenterFixtures: OperatorTask[] = [
  {
    id: "TASK-401",
    title: "完成 ISS-1024 Triage",
    summary: "支付失敗率與 Google 負評已合併，需確認根因與下一步 owner。",
    owner: "營運",
    status: "SLA 58m",
    priority: "P0",
    dueLabel: "58m",
    tone: "danger",
    workspace: "store",
    href: "/operator?ws=store",
    source: "fixture",
  },
  {
    id: "TASK-418",
    title: "SiteScore WAIT 複審",
    summary: "CS-1002 候選點分數 76，競品密度高，需要主管決策理由。",
    owner: "展店",
    status: "Review",
    priority: "P1",
    dueLabel: "2h",
    tone: "warning",
    workspace: "govern",
    href: "/operator?ws=govern",
    source: "fixture",
  },
  {
    id: "TASK-433",
    title: "補齊 RV-701 路口可視性佐證",
    summary: "Listing Radar 已去重，仍缺街角照片與招牌可視性 evidence。",
    owner: "展店",
    status: "Need data",
    priority: "P1",
    dueLabel: "Today",
    tone: "warning",
    workspace: "network",
    href: "/operator?ws=network",
    source: "fixture",
  },
  {
    id: "TASK-452",
    title: "會員回流活動毛利保護確認",
    summary: "夜間券建議 20:00-23:00 投放，需確認折扣上限與衝突。",
    owner: "行銷",
    status: "Draft",
    priority: "P2",
    dueLabel: "Today",
    tone: "info",
    workspace: "growth",
    href: "/operator?ws=growth",
    source: "fixture",
  },
];

const commandPageTargets: Array<{ href: string; keywords: string[]; subtitle: string; title: string }> = [
  { title: "營運管理", subtitle: "跨模組狀態與最近決策", href: "/operator", keywords: ["home", "overview"] },
  { title: "任務中心", subtitle: "個人與團隊待辦", href: "/operator", keywords: ["tasks", "todo"] },
  { title: "全域搜尋", subtitle: "門市、候選點、決策、模型版本", href: "/operator", keywords: ["search"] },
  { title: "營運監控", subtitle: "四燈、預測帶與根因證據", href: "/operator?ws=store", keywords: ["operations"] },
  { title: "空間治理", subtitle: "HeatZone 合併／拆分提案審批與 Lineage", href: "/operator?ws=network&tab=composition", keywords: ["composition", "merge", "split", "heatzone", "lineage"] },
  { title: "展店選址", subtitle: "HeatZone、Listing、SiteScore", href: "/operator?ws=network", keywords: ["expansion"] },
  { title: "干預決策", subtitle: "干預建議與觀察窗", href: "/operator?ws=store", keywords: ["interventions"] },
  { title: "定價", subtitle: "調價方案與保護線", href: "/operator?ws=growth", keywords: ["pricing"] },
  { title: "廣告增益", subtitle: "treatment/control 與 iROMI", href: "/operator?ws=growth", keywords: ["adlift"] },
  { title: "門市估值", subtitle: "AVM 公允價值與資料室", href: "/operator?ws=network", keywords: ["avm"] },
  { title: "網路規劃", subtitle: "NetPlan 情境與 solver", href: "/operator?ws=network&tab=rebalance", keywords: ["netplan"] },
  { title: "模型與學習", subtitle: "模型版本、release、rollback", href: "/operator?ws=govern", keywords: ["learning"] },
  { title: "稽核軌跡", subtitle: "決策時間軸與證據包", href: "/operator?ws=govern", keywords: ["audit"] },
];

const commandGroupLabels: Record<CommandGroup, string> = {
  pages: "Pages",
  entities: "Entities",
  actions: "Quick actions",
};

function isOperatorRoleId(value: string): value is OperatorRoleId {
  return OPERATOR_ROLES.some((role) => role.id === value);
}

function isWorkspaceId(value: string): value is WorkspaceId {
  return WORKSPACES.some((workspace) => workspace.id === value);
}

function coerceText(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function getRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function getNestedText(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    const text = coerceText(value);
    if (text) return text;
  }
  return "";
}

function inferWorkspace(record: Record<string, unknown>): WorkspaceId | undefined {
  const direct = getNestedText(record, ["workspace", "workspaceId", "module", "routeKey", "area"]).toLowerCase();
  if (WORKSPACES.some((workspace) => workspace.id === direct)) return direct as WorkspaceId;

  const artifacts = Array.isArray(record.artifacts) ? record.artifacts.join(" ") : "";
  const text = `${direct} ${artifacts} ${getNestedText(record, ["title", "summary", "summary_zh", "description", "next"])}`.toLowerCase();
  if (text.includes("store") || text.includes("operator") || text.includes("forecast") || text.includes("issue")) return "store";
  if (text.includes("growth") || text.includes("price") || text.includes("adlift") || text.includes("campaign")) return "growth";
  if (text.includes("network") || text.includes("site") || text.includes("listing") || text.includes("heatzone") || text.includes("netplan")) return "network";
  if (text.includes("govern") || text.includes("audit") || text.includes("approval") || text.includes("learning")) return "govern";
  return undefined;
}

function getTaskTone(status: string, priority: string): Tone {
  const value = `${status} ${priority}`.toLowerCase();
  if (value.includes("blocked") || value.includes("overdue") || value.includes("p0") || value.includes("critical")) return "danger";
  if (value.includes("review") || value.includes("wait") || value.includes("need") || value.includes("p1")) return "warning";
  if (value.includes("done") || value.includes("complete") || value.includes("closed")) return "success";
  if (value.includes("progress") || value.includes("draft") || value.includes("todo")) return "info";
  return "neutral";
}

function getTaskStatusLabel(status: string): string {
  const value = status.trim();
  if (!value) return "Todo";
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function normalizeTaskRecord(value: unknown, index: number, source: TaskCenterSource): OperatorTask | null {
  const record = getRecord(value);
  if (!record) return null;

  const id = getNestedText(record, ["id", "task_id", "taskId", "key", "uuid"]) || `TASK-${index + 1}`;
  const title =
    getNestedText(record, ["title", "name", "summary_zh", "summary", "message", "next"]) || `Task ${index + 1}`;
  const summary =
    getNestedText(record, ["description", "summary", "summary_zh", "next", "message"]) || "No task summary available.";
  const owner = getNestedText(record, ["owner", "assignee", "agent", "role"]) || "Unassigned";
  const rawStatus = getNestedText(record, ["status", "state", "stage"]) || "todo";
  const priority = getNestedText(record, ["priority", "severity", "rank"]) || "P2";
  const dueLabel =
    getNestedText(record, ["dueLabel", "due_label", "sla", "due", "due_at", "dueAt", "deadline"]) || "No SLA";
  const workspace = inferWorkspace(record);
  const href =
    getNestedText(record, ["href", "url"]) ||
    (workspace ? `/operator?ws=${workspace}` : "/operator");
  const tone = getTaskTone(rawStatus, priority);

  return {
    dueLabel,
    href,
    id,
    owner,
    priority,
    source,
    status: getTaskStatusLabel(rawStatus),
    summary,
    title,
    tone,
    workspace,
  };
}

function normalizeTasksPayload(payload: unknown, source: TaskCenterSource): OperatorTask[] {
  const record = getRecord(payload);
  const rawItems =
    (Array.isArray(payload) && payload) ||
    (Array.isArray(record?.items) && record.items) ||
    (Array.isArray(record?.tasks) && record.tasks) ||
    (Array.isArray(record?.data) && record.data) ||
    (Array.isArray(record?.results) && record.results) ||
    [];

  return rawItems
    .map((item, index) => normalizeTaskRecord(item, index, source))
    .filter((item): item is OperatorTask => Boolean(item));
}

function commandSearchText(item: CommandPaletteItem): string {
  return [item.title, item.subtitle, item.group, ...item.keywords].join(" ").toLowerCase();
}

function toDomSafeId(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "-");
}

function newCorrelationId(scope: string): string {
  const random =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2, 14);
  return `corr-${scope}-${random}`;
}

function dataModeBannerCopy(
  status: OperatorDataAvailability,
  fixturesAllowed: boolean,
): { detail: string; label: string } {
  if (status === "ready") {
    if (fixturesAllowed) {
      return { label: "開發／驗收 API 資料", detail: "目前顯示開發或驗收環境的 API 資料。" };
    }
    return { label: "Live API", detail: "目前顯示的是經驗證的正式營運資料。" };
  }
  if (fixturesAllowed) {
    return { label: "Local fixture mode", detail: "本機示範資料，僅供開發與測試使用。" };
  }
  if (status === "loading") {
    return { label: "正式資料載入中", detail: "資料載入完成前不會顯示任何示範或測試資料。" };
  }
  return { label: "正式資料未就緒", detail: "目前無法顯示正式營運資料；系統不會以示範資料代替。" };
}

export function OperatorConsole({
  deploymentEnvironment,
  searchParams = {},
  shellView,
  notificationPreferences = false,
  notificationReportId,
}: {
  /** Deploy target from server config (ODP_DEPLOY_ENV); independent of the data policy. */
  deploymentEnvironment?: string | null;
  shellView?: "tasks" | "notifications";
  notificationPreferences?: boolean;
  notificationReportId?: string;
  searchParams?: Record<string, string | string[] | undefined>;
}) {
  const intakeDetailOpen = isIntakeDetailOpen(searchParams);
  const fixturesAllowed = operatorFixturesAllowed();
  const environment = resolveOperatorEnvironment(
    deploymentEnvironment ?? process.env.NEXT_PUBLIC_ODP_DEPLOY_ENV,
  );
  const [activeRoleId, setActiveRoleId] = useState<OperatorRoleId>(DEFAULT_OPERATOR_ROLE_ID);
  const [activeWorkspaceId, setActiveWorkspaceState] = useState<WorkspaceId>(() => {
    const requested = typeof searchParams.ws === "string" ? searchParams.ws : "";
    return isWorkspaceId(requested) ? requested : DEFAULT_WORKSPACE_ID;
  });
  // Async bootstrap must adopt the latest URL/history intent, never the
  // searchParams or active-workspace value captured when its read started.
  const workspaceIntent = useRef<WorkspaceId>(activeWorkspaceId);
  const setActiveWorkspaceId = useCallback((workspaceId: WorkspaceId) => {
    workspaceIntent.current = workspaceId;
    setActiveWorkspaceState(workspaceId);
  }, []);
  const [activeTabId, setActiveTabId] = useState(
    typeof searchParams.tab === "string" ? searchParams.tab : "overview",
  );
  const [activeStoreOpsDialog, setActiveStoreOpsDialog] = useState<StoreOpsWorkflowDialogType | null>(null);
  const [selectedStoreOpsIssue, setSelectedStoreOpsIssue] = useState<Issue | undefined>(undefined);
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
  const [governanceView, setGovernanceView] = useState(() => readGovernanceViewState(searchParams));
  const [isRoleMenuOpen, setIsRoleMenuOpen] = useState(false);
  const [isNotificationOpen, setIsNotificationOpen] = useState(false);
  const [isTaskCenterOpen, setIsTaskCenterOpen] = useState(false);
  const [confirmedTaskCount, setConfirmedTaskCount] = useState<number | null>(null);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [taskCenterLoadState, setTaskCenterLoadState] = useState<TaskCenterLoadState>("idle");
  const [taskCenterSource, setTaskCenterSource] = useState<TaskCenterSource>(
    fixturesAllowed ? "fixture" : "api",
  );
  const [liveTasks, setLiveTasks] = useState<OperatorTask[]>(fixturesAllowed ? taskCenterFixtures : []);
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState("");
  const [activeCommandIndex, setActiveCommandIndex] = useState(0);
  const [searchActiveIndex, setSearchActiveIndex] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  const [searchValue, setSearchValue] = useState("");
  const [hasHydratedPreferences, setHasHydratedPreferences] = useState(false);
  const [bootstrapAuthMode, setBootstrapAuthMode] = useState<"unknown" | "authenticated" | "fixture">("unknown");
  const bootstrapSequence = useRef(0);
  const bootstrapHasAuthenticatedSession = useRef(false);
  const bootstrapAdoptedRole = useRef<{ roleId: OperatorRoleId; subject: string; reloadToken: number } | null>(null);
  const bootstrapAccount = useRef<string | null>(null);
  const bootstrapPersona = useRef<{ subject: string; roleId: OperatorRoleId } | null>(null);
  const bootstrapRoleIntent = useRef<{ subject: string; roleId: OperatorRoleId } | null>(null);
  const commandInputRef = useRef<HTMLInputElement>(null);
  const topbarRef = useRef<HTMLElement>(null);

  const [shellEnvelope, setShellEnvelope] = useState<OperatorShellEnvelope>(() =>
    normalizeShellEnvelope(undefined, { allowFixtureFallback: fixturesAllowed }),
  );
  const [shellDataStatus, setShellDataStatus] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const [shellLoadFailure, setShellLoadFailure] = useState<OperatorLoadFailure | null>(null);
  const [shellWarmingUp, setShellWarmingUp] = useState(false);
  const [shellReloadToken, setShellReloadToken] = useState(0);
  const [liveNotifications, setLiveNotifications] = useState<any[]>(fixturesAllowed ? notifications : []);
  const [confirmedNotificationCount, setConfirmedNotificationCount] = useState<number | null>(null);
  const [liveIssues, setLiveIssues] = useState<Issue[]>(fixturesAllowed ? ISSUE_FIXTURES : []);
  const [liveNetworkBindings, setLiveNetworkBindings] = useState<NetworkFindAreasBindings | null>(null);
  const [liveApprovals, setLiveApprovals] = useState<any[]>([]);
  const [liveGovernanceDecisions, setLiveGovernanceDecisions] = useState<any[]>([]);
  const [liveGovernanceAuditRows, setLiveGovernanceAuditRows] = useState<any[]>([]);

  const getSecurityHeaders = (roleId: OperatorRoleId) => {
    return operatorSecurityHeaders(roleId);
  };

  const applyOperatorEnvelope = (payload: unknown, allowFixtureFallback: boolean) => {
    const nextEnvelope = normalizeShellEnvelope(payload, {
      allowFixtureFallback,
    });
    const record = getRecord(payload);
    setShellEnvelope(nextEnvelope);
    setLiveNotifications(
      nextEnvelope.notifications.length || !allowFixtureFallback
        ? nextEnvelope.notifications
        : notifications,
    );
    // `nextEnvelope.approvals` is an alias of the Today decision cards, not a
    // governance approval feed: it carries no module / requestor / submittedAt.
    // The Govern workspace binds to /api/v1/operator/governance/snapshot, so
    // only explicitly governance-shaped bootstrap side channels are forwarded,
    // and always through the governance normalizers. Each envelope replaces the
    // previous one, so a side channel the new envelope does not carry is
    // cleared: rows from a superseded envelope are stale governance state.
    setLiveApprovals(
      Array.isArray(record?.governanceApprovals)
        ? normalizeGovernanceApprovals(record.governanceApprovals)
        : [],
    );

    if (Array.isArray(record?.issues)) {
      setLiveIssues(record.issues as Issue[]);
    } else if (!allowFixtureFallback) {
      setLiveIssues([]);
    }
    setLiveGovernanceDecisions(
      Array.isArray(record?.governanceDecisions)
        ? normalizeGovernanceDecisionRows(record.governanceDecisions)
        : [],
    );
    setLiveGovernanceAuditRows(
      Array.isArray(record?.governanceAuditRows)
        ? normalizeGovernanceAuditRows(record.governanceAuditRows)
        : [],
    );
  };

  const rolesForShell = useMemo(() => {
    const roles = shellEnvelope.navigation.roles.length
      ? shellEnvelope.navigation.roles.map((role) => ({
          allowedWorkspaces: role.allowedWorkspaces,
          id: isOperatorRoleId(role.id) ? role.id : DEFAULT_OPERATOR_ROLE_ID,
          label: role.label,
          subtitle: role.subtitle,
        }))
      : OPERATOR_ROLES;
    return !fixturesAllowed && shellDataStatus === "ready"
      ? roles.map((role) => ({ ...role, allowedWorkspaces: shellEnvelope.navigation.allowedWorkspaces }))
      : roles;
  }, [fixturesAllowed, shellDataStatus, shellEnvelope.navigation]);

  const activeRole = useMemo(() => {
    const role = rolesForShell.find((role) => role.id === activeRoleId) ?? getOperatorRole(activeRoleId);
    return !fixturesAllowed && shellDataStatus === "ready"
      ? { ...role, allowedWorkspaces: shellEnvelope.navigation.allowedWorkspaces }
      : role;
  }, [activeRoleId, fixturesAllowed, rolesForShell, shellDataStatus, shellEnvelope.navigation.allowedWorkspaces]);

  const workspaceNavItems = useMemo(() => {
    return shellEnvelope.navigation.workspaces.length
      ? shellEnvelope.navigation.workspaces
      : WORKSPACES;
  }, [shellEnvelope.navigation.workspaces]);

  useEffect(() => {
    // Preferences express intent, not authority. Wait for bootstrap before
    // validating them: principal grants can differ from the static persona.
    if (!hasHydratedPreferences || (shellDataStatus !== "ready" && !(fixturesAllowed && bootstrapAuthMode === "fixture"))) return;
    const workspaceId = isWorkspaceAllowed(activeRole, activeWorkspaceId)
      ? activeWorkspaceId
      : DEFAULT_WORKSPACE_ID;
    setActiveWorkspaceId(workspaceId);
    window.sessionStorage.setItem(workspaceStorageKey, workspaceId);
  }, [activeRole, activeWorkspaceId, bootstrapAuthMode, fixturesAllowed, hasHydratedPreferences, shellDataStatus]);

  const activeWorkspace = getWorkspace(activeWorkspaceId);
  const liveWorkQueue = shellEnvelope.workQueue;
  const liveDecisions = shellEnvelope.decisions;
  const canRenderWorkspace = (shellDataStatus === "ready" || fixturesAllowed && bootstrapAuthMode === "fixture")
    && isWorkspaceAllowed(activeRole, activeWorkspaceId);
  const canReadColdIntake = intakeDetailOpen && bootstrapAuthMode === "authenticated" && !canRenderWorkspace;
  const isTodayContent = canRenderWorkspace && activeWorkspaceId === "today";
  const isGovernContent = canRenderWorkspace && activeWorkspaceId === "govern";
  const dataModeBanner = dataModeBannerCopy(shellDataStatus, fixturesAllowed);

  useLayoutEffect(() => {
    const topbar = topbarRef.current;
    if (!topbar) return;
    const root = document.documentElement;
    const property = "--operator-sticky-header-height";
    const previousValue = root.style.getPropertyValue(property);
    const updateHeight = () => {
      const height = Math.ceil(topbar.getBoundingClientRect().height);
      if (height > 0) root.style.setProperty(property, `${height}px`);
    };
    updateHeight();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateHeight);
    observer?.observe(topbar);
    window.addEventListener("resize", updateHeight);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateHeight);
      if (previousValue) root.style.setProperty(property, previousValue);
      else root.style.removeProperty(property);
    };
  }, []);

  useEffect(() => {
    // Browser preferences are presentation only. Once a server persona or an
    // explicit subject-bound selection exists, URL hydration cannot restore a
    // stale preference as an authenticated caller constraint.
    const currentPersona = bootstrapRoleIntent.current?.roleId ?? bootstrapPersona.current?.roleId;
    const storedRole = getOperatorRole(currentPersona ?? window.sessionStorage.getItem(roleStorageKey));
    setActiveRoleId(storedRole.id);

    let targetWorkspaceId = DEFAULT_WORKSPACE_ID;
    if (searchParams && typeof searchParams.ws === "string") {
      const ws = searchParams.ws as WorkspaceId;
      if (WORKSPACES.some((w) => w.id === ws)) {
        targetWorkspaceId = ws;
      }
    } else {
      const storedWorkspace = getWorkspace(window.sessionStorage.getItem(workspaceStorageKey));
      targetWorkspaceId = storedWorkspace.id;
    }

    const entity = searchParams && typeof searchParams.entity === "string" ? searchParams.entity : null;
    const tab = searchParams && typeof searchParams.tab === "string" ? searchParams.tab : "overview";

    // Do not erase a permitted deep link or stored workspace using the
    // persona's fixture grants while the verified bootstrap is still loading.
    setActiveWorkspaceId(targetWorkspaceId);
    setSelectedEntityId(entity);
    setActiveTabId(tab);
    setGovernanceView(readGovernanceViewState(searchParams));
    setHasHydratedPreferences(true);
  }, [searchParams]);

  useEffect(() => {
    function restoreLocation() {
      const params = new URLSearchParams(window.location.search);
      const requested = params.get("ws") ?? DEFAULT_WORKSPACE_ID;
      const intendedWorkspace = isWorkspaceId(requested) ? requested : DEFAULT_WORKSPACE_ID;
      const workspace = !fixturesAllowed && shellDataStatus !== "ready"
        ? intendedWorkspace
        : isWorkspaceAllowed(activeRole, intendedWorkspace) ? intendedWorkspace : DEFAULT_WORKSPACE_ID;
      setActiveWorkspaceId(workspace);
      setSelectedEntityId(params.get("entity"));
      setActiveTabId(params.get("tab") ?? "overview");
      setGovernanceView(readGovernanceViewState(params));
      window.sessionStorage.setItem(workspaceStorageKey, workspace);
    }
    window.addEventListener("popstate", restoreLocation);
    return () => window.removeEventListener("popstate", restoreLocation);
  }, [activeRole, fixturesAllowed, shellDataStatus]);

  const clearAuthenticatedRows = useCallback(() => {
    setShellEnvelope(normalizeShellEnvelope(undefined, { allowFixtureFallback: false }));
    setLiveNotifications([]); setConfirmedNotificationCount(null); setLiveIssues([]); setLiveNetworkBindings(null);
    setLiveApprovals([]); setLiveGovernanceDecisions([]); setLiveGovernanceAuditRows([]);
    setLiveTasks([]); setConfirmedTaskCount(null); setTaskCenterLoadState("idle");
  }, []);

  // A mounted read must recheck the server session even when no UI event
  // changes the local account refs. Cookies can rotate during HTTP or JSON.
  const pinAuthenticatedRead = useCallback(async (expected: string | null, current: () => boolean): Promise<boolean> => {
    if (!current()) return false;
    let actual: string | null = null;
    let response: Response | null = null;
    try {
      response = await fetch("/auth/session", { cache: "no-store", headers: { accept: "application/json" },
        signal: AbortSignal.timeout(operatorBootstrapTimeoutMs) });
      if (!current()) return false;
      if (response.status === 200) {
        bootstrapHasAuthenticatedSession.current = true;
      }
      const session: unknown = await response.json();
      if (!current()) return false;
      if (response.status === 200 && hasCanonicalScoreSession(session)) actual = session.subject;
      if (expected !== null && actual === expected) return true;
      // Legacy isolated POC may explicitly lack the session endpoint. Neither
      // 401 nor 404 establishes a subject, persona, or authenticated API grant.
      if (expected === null && fixturesAllowed && !bootstrapHasAuthenticatedSession.current
        && [401, 404].includes(response.status)) return true;
    } catch {
      if (!current()) return false;
      if (expected === null && fixturesAllowed && !bootstrapHasAuthenticatedSession.current && response?.status === 404) return true;
    }
    if (expected === null && actual === null && !bootstrapHasAuthenticatedSession.current) {
      ++bootstrapSequence.current;
      bootstrapPersona.current = null; bootstrapRoleIntent.current = null; bootstrapAdoptedRole.current = null;
      setBootstrapAuthMode("authenticated"); clearAuthenticatedRows(); setShellDataStatus("error");
      const error = new Error("Current Operator session could not be verified");
      setShellLoadFailure({ correlationId: newCorrelationId("operator-session-read"), kind: classifyLoadFailure(error, response?.status),
        httpStatus: response?.status, occurredAt: new Date().toISOString(), technicalDetail: error.message });
      return false;
    }
    // Invalidate other in-flight reads immediately, before React rerenders.
    ++bootstrapSequence.current;
    bootstrapAccount.current = actual;
    bootstrapPersona.current = null; bootstrapRoleIntent.current = null; bootstrapAdoptedRole.current = null;
    setBootstrapAuthMode("authenticated");
    clearAuthenticatedRows(); setShellDataStatus("loading"); setShellLoadFailure(null);
    setTaskCenterSource("api"); setTaskCenterLoadState("loading");
    setShellReloadToken((token) => token + 1);
    return false;
  }, [clearAuthenticatedRows, fixturesAllowed]);

  useEffect(() => {
    if (!hasHydratedPreferences) return;
    const adopted = bootstrapAdoptedRole.current;
    if (adopted?.roleId === activeRoleId && adopted.subject === bootstrapAccount.current && adopted.reloadToken === shellReloadToken) {
      bootstrapAdoptedRole.current = null;
      return;
    }

    let cancelled = false;
    const sequence = ++bootstrapSequence.current;
    const accountChanged = Symbol("superseded bootstrap account");
    const current = () => !cancelled && sequence === bootstrapSequence.current;
    async function loadShellEnvelope() {
      if (!fixturesAllowed || bootstrapAuthMode === "authenticated") {
        setShellDataStatus("loading");
        clearAuthenticatedRows();
      }
      setShellLoadFailure(null);
      setShellWarmingUp(false);
      const correlationId = newCorrelationId("operator-bootstrap");
      const failWith = (error: unknown, httpStatus?: number, responseCorrelationId?: string | null) => {
        setShellWarmingUp(false);
        setShellDataStatus("error");
        setShellLoadFailure({
          correlationId: responseCorrelationId || correlationId,
          httpStatus,
          kind: classifyLoadFailure(error, httpStatus),
          occurredAt: new Date().toISOString(),
          technicalDetail: error instanceof Error ? error.message : String(error ?? "Operator bootstrap failed"),
        });
      };
      let session200Observed = false;
      async function sessionSubject(): Promise<string | null> {
        let response: Response | null = null;
        try {
          response = await fetch("/auth/session", { cache: "no-store", headers: { accept: "application/json" },
            signal: AbortSignal.timeout(operatorBootstrapTimeoutMs) });
          if (!current()) throw accountChanged;
          if (response.status === 200) {
            session200Observed = true; bootstrapHasAuthenticatedSession.current = true;
            setBootstrapAuthMode("authenticated"); setShellDataStatus("loading"); clearAuthenticatedRows();
          }
          const session: unknown = await response.json();
          if (!current()) throw accountChanged;
          if (response.status === 200 && hasCanonicalScoreSession(session)) return session.subject;
          if (fixturesAllowed && !bootstrapHasAuthenticatedSession.current && bootstrapAccount.current === null && [401, 404].includes(response.status)) return null;
          throw Object.assign(new Error("Operator session is missing, expired or invalid"), { httpStatus: response.status === 200 ? 401 : response.status });
        } catch (error) {
          if (error === accountChanged) throw error;
          if (fixturesAllowed && !bootstrapHasAuthenticatedSession.current && bootstrapAccount.current === null && (response === null || response.status === 404)) return null;
          throw error;
        }
      }
      async function pinAccount(expected: string | null) {
        const subject = await sessionSubject();
        if (!current()) throw accountChanged;
        if (subject !== expected) {
          bootstrapAccount.current = subject;
          bootstrapPersona.current = null; bootstrapRoleIntent.current = null;
          clearAuthenticatedRows(); setShellDataStatus("loading");
          setShellReloadToken((token) => token + 1);
          throw accountChanged;
        }
      }
      let subject: string | null = null;
      let headers: Record<string, string>;

      // One attempt covers the request and, for a successful status, reading
      // the body, each under a fresh timeout budget.
      const attemptBootstrap = async (finalAttempt: boolean) => {
        const signal = AbortSignal.timeout(operatorBootstrapTimeoutMs);
        try {
          const response = await fetch("/api/v1/operator/bootstrap", { headers, signal });
          await pinAccount(subject);
          if (!current()) throw accountChanged;
          if (!finalAttempt && operatorBootstrapRetryableStatuses.has(response.status)) {
            throw new RetryableBootstrapStatus(response.status);
          }
          if (!response.ok) return { response, payload: undefined as unknown };
          const payload: unknown = await response.json();
          await pinAccount(subject);
          if (!current()) throw accountChanged;
          return { response, payload };
        } catch (error) {
          // Chromium before 154 rejects a body read cut off by this timeout
          // with AbortError instead of TimeoutError; report the signal's own
          // timeout reason so the failure is classified (and retried) as one.
          if (error instanceof Error && error.name === "AbortError" && signal.aborted) {
            throw signal.reason instanceof Error ? signal.reason : error;
          }
          throw error;
        }
      };
      try {
        subject = await sessionSubject();
        if (!current()) return;
        if (subject !== null) {
          if (bootstrapAccount.current !== subject) {
            bootstrapPersona.current = null; bootstrapRoleIntent.current = null;
          }
          bootstrapAccount.current = subject;
          setBootstrapAuthMode("authenticated"); setShellDataStatus("loading"); clearAuthenticatedRows();
        } else {
          setBootstrapAuthMode("fixture");
        }
        headers = { ...getSecurityHeaders(activeRoleId), "X-Correlation-Id": correlationId };
        const roleIntent = bootstrapRoleIntent.current;
        const persona = bootstrapPersona.current;
        if (subject !== null && !(roleIntent?.subject === subject || persona?.subject === subject)) delete headers["X-Operator-Role"];
        let attempt: Awaited<ReturnType<typeof attemptBootstrap>> | undefined;
        for (let retry = 0; attempt === undefined; retry++) {
          const delays = operatorBootstrapRetryPolicy.delaysMs;
          const finalAttempt = retry === delays.length;
          try {
            attempt = await attemptBootstrap(finalAttempt);
          } catch (error) {
            if (error === accountChanged || finalAttempt || !isRetryableBootstrapFailure(error)) throw error;
            if (cancelled) return;
            console.warn(`Retrying operator bootstrap (${retry + 1}/${delays.length}):`, error);
            setShellWarmingUp(true);
            await new Promise((resolve) => window.setTimeout(resolve, delays[retry]));
            if (cancelled) return;
          }
        }
        // A superseded load must not clear the warming notice of the load
        // that replaced it (e.g. after a role switch) while that one retries.
        if (cancelled || attempt === undefined) return;
        setShellWarmingUp(false);
        const bootstrapRes = attempt.response;
        if (!bootstrapRes.ok) {
          const responseCorrelationId = bootstrapRes.headers?.get?.("x-correlation-id") ?? null;
          const denial = await classifyAccessDenial(bootstrapRes);
          await pinAccount(subject);
          if (!current()) throw accountChanged;
          if (denial === "password_change_required") {
            // First sign-in after the identity bootstrap: the API refuses every
            // protected read until the one-time password is rotated.
            window.location.assign(PASSWORD_CHANGE_PATH);
            return;
          }
          if (fixturesAllowed && subject === null) {
            setShellDataStatus("fixture");
            return;
          }
          failWith(
            new Error(`Operator bootstrap returned ${bootstrapRes.status}${denial ? ` (${denial})` : ""}`),
            denial === "forbidden" ? 403 : bootstrapRes.status,
            responseCorrelationId,
          );
          return;
        }
        const payload = attempt.payload;
        if (cancelled) return;
        const inspection = inspectOperatorShellPayload(payload);

        if ((!fixturesAllowed || subject !== null) && inspection.status !== "ready") {
          setShellDataStatus(inspection.status);
          setShellLoadFailure(
            inspection.status === "seed"
              ? {
                  correlationId,
                  kind: "unknown",
                  occurredAt: new Date().toISOString(),
                  technicalDetail: `Blocked non-production source: ${inspection.source ?? "unknown"}`,
                }
              : null,
          );
          return;
        }

        const resolvedRoleId = getRecord(getRecord(getRecord(payload)?.meta)?.role)?.id;
        if (subject !== null && (typeof resolvedRoleId !== "string" || !isOperatorRoleId(resolvedRoleId)
          || bootstrapRoleIntent.current?.subject === subject && bootstrapRoleIntent.current.roleId !== resolvedRoleId)) {
          failWith(new Error("Operator bootstrap returned an invalid or conflicting authenticated persona"));
          return;
        }
        applyOperatorEnvelope(payload, subject === null && fixturesAllowed);
        // The server resolves the selected persona from the authenticated
        // principal; a stale/default browser preference must not impersonate
        // operations when that principal is an expansion contributor.
        if (subject !== null && typeof resolvedRoleId === "string" && isOperatorRoleId(resolvedRoleId)) {
          bootstrapPersona.current = { subject, roleId: resolvedRoleId };
          if (resolvedRoleId !== activeRoleId) bootstrapAdoptedRole.current = { subject, roleId: resolvedRoleId, reloadToken: shellReloadToken };
          setActiveRoleId(resolvedRoleId);
          const requestedWorkspace = workspaceIntent.current;
          const actualRole = getRecord(getRecord(getRecord(payload)?.meta)?.role);
          if (isWorkspaceId(requestedWorkspace) && Array.isArray(actualRole?.allowedWorkspaces)
            && actualRole.allowedWorkspaces.includes(requestedWorkspace)) setActiveWorkspaceId(requestedWorkspace);
          else setActiveWorkspaceId(DEFAULT_WORKSPACE_ID);
        }
        // Explicit isolated POC may read a real admitted API envelope without
        // password-session authority. Seed/local fallback remains fixture;
        // this never establishes a subject or authenticated persona.
        setShellDataStatus(inspection.status === "ready" && (subject !== null || fixturesAllowed) ? "ready" : "fixture");
      } catch (err) {
        if (err === accountChanged || !current()) return;
        console.error("Error loading operator shell envelope:", err);
        if (fixturesAllowed && !bootstrapHasAuthenticatedSession.current && !session200Observed && subject === null && bootstrapAccount.current === null) {
          setShellDataStatus("fixture");
        } else {
          failWith(err, typeof (err as { httpStatus?: unknown })?.httpStatus === "number"
            ? (err as { httpStatus: number }).httpStatus : undefined);
        }
      }
    }

    void loadShellEnvelope();
    return () => {
      cancelled = true;
    };
  }, [activeRoleId, fixturesAllowed, hasHydratedPreferences, shellReloadToken, clearAuthenticatedRows]);

  useEffect(() => {
    if (!isTaskCenterOpen || !canRenderWorkspace) return undefined;

    let cancelled = false;
    const sequence = bootstrapSequence.current;
    const subject = bootstrapAccount.current;
    const allowTaskFixtures = fixturesAllowed && subject === null && !bootstrapHasAuthenticatedSession.current;
    const current = () => !cancelled && sequence === bootstrapSequence.current && subject === bootstrapAccount.current;
    async function loadTasks() {
      setTaskCenterLoadState("loading");
      try {
        if (!await pinAuthenticatedRead(subject, current)) return;
        const res = await fetch("/api/v1/operator/shell/tasks", {
          headers: getSecurityHeaders(activeRoleId),
        });
        if (!await pinAuthenticatedRead(subject, current)) return;
        if (!res.ok) throw new Error(`Task center API returned ${res.status}`);

        const data = await res.json();
        if (!await pinAuthenticatedRead(subject, current)) return;
        if (!allowTaskFixtures && payloadContainsSeedData(data)) {
          throw new Error("Task center API returned seed, fixture, or mock data");
        }
        const tasks = normalizeTasksPayload(data, "api");
        if (!current()) return;

        if (tasks.length > 0) {
          setLiveTasks(tasks);
          setTaskCenterSource("api");
          setTaskCenterLoadState("ready");
        } else if (!allowTaskFixtures) {
          setLiveTasks([]);
          setTaskCenterSource("api");
          setTaskCenterLoadState("empty");
        } else {
          setLiveTasks(taskCenterFixtures);
          setTaskCenterSource("fixture");
          setTaskCenterLoadState("fallback");
        }
      } catch {
        if (!await pinAuthenticatedRead(subject, current) || !current()) return;
        if (allowTaskFixtures) {
          setLiveTasks(taskCenterFixtures);
          setTaskCenterSource("fixture");
          setTaskCenterLoadState("fallback");
        } else {
          setLiveTasks([]);
          setTaskCenterSource("api");
          setTaskCenterLoadState("error");
        }
      }
    }

    loadTasks();
    return () => {
      cancelled = true;
    };
  }, [activeRoleId, fixturesAllowed, isTaskCenterOpen, canRenderWorkspace, pinAuthenticatedRead]);

  useEffect(() => {
    if (!fixturesAllowed || activeWorkspaceId !== "network" || liveNetworkBindings !== null
      || !canRenderWorkspace || bootstrapAuthMode === "authenticated" && bootstrapPersona.current?.roleId !== activeRoleId) return;
    let cancelled = false;
    const sequence = bootstrapSequence.current;
    const subject = bootstrapAccount.current;
    const persona = bootstrapPersona.current;
    const current = () => !cancelled && sequence === bootstrapSequence.current && subject === bootstrapAccount.current
      && persona === bootstrapPersona.current;
    const superseded = () => new Error("Network binding account changed or session is invalid");
    const client = createOdpApiClient({
      baseUrl: `${window.location.origin}/api/v1`,
      defaultHeaders: getSecurityHeaders(activeRoleId),
      fetchImpl: async (input, init) => {
        if (!await pinAuthenticatedRead(subject, current)) throw superseded();
        const response = await fetch(input, init);
        if (!await pinAuthenticatedRead(subject, current)) throw superseded();
        const readJSON = response.json.bind(response);
        response.json = async () => {
          if (!await pinAuthenticatedRead(subject, current)) throw superseded();
          const body: unknown = await readJSON();
          if (!await pinAuthenticatedRead(subject, current)) throw superseded();
          return body;
        };
        return response;
      },
    });
    import("./network/networkFindAreasLoader")
      .then(({ loadNetworkFindAreasBindings }) => {
        if (!current()) throw superseded();
        return loadNetworkFindAreasBindings(client);
      })
      .then(async (bindings) => {
        if (await pinAuthenticatedRead(subject, current) && current()) setLiveNetworkBindings(bindings);
      })
      .catch(async () => { await pinAuthenticatedRead(subject, current); });
    return () => { cancelled = true; };
  }, [activeRoleId, activeWorkspaceId, fixturesAllowed, liveNetworkBindings, canRenderWorkspace, bootstrapAuthMode, pinAuthenticatedRead]);

  useEffect(() => {
    if (!toast) return undefined;

    const timeout = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  useEffect(() => {
    function handleGlobalKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
        event.preventDefault();
        openCommandPalette(searchValue);
      } else if (event.key === "Escape") {
        if (isCommandPaletteOpen) {
          closeCommandPalette();
        } else {
          setIsSearchOpen(false);
          setIsNotificationOpen(false);
          setIsTaskCenterOpen(false);
          setIsRoleMenuOpen(false);
        }
      }
    }

    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, [isCommandPaletteOpen, searchValue]);

  useEffect(() => {
    if (!isCommandPaletteOpen) return undefined;
    const timeout = window.setTimeout(() => commandInputRef.current?.focus(), 0);
    return () => window.clearTimeout(timeout);
  }, [isCommandPaletteOpen]);

  useEffect(() => {
    setSearchActiveIndex(0);
  }, [searchValue]);

  const searchMatches = useMemo(() => {
    const normalized = searchValue.trim().toLocaleLowerCase();
    if (!normalized) return shellEnvelope.search.items.slice(0, 6);
    return shellEnvelope.search.items
      .filter((item) =>
        [item.label, item.description, item.entityId, item.keywords ?? ""].join(" ").toLocaleLowerCase().includes(normalized),
      )
      .slice(0, 8);
  }, [searchValue, shellEnvelope.search.items]);

  const taskSummary = useMemo(() => {
    const activeTasks = liveTasks.filter((task) => !/(done|closed|complete|resolved)/i.test(task.status));
    const urgentTasks = activeTasks.filter((task) => task.tone === "danger" || task.priority.toUpperCase() === "P0");
    return {
      active: activeTasks.length,
      urgent: urgentTasks.length,
    };
  }, [liveTasks]);

  const workspaceCommands: CommandPaletteItem[] = WORKSPACES
    .filter((workspace) => isWorkspaceAllowed(activeRole, workspace.id))
    .map((workspace) => ({
    id: `workspace-${workspace.id}`,
    group: "pages",
    title: workspace.label,
    subtitle: `${workspace.description} workspace`,
    keywords: [workspace.id, workspace.shortLabel, workspace.description],
    execute: () => handleWorkspaceClick(workspace.id),
  }));

  const routeCommands: CommandPaletteItem[] = commandPageTargets.filter((page) => {
    const params = new URL(page.href, "https://operator.invalid").searchParams;
    if (params.get("ws") !== "network") return true;
    return fixturesAllowed || (shellEnvelope.navigation.allowedTabs?.network ?? [])
      .includes(NETWORK_TAB_IDS[parseNetworkTabIndex(params)]);
  }).map((page) => ({
    id: `route-${page.href}`,
    group: "pages",
    title: page.title,
    subtitle: page.subtitle,
    keywords: [page.href, ...page.keywords],
    execute: () => window.location.assign(page.href),
  }));

  const apiSearchCommands: CommandPaletteItem[] = shellEnvelope.search.items.slice(0, 20).map((item) => ({
    id: `api-${item.id}`,
    group: "entities",
    title: item.label,
    subtitle: `${item.entityId} · ${item.description}`,
    keywords: [item.entityId, item.description, item.keywords ?? "", item.target.workspace, item.target.tab ?? ""],
    tone: "info",
    execute: () => handleTargetSelect(item.target, item.entityId),
  }));

  const taskCommands: CommandPaletteItem[] = liveTasks.slice(0, 12).map((task) => ({
    id: `task-${task.id}`,
    group: "entities",
    title: task.title,
    subtitle: `${task.id} · ${task.owner} · ${task.status}`,
    keywords: [task.id, task.owner, task.priority, task.summary, task.workspace ?? "tasks"],
    tone: task.tone,
    execute: () => handleTaskSelect(task),
  }));

  const queueCommands: CommandPaletteItem[] = liveWorkQueue.slice(0, 8).map((item) => ({
    id: `queue-${item.id}`,
    group: "entities",
    title: item.title,
    subtitle: `${item.id} · ${item.owner} · ${item.status}`,
    keywords: [item.id, item.meta, item.description ?? "", item.workspace, item.target.tab ?? ""],
    tone: item.tone,
    execute: () => handleTargetSelect(item.target, item.id),
  }));

  const decisionCommands: CommandPaletteItem[] = liveDecisions.slice(0, 6).map((decision) => ({
    id: `decision-${decision.id}`,
    group: "entities",
    title: decision.title,
    subtitle: `${decision.id} · ${decision.status}`,
    keywords: [decision.id, decision.meta, decision.cta, decision.target.tab ?? ""],
    tone: decision.tone,
    execute: () => handleTargetSelect(decision.target, decision.id),
  }));

  const issueCommands: CommandPaletteItem[] = liveIssues.slice(0, 8).map((issue) => ({
    id: `issue-${issue.id}`,
    group: "entities",
    title: issue.title,
    subtitle: `${issue.id} · ${issue.storeName}`,
    keywords: [issue.id, issue.storeName, issue.summary],
    tone: issue.severity === "critical" ? "danger" : issue.severity === "high" ? "warning" : "info",
    execute: () => handleTargetSelect({ workspace: "store", entityId: issue.id, tab: "overview" }, issue.id),
  }));

  const actionCommands: CommandPaletteItem[] = [
    {
      id: "action-task-center",
      group: "actions",
      title: "開啟任務中心",
      subtitle: `${shellEnvelope.header.counts.taskCenter} API tasks · ${taskSummary.urgent} urgent local`,
      keywords: ["task center", "tasks", "todo", "inbox"],
      tone: shellEnvelope.header.counts.critical > 0 ? "danger" : "info",
      execute: () => openTaskCenter(),
    },
    {
      id: "action-notifications",
      group: "actions",
      title: "開啟通知",
      subtitle: `${confirmedNotificationCount ?? shellEnvelope.header.counts.notifications} notifications`,
      keywords: ["notifications", "alerts"],
      tone: liveNotifications.some((notification) => notification.tone === "danger") ? "danger" : "info",
      execute: () => {
        setIsNotificationOpen(true);
        setIsTaskCenterOpen(false);
      },
    },
    ...(fixturesAllowed
      ? [{
          id: "action-reset-demo",
          group: "actions" as const,
          title: "重設示範資料",
          subtitle: "清除角色與 workspace session state",
          keywords: ["reset", "demo", "session"],
          execute: () => handleReset(),
        }]
      : []),
    ...rolesForShell.map((role) => ({
      id: `role-${role.id}`,
      group: "actions" as CommandGroup,
      title: `切換角色：${role.label}`,
      subtitle: role.subtitle,
      keywords: [role.id, role.label, role.subtitle],
      execute: () => handleRoleSelect(role.id),
    })),
  ];

  const commandItems: CommandPaletteItem[] = [
    ...workspaceCommands,
    ...routeCommands,
    ...apiSearchCommands,
    ...taskCommands,
    ...queueCommands,
    ...decisionCommands,
    ...issueCommands,
    ...actionCommands,
  ];

  const commandQueryText = commandQuery.trim().toLowerCase();
  const filteredCommandItems = (
    commandQueryText
      ? commandItems.filter((item) => commandSearchText(item).includes(commandQueryText))
      : commandItems.filter((item) => item.group !== "entities").slice(0, 10)
  ).slice(0, 18);

  const commandGroups = (["pages", "entities", "actions"] as CommandGroup[])
    .map((group) => ({
      group,
      items: filteredCommandItems.filter((item) => item.group === group),
    }))
    .filter((group) => group.items.length > 0);

  useEffect(() => {
    setActiveCommandIndex(0);
  }, [commandQuery, isCommandPaletteOpen]);

  async function handleLogout() {
    const result = await signOutOperator();
    if (!result.ok) {
      showToast(
        `登出失敗：session 尚未撤銷（${result.status ?? "network error"}），請重試。`,
      );
    }
  }

  function showToast(message: string) {
    setToast(message);
  }

  function updateDeepLink(target: ShellTarget, historyMode: "push" | "replace" = "replace") {
    if (typeof window === "undefined") return;
    const nextUrl = new URL(window.location.href);
    nextUrl.searchParams.set("ws", target.workspace);
    nextUrl.searchParams.delete("evidence");
    nextUrl.searchParams.delete("auditCategory");
    if (target.workspace === "network" && target.tab === "review" && target.candidateId && target.reviewId) {
      nextUrl.searchParams.set("candidate", target.candidateId);
      nextUrl.searchParams.set("review", target.reviewId);
    } else {
      nextUrl.searchParams.delete("candidate");
      nextUrl.searchParams.delete("review");
    }
    if (target.entityId) {
      nextUrl.searchParams.set("entity", target.entityId);
    } else {
      nextUrl.searchParams.delete("entity");
    }
    if (target.tab) {
      nextUrl.searchParams.set("tab", target.tab);
    } else {
      nextUrl.searchParams.delete("tab");
    }
    const href = `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`;
    if (historyMode === "push") window.history.pushState(null, "", href);
    else window.history.replaceState(null, "", href);
  }

  function handleTargetSelect(target: ShellTarget, label: string) {
    const workspaceId = isWorkspaceId(target.workspace) ? target.workspace : DEFAULT_WORKSPACE_ID;
    const workspace = getWorkspace(workspaceId);

    if (!isWorkspaceAllowed(activeRole, workspaceId)) {
      showToast(`${activeRole.label} 暫無 ${workspace.label} 權限`);
      return;
    }

    if (shellView) {
      const params = new URLSearchParams({ ws: workspaceId });
      if (target.tab) params.set("tab", target.tab);
      if (target.entityId) params.set("entity", target.entityId);
      window.location.assign(`/operator?${params}`);
      return;
    }

    setActiveWorkspaceId(workspaceId);
    setSelectedEntityId(target.entityId ?? null);
    setActiveTabId(target.tab ?? "overview");
    window.sessionStorage.setItem(workspaceStorageKey, workspaceId);
    setGovernanceView(readGovernanceViewState({ tab:target.tab, entity:target.entityId }));
    updateDeepLink({ ...target, workspace: workspaceId }, "push");
    setIsSearchOpen(false);
    setIsCommandPaletteOpen(false);
    setSearchValue("");
    setCommandQuery("");

    if (workspaceId === "store") {
      const issue = liveIssues.find((item) => item.id === target.entityId);
      setSelectedStoreOpsIssue(issue);
    } else {
      setActiveStoreOpsDialog(null);
    }

    showToast(`${label} opened: ${workspace.label}${target.tab ? ` / ${target.tab}` : ""}`);
  }

  function handleWorkspaceClick(workspaceId: WorkspaceId) {
    handleTargetSelect({ workspace: workspaceId, tab: "overview" }, getWorkspace(workspaceId).label);
  }

  function handleGovernanceViewChange(next: GovernanceViewState) {
    if (!isWorkspaceAllowed(activeRole, "govern")) return;
    const url = new URL(window.location.href);
    url.search = serializeGovernanceViewState(next, url.searchParams).toString();
    const href = `${url.pathname}${url.search}${url.hash}`;
    if (href !== `${window.location.pathname}${window.location.search}${window.location.hash}`) window.history.pushState(null, "", href);
    setGovernanceView(next); setSelectedEntityId(next.approvalId); setActiveTabId(next.tab);
  }

  function handleRoleSelect(roleId: OperatorRoleId) {
    const nextRole = rolesForShell.find((role) => role.id === roleId);
    if (!nextRole) return;
    if (!fixturesAllowed && nextRole.id !== activeRoleId) setShellDataStatus("loading");
    if (bootstrapAccount.current) bootstrapRoleIntent.current = { subject: bootstrapAccount.current, roleId: nextRole.id };
    setActiveRoleId(nextRole.id);
    window.sessionStorage.setItem(roleStorageKey, nextRole.id);
    setIsRoleMenuOpen(false);

    if (!isWorkspaceAllowed(nextRole, activeWorkspaceId)) {
      setActiveWorkspaceId(DEFAULT_WORKSPACE_ID);
      window.sessionStorage.setItem(workspaceStorageKey, DEFAULT_WORKSPACE_ID);
      updateDeepLink({ workspace: DEFAULT_WORKSPACE_ID, tab: "overview" });
    } else {
      updateDeepLink({ workspace:activeWorkspaceId, tab:"overview" });
    }
    setSelectedEntityId(null);
    setActiveTabId("overview");
    setGovernanceView(readGovernanceViewState({}));

    showToast(`已切換為 ${nextRole.label}`);
  }

  function handleReset() {
    window.sessionStorage.removeItem(roleStorageKey);
    window.sessionStorage.removeItem(workspaceStorageKey);
    bootstrapPersona.current = null; bootstrapRoleIntent.current = null; bootstrapAdoptedRole.current = null;
    setShellReloadToken((token) => token + 1);
    setActiveRoleId(DEFAULT_OPERATOR_ROLE_ID);
    setActiveWorkspaceId(DEFAULT_WORKSPACE_ID);
    setSelectedEntityId(null);
    setActiveTabId("overview");
    setSearchValue("");
    setCommandQuery("");
    setGovernanceView(readGovernanceViewState({}));
    updateDeepLink({ workspace: DEFAULT_WORKSPACE_ID, tab: "overview" });
    showToast(fixturesAllowed && bootstrapAuthMode === "fixture" ? "已重置為營運主管 Today" : "已重置視角；正在重新確認可用角色。");
  }

  function openStoreOpsWorkflow(dialog: StoreOpsWorkflowDialogType, issue: Issue) {
    setSelectedStoreOpsIssue(issue);
    setActiveStoreOpsDialog(dialog);
  }

  function openCommandPalette(query = "") {
    setCommandQuery(query);
    setSearchValue(query);
    setActiveCommandIndex(0);
    setIsCommandPaletteOpen(true);
    setIsSearchOpen(false);
    setIsNotificationOpen(false);
    setIsRoleMenuOpen(false);
    setIsTaskCenterOpen(false);
  }

  function closeCommandPalette() {
    setIsCommandPaletteOpen(false);
    setCommandQuery("");
    setSearchValue("");
    setActiveCommandIndex(0);
  }

  function openTaskCenter() {
    setIsTaskCenterOpen(true);
    setIsNotificationOpen(false);
    setIsRoleMenuOpen(false);
  }

  function executeCommand(item: CommandPaletteItem) {
    closeCommandPalette();
    item.execute();
  }

  function handleTaskSelect(task: OperatorTask) {
    if (task.source === "fixture" && fixturesAllowed && !bootstrapHasAuthenticatedSession.current) {
      setIsTaskCenterOpen(false);
      if (task.workspace) {
        handleTargetSelect({ workspace: task.workspace, entityId: task.id, tab: "overview" }, task.id);
        return;
      }

      try {
        const href = new URL(task.href, window.location.origin);
        const ws = href.searchParams.get("ws");
        if (href.pathname === "/operator" && ws && isWorkspaceId(ws)) {
          handleTargetSelect(
            {
              workspace: ws,
              entityId: href.searchParams.get("entity") ?? task.id,
              tab: href.searchParams.get("tab") ?? "overview",
            },
            task.id,
          );
          return;
        }
      } catch {
        // Fall through to normal navigation for non-URL task hrefs.
      }

      window.location.assign(task.href);
      return;
    }
    setIsTaskCenterOpen(false);
    window.location.assign(`/tasks/${encodeURIComponent(task.id)}`);
  }

  function handleCommandKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveCommandIndex((index) => Math.min(index + 1, Math.max(filteredCommandItems.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveCommandIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const item = filteredCommandItems[activeCommandIndex];
      if (item) executeCommand(item);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeCommandPalette();
    }
  }

  function handleSearchKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSearchActiveIndex((index) => Math.min(index + 1, Math.max(searchMatches.length - 1, 0)));
      setIsSearchOpen(true);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSearchActiveIndex((index) => Math.max(index - 1, 0));
      setIsSearchOpen(true);
    } else if (event.key === "Escape") {
      setIsSearchOpen(false);
    } else if (event.key === "Enter" && searchMatches[searchActiveIndex]) {
      event.preventDefault();
      const selected = searchMatches[searchActiveIndex];
      handleTargetSelect(selected.target, selected.entityId);
    }
  }

  async function refreshOperatorEnvelope() {
    const sequence = bootstrapSequence.current;
    const subject = bootstrapAccount.current;
    const persona = bootstrapPersona.current;
    const current = () => sequence === bootstrapSequence.current && subject === bootstrapAccount.current
      && persona === bootstrapPersona.current;
    try {
      if (!await pinAuthenticatedRead(subject, current)) return;
      const freshRes = await fetch("/api/v1/operator/bootstrap", {
        headers: getSecurityHeaders(activeRoleId),
      });
      if (!await pinAuthenticatedRead(subject, current)) return;
      if (!freshRes.ok) {
        throw new Error(`Operator bootstrap refresh returned ${freshRes.status}`);
      }
      const payload: unknown = await freshRes.json();
      if (!await pinAuthenticatedRead(subject, current) || !current()) return;
      const inspection = inspectOperatorShellPayload(payload);
      if ((!fixturesAllowed || subject !== null) && inspection.status !== "ready") {
        clearAuthenticatedRows(); setShellDataStatus(inspection.status);
        throw new Error(`Operator bootstrap refresh was ${inspection.status}`);
      }
      const rawRole = (payload as { meta?: { role?: { id?: unknown } } } | null)?.meta?.role?.id;
      if (typeof rawRole !== "string" || !isOperatorRoleId(rawRole) || subject !== null && (rawRole !== persona?.roleId || rawRole !== activeRoleId)) {
        clearAuthenticatedRows(); setShellDataStatus("error");
        throw new Error("Operator bootstrap refresh returned an invalid current persona");
      }
      applyOperatorEnvelope(payload, subject === null && fixturesAllowed);
      setShellDataStatus(inspection.status === "ready" ? "ready" : "fixture");
    } catch (error) {
      // Rejected HTTP/JSON reads must not act on a superseded account either.
      if (await pinAuthenticatedRead(subject, current) && current()) throw error;
    }
  }


  return (
    <div
      className={[
        styles.console,
        intakeDetailOpen ? "operatorIntakeDetailOpen" : "",
      ].filter(Boolean).join(" ")}
      data-intake-detail-open={intakeDetailOpen ? "true" : undefined}
      data-testid="operator-console"
    >
      <header ref={topbarRef} className={styles.topbar} data-screen-label="Top Navigation">
        <div className={styles.topbarInner} data-testid="operator-topbar-inner">
          <div className={styles.brandCluster} aria-label="Oday Plus Operator Console">
            <span className={styles.brandMark}>O+</span>
            <span className={styles.brandText}>
              <strong>Oday Plus</strong>
              <small>Operator Console</small>
            </span>
            <span
              aria-label={`部署環境：${environment.label}`}
              className={[styles.envBadge, styles[`envBadge_${environment.id}`]].join(" ")}
              data-environment={environment.id}
              data-testid="operator-environment-badge"
            >
              {environment.label}
            </span>
          </div>

          <nav className={styles.workspaceNav} aria-label="Operator workspaces">
            {workspaceNavItems.filter((workspace) => isWorkspaceAllowed(activeRole, workspace.id)).map((workspace) => {
              const workspaceId = isWorkspaceId(workspace.id) ? workspace.id : DEFAULT_WORKSPACE_ID;
              const isActive = activeWorkspaceId === workspaceId;
              const isLocked = !isWorkspaceAllowed(activeRole, workspaceId);

              return (
                <button
                  aria-current={isActive ? "page" : undefined}
                  aria-disabled={isLocked}
                  className={[
                    styles.workspaceNavItem,
                    isActive ? styles.workspaceNavItem_active : "",
                    isLocked ? styles.workspaceNavItem_locked : "",
                  ].join(" ")}
                  key={workspace.id}
                  onClick={() => handleWorkspaceClick(workspaceId)}
                  title={isLocked ? `${activeRole.label} locked` : workspace.description}
                  type="button"
                >
                  <span>{workspace.label}</span>
                  <small>{workspace.description}</small>
                </button>
              );
            })}
          </nav>

          <div className={styles.topActions}>
            <div className={styles.popoverAnchor}>
              <div className={styles.searchBox}>
                <input
                  aria-label="Global search"
                  onBlur={() => window.setTimeout(() => setIsSearchOpen(false), 120)}
                  onChange={(event) => {
                    setSearchValue(event.target.value);
                    setIsSearchOpen(true);
                  }}
                  onFocus={() => setIsSearchOpen(true)}
                  onKeyDown={handleSearchKeyDown}
                  placeholder="搜尋門市、案件"
                  value={searchValue}
                />
                <button
                  aria-label="Open command palette"
                  className={styles.searchShortcut}
                  data-testid="operator-command-trigger"
                  onClick={() => openCommandPalette(searchValue)}
                  title="Ctrl+K / ⌘K"
                  type="button"
                >
                  ⌘K
                </button>
              </div>
              {isSearchOpen && !isCommandPaletteOpen && searchValue.trim() ? (
                <div className={styles.searchPanel} data-testid="operator-search-results" role="listbox">
                  <div className={styles.popoverTitle}>Search</div>
                  {searchMatches.length ? (
                    searchMatches.map((item, index) => (
                      <button
                        aria-selected={index === searchActiveIndex}
                        className={styles.searchResult}
                        data-target-entity={item.target.entityId}
                        data-target-tab={item.target.tab}
                        data-target-workspace={item.target.workspace}
                        key={item.id}
                        onMouseDown={(event) => {
                          event.preventDefault();
                          handleTargetSelect(item.target, item.entityId);
                        }}
                        role="option"
                        type="button"
                      >
                        <span>{item.entityId}</span>
                        <strong>{item.label}</strong>
                        <small>{item.description}</small>
                      </button>
                    ))
                  ) : (
                    <div className={styles.searchEmpty}>No matching operator work</div>
                  )}
                </div>
              ) : null}
            </div>

            <div className={styles.popoverAnchor}>
              <button
                aria-expanded={shellView !== "notifications" && isNotificationOpen}
                className={styles.headerAction}
                data-testid="operator-notification-button"
                onClick={() => {
                  if (shellView === "notifications") {
                    setIsNotificationOpen(false);
                    if (window.location.pathname === "/notifications") document.getElementById("canonical-notifications-page")?.focus();
                    else window.location.assign("/notifications");
                    return;
                  }
                  setIsNotificationOpen((open) => !open);
                  setIsTaskCenterOpen(false);
                  setIsRoleMenuOpen(false);
                }}
                type="button"
              >
                通知
                <span
                  className={[
                    styles.headerCount,
                    (confirmedNotificationCount ?? shellEnvelope.header.counts.notifications) > 0 ? styles.headerCount_alert : "",
                  ].join(" ")}
                >
                  {confirmedNotificationCount ?? shellEnvelope.header.counts.notifications}
                </span>
              </button>
              {shellView !== "notifications" && isNotificationOpen && canRenderWorkspace ? (
                <ShellNotificationCenter
                  scopeKey={JSON.stringify([bootstrapSequence.current, bootstrapAccount.current, bootstrapPersona.current?.roleId, activeRoleId])}
                  headers={getSecurityHeaders(activeRoleId)}
                  createAuthenticatedReadGuard={(readCurrent) => {
                    const sequence = bootstrapSequence.current;
                    const subject = bootstrapAccount.current;
                    const persona = bootstrapPersona.current;
                    const current = () => readCurrent() && sequence === bootstrapSequence.current
                      && subject === bootstrapAccount.current && persona === bootstrapPersona.current;
                    return () => pinAuthenticatedRead(subject, current);
                  }}
                  allowFixtureFallback={fixturesAllowed && bootstrapAuthMode === "fixture" && bootstrapAccount.current === null}
                  fixtureRows={liveNotifications}
                  onConfirmedUnreadCount={setConfirmedNotificationCount}
                  onReportSelect={(reportId) => window.location.assign(`/notifications/reports/${encodeURIComponent(reportId)}`)}
                  onTaskSelect={(taskId, requestKey) => window.location.assign(`/tasks/${encodeURIComponent(taskId)}?${new URLSearchParams({ view: "approvals", requestKey })}`)}
                  onTargetSelect={handleTargetSelect}
                />
              ) : null}
            </div>

            <div className={styles.popoverAnchor}>
              <button
                aria-expanded={isTaskCenterOpen}
                className={styles.headerAction}
                data-testid="operator-task-center-button"
                onClick={() => (isTaskCenterOpen ? setIsTaskCenterOpen(false) : openTaskCenter())}
                title={taskSummary.urgent > 0 ? `${taskSummary.urgent} 項緊急` : undefined}
                type="button"
              >
                <span data-testid="operator-task-center-count">
                  任務中心
                  <strong
                    className={[
                      styles.headerCount,
                      taskSummary.urgent > 0 && shellEnvelope.header.counts.taskCenter > 0 ? styles.headerCount_alert : "",
                    ].join(" ")}
                  >
                    {confirmedTaskCount ?? shellEnvelope.header.counts.taskCenter}
                  </strong>
                </span>
              </button>
              {isTaskCenterOpen ? (
                <div className={styles.taskCenterPanel} data-testid="operator-task-center">
                  <div className={styles.taskCenterHeader}>
                    <div>
                      <div className={styles.popoverTitle}>Task center</div>
                      <strong>我的待辦與核准</strong>
                    </div>
                    <Chip tone={taskCenterSource === "api" ? "success" : "neutral"}>
                      {taskCenterLoadState === "loading" ? "SYNCING" : taskCenterSource.toUpperCase()}
                    </Chip>
                  </div>
                  <div className={styles.taskCenterStats} aria-label="Task center summary">
                    <span>
                      <strong>{shellEnvelope.header.counts.taskCenter}</strong>
                      API
                    </span>
                    <span>
                      <strong>{taskSummary.urgent}</strong>
                      Urgent
                    </span>
                    <span>
                      <strong>{liveTasks.length}</strong>
                      Visible
                    </span>
                  </div>
                  <div className={styles.taskList}>
                    {taskCenterLoadState === "empty" || taskCenterLoadState === "error" ? (
                      <div className={styles.commandEmpty} data-testid="operator-task-center-unavailable">
                        {taskCenterLoadState === "empty"
                          ? "Task API 已回應，但目前沒有可用任務。"
                          : "Task API 暫時無法取得，未顯示 fixture 任務。"}
                      </div>
                    ) : liveTasks.slice(0, 8).map((task) => (
                      <button
                        className={styles.taskRow}
                        key={task.id}
                        onClick={() => handleTaskSelect(task)}
                        type="button"
                      >
                        <span className={[styles.taskMarker, styles[`marker_${task.tone}`]].join(" ")} aria-hidden="true" />
                        <span className={styles.taskMain}>
                          <span>
                            <strong>{task.title}</strong>
                            <StatusBadge tone={task.tone}>{task.status}</StatusBadge>
                          </span>
                          <small>{task.id} · {task.summary}</small>
                        </span>
                        <span className={styles.taskMeta}>
                          <b>{task.owner}</b>
                          <em>{task.priority}</em>
                          <time>{task.dueLabel}</time>
                        </span>
                      </button>
                    ))}
                  </div>
                  <div className={styles.taskCenterFooter}>
                    <button type="button" onClick={() => window.location.assign("/tasks")}>
                      Open full tasks page
                    </button>
                    <span>
                      {taskCenterLoadState === "fallback"
                        ? "/api/v1/operator/shell/tasks local fixture active"
                        : "/api/v1/operator/shell/tasks"}
                    </span>
                  </div>
                </div>
              ) : null}
            </div>

            <button
              className={styles.headerAction}
              data-testid="operator-approval-count"
              onClick={() => handleWorkspaceClick("govern")}
              type="button"
            >
              待核准
              <strong
                className={[
                  styles.headerCount,
                  shellEnvelope.header.counts.approvals > 0 ? styles.headerCount_pending : "",
                ].join(" ")}
              >
                {shellEnvelope.header.counts.approvals}
              </strong>
            </button>

            <div className={styles.popoverAnchor}>
              <button
                aria-expanded={isRoleMenuOpen}
                className={styles.roleButton}
                title={activeRole.label}
                onClick={() => {
                  setIsRoleMenuOpen((open) => !open);
                  setIsNotificationOpen(false);
                  setIsTaskCenterOpen(false);
                }}
                type="button"
              >
                <span aria-hidden="true" className={styles.roleAvatar}>{Array.from(activeRole.label)[0]}</span>
                <span className={styles.roleLabel}>{activeRole.label}</span>
                <span aria-hidden="true" className={styles.roleCaret}>▾</span>
              </button>
              {isRoleMenuOpen ? (
                <div className={styles.roleMenu} data-screen-label="Role Switch Menu">
                  <div className={styles.popoverTitle}>使用者與角色</div>
                  {rolesForShell.map((role) => (
                    <button
                      className={role.id === activeRole.id ? styles.roleOption_active : styles.roleOption}
                      key={role.id}
                      onClick={() => handleRoleSelect(role.id)}
                      type="button"
                    >
                      <span className={styles.roleOptionText}>
                        <strong>{role.label}</strong>
                        <small>{role.subtitle}</small>
                      </span>
                      <span className={styles.roleAccessList} aria-label={`${role.label} workspace access`}>
                        {WORKSPACES.map((workspace) => {
                          const allowed = role.allowedWorkspaces.includes(workspace.id);
                          return (
                            <span
                              className={allowed ? styles.roleAccess_allowed : styles.roleAccess_locked}
                              key={workspace.id}
                            >
                              {allowed ? workspace.shortLabel : `Lock ${workspace.shortLabel}`}
                            </span>
                          );
                        })}
                      </span>
                    </button>
                  ))}
                  <div className={styles.userMenuFooter}>
                    <a className={styles.userMenuItem} data-testid="operator-admin-link" href={ADMIN_PATH}>
                      管理後台
                    </a>
                    <button className={styles.userMenuItem} onClick={handleLogout} type="button">
                      登出
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </header>

      <div className={styles.pocBanner} data-data-status={shellDataStatus}>
        <div>
          <span aria-hidden="true" className={styles.pocBannerDot} />
          <strong>{dataModeBanner.label}</strong>
          <span>{dataModeBanner.detail}</span>
        </div>
        <button className={styles.pocBannerAction} onClick={handleReset} type="button">
          重設視角
        </button>
      </div>

      <main
        className={[
          styles.shell,
          isTodayContent ? styles.shell_today : "",
          isGovernContent ? styles.shell_govern : "",
        ].filter(Boolean).join(" ")}
      >
        {canReadColdIntake ? (
          <ColdIntakeDetail intakeId={typeof searchParams.selected === "string" ? searchParams.selected : ""}
            onClose={() => window.location.assign("/operator?ws=network&tab=radar")} />
        ) : !canRenderWorkspace ? (
          <OperatorDataUnavailableGate
            failure={shellLoadFailure}
            loadingDetail={shellWarmingUp ? operatorBootstrapWarmingUpDetail : undefined}
            onRetry={() => setShellReloadToken((token) => token + 1)}
            status={toUnavailableOperatorStatus(shellDataStatus)}
          />
        ) : shellView === "notifications" ? (
          <ShellNotificationPage preferencesPage={notificationPreferences} reportId={notificationReportId}
            scopeKey={JSON.stringify([bootstrapSequence.current, bootstrapAccount.current, bootstrapPersona.current?.roleId, activeRoleId])}
            expectedSubjectId={bootstrapAccount.current}
            headers={getSecurityHeaders(activeRoleId)}
            createAuthenticatedReadGuard={(readCurrent) => {
              const sequence = bootstrapSequence.current;
              const subject = bootstrapAccount.current;
              const persona = bootstrapPersona.current;
              const current = () => readCurrent() && sequence === bootstrapSequence.current
                && subject === bootstrapAccount.current && persona === bootstrapPersona.current;
              return () => pinAuthenticatedRead(subject, current);
            }}
            allowFixtureFallback={fixturesAllowed && bootstrapAuthMode === "fixture" && bootstrapAccount.current === null}
            fixtureRows={liveNotifications}
            onConfirmedUnreadCount={setConfirmedNotificationCount}
            onReportSelect={(reportId) => window.location.assign(`/notifications/reports/${encodeURIComponent(reportId)}`)}
            onTaskSelect={(taskId, requestKey) => window.location.assign(`/tasks/${encodeURIComponent(taskId)}?${new URLSearchParams({ view: "approvals", requestKey })}`)}
            onTargetSelect={handleTargetSelect}
          />
        ) : shellView === "tasks" ? (
          <ShellTaskCenter
            scopeKey={JSON.stringify([bootstrapSequence.current, bootstrapAccount.current, bootstrapPersona.current, activeRoleId])}
            headers={operatorSecurityHeaders(activeRoleId)}
            createAuthenticatedReadGuard={(readCurrent) => {
              const sequence = bootstrapSequence.current;
              const subject = bootstrapAccount.current;
              const persona = bootstrapPersona.current;
              const current = () => readCurrent() && sequence === bootstrapSequence.current
                && subject === bootstrapAccount.current && persona === bootstrapPersona.current;
              return () => pinAuthenticatedRead(subject, current);
            }}
            onConfirmedActiveCount={setConfirmedTaskCount}
            allowFixtureData={fixturesAllowed && bootstrapAuthMode === "fixture"}
          />
        ) : activeWorkspaceId === "today" && activeTabId === "interventions" ? (
          <InterventionWorkspace selectedId={selectedEntityId ?? (typeof searchParams.selected === "string" ? searchParams.selected : undefined)}
            onSelect={(entityId) => handleTargetSelect({ workspace: "today", tab: "interventions", entityId }, "干預工作台")}
            onDecisionConfirmed={async () => {
              try { await refreshOperatorEnvelope(); }
              catch { showToast("核決已保存；Today 資料尚未重新核對，請重新整理。"); }
            }} />
        ) : activeWorkspaceId === "today" ? (
          <ApiTodayWorkspace
            envelope={shellEnvelope}
            onTargetSelect={handleTargetSelect}
          />
        ) : activeWorkspaceId === "store" ? (
          <DesignStoreOpsWorkspace
            activeRoleId={activeRoleId}
            forecastScopeKey={JSON.stringify([bootstrapSequence.current, bootstrapAccount.current, bootstrapPersona.current, activeRoleId])}
            createAuthenticatedReadGuard={(readCurrent) => {
              const sequence = bootstrapSequence.current;
              const subject = bootstrapAccount.current;
              const persona = bootstrapPersona.current;
              const current = () => readCurrent() && sequence === bootstrapSequence.current
                && subject === bootstrapAccount.current && persona === bootstrapPersona.current;
              return () => pinAuthenticatedRead(subject, current);
            }}
            initialIssueId={selectedEntityId ?? undefined}
            initialTabId={activeTabId}
            issues={liveIssues}
            onOpenWorkflow={openStoreOpsWorkflow}
          />
        ) : activeWorkspaceId === "network" ? (
          <NetworkFindAreasWorkspace
            activeRoleId={activeRoleId}
            createAuthenticatedReadGuard={(readCurrent) => {
              // Capture the owner before HTTP starts; never adopt a later cookie
              // subject, persona, or A→B→A epoch for an older Network response.
              const sequence = bootstrapSequence.current;
              const subject = bootstrapAccount.current;
              const persona = bootstrapPersona.current;
              const current = () => readCurrent() && sequence === bootstrapSequence.current
                && subject === bootstrapAccount.current && persona === bootstrapPersona.current;
              return () => pinAuthenticatedRead(subject, current);
            }}
            allowedTabIds={fixturesAllowed ? undefined : shellEnvelope.navigation.allowedTabs?.network ?? []}
            onReviewDecisionConfirmed={async () => {
              try { await refreshOperatorEnvelope(); }
              catch { showToast("核決已保存；Today 資料尚未重新核對，請重新整理。"); }
            }}
            initialTabId={activeTabId}
            liveCandidates={liveNetworkBindings?.candidates}
            liveHeatZones={liveNetworkBindings?.heatZones}
            callbacks={{
              onChangeLens: (lens) => showToast(`Network lens: ${lens}`),
              onScoreCandidate: (candidate, heatZone) => showToast(`${candidate.id} scoring opened for ${heatZone.id}`),
              onSelectHeatZone: (heatZone) => showToast(`${heatZone.id} selected`),
              onSourceListings: (heatZone) => showToast(`${heatZone.id} source listings callback recorded`),
              onSubmitReview: (heatZone) => showToast(`${heatZone.id} review submitted to POC shell`),
              onToggleTracked: (heatZone, tracked) => showToast(`${heatZone.id} ${tracked ? "tracked" : "untracked"}`),
            }}
          />
        ) : activeWorkspaceId === "govern" ? (
          // GovernanceWorkspace renders its own Package 10 page header.
          <GovernanceWorkspace
            canonicalMonitoringScopeKey={JSON.stringify([bootstrapSequence.current, bootstrapAccount.current, bootstrapPersona.current, activeRoleId])}
            createCanonicalMonitoringReadGuard={(readCurrent) => {
              const sequence = bootstrapSequence.current, subject = bootstrapAccount.current, persona = bootstrapPersona.current;
              const current = () => readCurrent() && sequence === bootstrapSequence.current
                && subject === bootstrapAccount.current && persona === bootstrapPersona.current;
              return () => pinAuthenticatedRead(subject, current);
            }}
            onDecisionConfirmed={async () => {
              try { await refreshOperatorEnvelope(); }
              catch { showToast("核決已保存；Today 資料尚未重新核對，請重新整理。"); }
            }}
            viewState={governanceView}
            onViewStateChange={handleGovernanceViewChange}
            approvals={liveApprovals.length ? liveApprovals : undefined}
            auditRows={liveGovernanceAuditRows.length ? liveGovernanceAuditRows : undefined}
            callbacks={{
              onSelectApproval: (approval) => showToast(`${approval.id} selected`),
              onOpenNativeTarget: (target, approval) => handleTargetSelect(target, approval.title),
            }}
            decisions={liveGovernanceDecisions.length ? liveGovernanceDecisions : undefined}
            canComment={activeRoleId !== "pm-audit"}
            role={activeRole.label}
            roleId={activeRoleId}
          />
        ) : activeWorkspaceId === "growth" ? (
          <GrowthWorkspace searchParams={searchParams} basePath="/operator" />
        ) : (
          <WorkspaceChrome activeRoleLabel={activeRole.label} workspace={activeWorkspace}>
            <WorkspacePlaceholder workspaceId={activeWorkspaceId} />
          </WorkspaceChrome>
        )}
      </main>

      {activeStoreOpsDialog ? (
      <StoreOpsWorkflowDialogs
        activeDialog={activeStoreOpsDialog}
        issue={selectedStoreOpsIssue}
        onClose={() => setActiveStoreOpsDialog(null)}
      />
      ) : null}

      {isCommandPaletteOpen ? (
        <div
          className={styles.commandOverlay}
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) closeCommandPalette();
          }}
        >
          <section
            aria-label="Command palette"
            aria-modal="true"
            className={styles.commandDialog}
            data-testid="operator-command-palette"
            onKeyDown={handleCommandKeyDown}
            role="dialog"
          >
            <div className={styles.commandSearchRow}>
              <span className={styles.commandKey}>⌘K</span>
              <input
                aria-activedescendant={filteredCommandItems[activeCommandIndex] ? `command-${toDomSafeId(filteredCommandItems[activeCommandIndex].id)}` : undefined}
                aria-controls="operator-command-results"
                aria-expanded={isCommandPaletteOpen}
                aria-label="Command palette search Global search"
                autoComplete="off"
                onChange={(event) => {
                  setCommandQuery(event.target.value);
                  setSearchValue(event.target.value);
                }}
                placeholder="搜尋頁面、任務、門市、候選點或快速動作"
                ref={commandInputRef}
                role="combobox"
                value={commandQuery}
              />
              <Button onClick={closeCommandPalette} size="sm" variant="ghost">
                Esc
              </Button>
            </div>
            <div className={styles.commandResults} id="operator-command-results" role="listbox">
              {commandGroups.length > 0 ? (
                commandGroups.map((group) => (
                  <div className={styles.commandGroup} key={group.group}>
                    <div className={styles.commandGroupTitle}>{commandGroupLabels[group.group]}</div>
                    {group.items.map((item) => {
                      const flatIndex = filteredCommandItems.findIndex((candidate) => candidate.id === item.id);
                      const isActive = flatIndex === activeCommandIndex;
                      return (
                        <button
                          aria-selected={isActive}
                          className={[styles.commandItem, isActive ? styles.commandItem_active : ""].join(" ")}
                          id={`command-${toDomSafeId(item.id)}`}
                          key={item.id}
                          onClick={() => executeCommand(item)}
                          onMouseEnter={() => setActiveCommandIndex(flatIndex)}
                          role="option"
                          type="button"
                        >
                          <span className={styles.commandItemMain}>
                            <strong>{item.title}</strong>
                            <small>{item.subtitle}</small>
                          </span>
                          <span className={styles.commandItemMeta}>
                            {item.tone ? <StatusBadge tone={item.tone}>{commandGroupLabels[item.group]}</StatusBadge> : null}
                            <kbd>Enter</kbd>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                ))
              ) : (
                <div className={styles.commandEmpty}>No matching command</div>
              )}
            </div>
          </section>
        </div>
      ) : null}

      {toast ? <div className={styles.toast}>{toast}</div> : null}
    </div>
  );
}

export function isIntakeDetailOpen(
  searchParams: Record<string, string | string[] | undefined>,
): boolean {
  const value = (key: string) => {
    const current = searchParams[key];
    return Array.isArray(current) ? current[0] : current;
  };
  const dialog = value("dialog");
  return value("ws") === "network"
    && value("tab") === "radar"
    && Boolean(value("selected"))
    && (dialog === "detail" || dialog === "fix" || dialog === "decide" || dialog === "assignmentSla");
}

function WorkspaceChrome({
  activeRoleLabel,
  children,
  workspace,
}: {
  activeRoleLabel: string;
  children: ReactNode;
  workspace: ReturnType<typeof getWorkspace>;
}) {
  return (
    <>
      <div className={styles.workspaceHeader}>
        <div>
          <p>{workspace.description}</p>
          <h1>{workspace.label}</h1>
        </div>
        <div className={styles.workspaceMeta}>
          <Chip tone="info">{activeRoleLabel}</Chip>
          <Chip tone={operatorFixturesAllowed() ? "neutral" : "success"}>
            {operatorFixturesAllowed() ? "Local fixture mode" : "Live API"}
          </Chip>
          <Chip tone="neutral">Taipei / UTC+8 view</Chip>
        </div>
      </div>
      {children}
    </>
  );
}

function WorkspacePlaceholder({ workspaceId }: { workspaceId: WorkspaceId }) {
  const workspace = getWorkspace(workspaceId);

  return (
    <SectionPanel eyebrow="Fleet handoff shell" title={`${workspace.label} workspace shell`}>
      <div className={styles.placeholderLayout}>
        <div>
          <p>
            {workspace.description} 的 route、navigation guard 與 dense shell 已可渲染。後續 ODP-OC fleet 可在此掛上 typed
            fixtures、state machines 與工作流。
          </p>
          <TabBar
            items={[
              { id: "overview", label: "Overview", active: true },
              { id: "workbench", label: "Workbench", disabled: true },
              { id: "audit", label: "Audit", disabled: true },
            ]}
          />
        </div>
        <div className={styles.placeholderMatrix}>
          <StatusBadge tone="success">Shell ready</StatusBadge>
          <StatusBadge tone="info">Awaiting data worker</StatusBadge>
          <StatusBadge tone="neutral">No API import</StatusBadge>
        </div>
      </div>
    </SectionPanel>
  );
}
