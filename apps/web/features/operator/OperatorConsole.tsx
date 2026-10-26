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
} from "react";
import dynamic from "next/dynamic";
import type { Tone } from "./components";
import type { GovernanceWorkspaceProps } from "./GovernanceWorkspace";
import { readGovernanceViewState, serializeGovernanceViewState, type GovernanceViewState } from "./governance/governanceUrlState";
import type { NetworkFindAreasWorkspaceProps } from "./NetworkFindAreasWorkspace";
import type { ColdIntakeDetailProps } from "./network/intake/ColdIntakeDetail";
import type { InterventionWorkspaceProps } from "./interventions/InterventionWorkspace";
import { ISSUE_FIXTURES } from "./issueFixtures";
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
import { HEAT_ZONE_SELECTION_PARAM } from "./network/heatZoneNavigation";
import type { NetworkFindAreasBindings } from "./network/networkFindAreasLoader";
import { OperatorDataUnavailableGate } from "./OperatorDataUnavailableGate";
import {
  classifyLoadFailure,
  operatorFixturesAllowed,
  toUnavailableOperatorStatus,
  type OperatorDataAvailability,
  type OperatorLoadFailure,
} from "./operatorDataMode";
import { resolveOperatorEnvironment } from "./operatorEnvironment";
import styles from "./operator.module.css";
import type { StoreOpsWorkflowDialogType } from "./storeOpsWorkflowTypes";
import {
  normalizeShellEnvelope,
  type OperatorShellEnvelope,
  type ShellTarget,
} from "./shellEnvelope";
import type { Issue } from "./types";
import { hasCanonicalScoreSession } from "./operatorSessionValidation";
import { operatorSecurityHeaders } from "./operatorSecurityHeaders";
import {
  PASSWORD_CHANGE_PATH,
  classifyAccessDenial,
  signOutOperator,
} from "./operatorSession";


// Presentation loads at the original open/admitted branch; header and workspace state stay here.
const OperatorShellCommandPalette = dynamic<ComponentProps<typeof import("./OperatorShellCommandPalette").OperatorShellCommandPalette>>(
  () => import("./OperatorShellCommandPalette").then((mod) => mod.OperatorShellCommandPalette),
  { ssr: false },
);
const OperatorTaskPopover = dynamic<ComponentProps<typeof import("./OperatorShellHeaderPanels").OperatorTaskPopover>>(
  () => import("./OperatorShellHeaderPanels").then((mod) => mod.OperatorTaskPopover),
  { ssr: false },
);
const OperatorRoleMenu = dynamic<ComponentProps<typeof import("./OperatorShellHeaderPanels").OperatorRoleMenu>>(
  () => import("./OperatorShellHeaderPanels").then((mod) => mod.OperatorRoleMenu),
  { ssr: false },
);
const OperatorWorkspaceFallback = dynamic<ComponentProps<typeof import("./OperatorWorkspaceFallback").OperatorWorkspaceFallback>>(
  () => import("./OperatorWorkspaceFallback").then((mod) => mod.OperatorWorkspaceFallback),
  { ssr: false },
);

// Today is a separate admitted owner, like the other original workspaces.
// Task/Notification entry pages need only the pure shell envelope normalizer.
const ApiTodayWorkspace = dynamic<ComponentProps<typeof import("./TodayWorkspace").TodayWorkspace>>(
  () => import("./TodayWorkspace").then((mod) => mod.TodayWorkspace),
  { ssr: false, loading: () => <div aria-live="polite" role="status">今日工作載入中…</div> },
);

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

export type OperatorTask = {
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

function isOperatorRoleId(value: string): value is OperatorRoleId {
  return OPERATOR_ROLES.some((role) => role.id === value);
}

function isWorkspaceId(value: string): value is WorkspaceId {
  return WORKSPACES.some((workspace) => workspace.id === value);
}



function getRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
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

const EMPTY_OPERATOR_SEARCH_PARAMS: Record<string, string | string[] | undefined> = Object.freeze({});

export function OperatorConsole({
  deploymentEnvironment,
  searchParams = EMPTY_OPERATOR_SEARCH_PARAMS,
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

  const applyOperatorEnvelope = (payload: unknown, allowFixtureFallback: boolean,
    admission: typeof import("./operatorBootstrapPayload")) => {
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
        ? admission.normalizeGovernanceApprovals(record.governanceApprovals)
        : [],
    );

    if (Array.isArray(record?.issues)) {
      setLiveIssues(record.issues as Issue[]);
    } else if (!allowFixtureFallback) {
      setLiveIssues([]);
    }
    setLiveGovernanceDecisions(
      Array.isArray(record?.governanceDecisions)
        ? admission.normalizeGovernanceDecisionRows(record.governanceDecisions)
        : [],
    );
    setLiveGovernanceAuditRows(
      Array.isArray(record?.governanceAuditRows)
        ? admission.normalizeGovernanceAuditRows(record.governanceAuditRows)
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

  // A layout effect validates the workspace in the same commit that changed
  // the grants. As a passive effect it could run after the browser had already
  // delivered a click, and its snapshot workspace would overwrite that newer,
  // permitted selection while the URL kept it.
  useLayoutEffect(() => {
    // Preferences express intent, not authority. Wait for bootstrap before
    // validating them: principal grants can differ from the static persona.
    if (!hasHydratedPreferences || (shellDataStatus !== "ready" && !(fixturesAllowed && bootstrapAuthMode === "fixture"))) return;
    // A pending effect can run after a click has recorded newer intent but
    // before that state commits. Let the newer render validate that choice.
    if (workspaceIntent.current !== activeWorkspaceId) return;
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
        const admission = await import("./operatorBootstrapPayload");
        await pinAccount(subject);
        if (!current()) throw accountChanged;
        const inspection = admission.inspectOperatorShellPayload(payload);

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
        applyOperatorEnvelope(payload, subject === null && fixturesAllowed, admission);
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
        const { normalizeTasksPayload, payloadContainsSeedData } = await import("./operatorTaskPayload");
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
    Promise.all([
      import("@oday-plus/openapi-client"),
      import("./network/networkFindAreasLoader"),
    ])
      .then(([{ createOdpApiClient }, { loadNetworkFindAreasBindings }]) => {
        if (!current()) throw superseded();
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
    // ShellTarget carries no HeatZone identity: the exact entity+heatZone pair
    // is written only by its existing producers. A target change must not keep
    // a stale heatZone after `entity` is replaced or removed.
    nextUrl.searchParams.delete(HEAT_ZONE_SELECTION_PARAM);
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
      const admission = await import("./operatorBootstrapPayload");
      if (!await pinAuthenticatedRead(subject, current) || !current()) return;
      const inspection = admission.inspectOperatorShellPayload(payload);
      if ((!fixturesAllowed || subject !== null) && inspection.status !== "ready") {
        clearAuthenticatedRows(); setShellDataStatus(inspection.status);
        throw new Error(`Operator bootstrap refresh was ${inspection.status}`);
      }
      const rawRole = (payload as { meta?: { role?: { id?: unknown } } } | null)?.meta?.role?.id;
      if (typeof rawRole !== "string" || !isOperatorRoleId(rawRole) || subject !== null && (rawRole !== persona?.roleId || rawRole !== activeRoleId)) {
        clearAuthenticatedRows(); setShellDataStatus("error");
        throw new Error("Operator bootstrap refresh returned an invalid current persona");
      }
      applyOperatorEnvelope(payload, subject === null && fixturesAllowed, admission);
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
            {workspaceNavItems.map((workspace) => {
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
                <OperatorTaskPopover shellEnvelope={shellEnvelope} taskCenterSource={taskCenterSource}
                  taskCenterLoadState={taskCenterLoadState} taskSummary={taskSummary}
                  liveTasks={liveTasks} handleTaskSelect={handleTaskSelect} />
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
                <OperatorRoleMenu rolesForShell={rolesForShell} activeRole={activeRole}
                  handleRoleSelect={handleRoleSelect} handleLogout={handleLogout} />
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
          <OperatorWorkspaceFallback activeRoleLabel={activeRole.label} workspace={activeWorkspace} workspaceId={activeWorkspaceId} />
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
        <OperatorShellCommandPalette
          activeRole={activeRole} rolesForShell={rolesForShell} shellEnvelope={shellEnvelope}
          fixturesAllowed={fixturesAllowed} liveTasks={liveTasks} liveIssues={liveIssues}
          liveWorkQueue={liveWorkQueue} liveDecisions={liveDecisions} liveNotifications={liveNotifications}
          taskSummary={taskSummary} confirmedNotificationCount={confirmedNotificationCount}
          commandQuery={commandQuery} isCommandPaletteOpen={isCommandPaletteOpen} activeCommandIndex={activeCommandIndex}
          setActiveCommandIndex={setActiveCommandIndex} setCommandQuery={setCommandQuery}
          setSearchValue={setSearchValue} setIsNotificationOpen={setIsNotificationOpen}
          setIsTaskCenterOpen={setIsTaskCenterOpen} commandInputRef={commandInputRef}
          closeCommandPalette={closeCommandPalette} handleWorkspaceClick={handleWorkspaceClick}
          handleTargetSelect={handleTargetSelect} handleTaskSelect={handleTaskSelect}
          openTaskCenter={openTaskCenter} handleReset={handleReset} handleRoleSelect={handleRoleSelect}
        />
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
