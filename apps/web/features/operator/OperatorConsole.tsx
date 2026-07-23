"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { createOdpApiClient } from "@oday-plus/openapi-client";
import {
  Button,
  Chip,
  SectionPanel,
  StatusBadge,
  TabBar,
  type Tone,
} from "./components";
import { DesignStoreOpsWorkspace } from "./DesignAlignedWorkspaces";
import { GovernanceWorkspace } from "./GovernanceWorkspace";
import { GrowthWorkspace } from "./GrowthWorkspace";
import { NetworkFindAreasWorkspace } from "./NetworkFindAreasWorkspace";
import { StoreOpsWorkflowDialogs } from "./StoreOpsWorkflowDialogs";
import { ISSUE_FIXTURES } from "./fixtures";
import {
  DEFAULT_OPERATOR_ROLE_ID,
  DEFAULT_WORKSPACE_ID,
  OPERATOR_ROLES,
  WORKSPACES,
  getOperatorRole,
  getWorkspace,
  isWorkspaceAllowed,
  mergeOperatorRoles,
  planOperatorRoleSwitch,
  type OperatorRoleId,
  type WorkspaceId,
} from "./navigation";
import {
  loadNetworkFindAreasBindings,
  type NetworkFindAreasBindings,
} from "./networkFindAreasLoader";
import styles from "./operator.module.css";
import { operatorSecurityHeaders } from "./operatorSecurityHeaders";
import type { StoreOpsWorkflowDialogType } from "./storeOpsWorkflowTypes";
import {
  TodayWorkspace as ApiTodayWorkspace,
  normalizeShellEnvelope,
  type OperatorShellEnvelope,
  type ShellTarget,
} from "./TodayWorkspace";
import type { Issue } from "./types";

const roleStorageKey = "oday.operator.role";
const workspaceStorageKey = "oday.operator.workspace";

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

type TaskCenterLoadState = "idle" | "loading" | "ready" | "fallback";
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
  { title: "OpsBoard 總覽", subtitle: "跨模組狀態與最近決策", href: "/", keywords: ["home", "overview"] },
  { title: "任務中心", subtitle: "個人與團隊待辦", href: "/tasks", keywords: ["tasks", "todo"] },
  { title: "全域搜尋", subtitle: "門市、候選點、決策、模型版本", href: "/search", keywords: ["search"] },
  { title: "營運監控", subtitle: "四燈、預測帶與根因證據", href: "/operations", keywords: ["operations"] },
  { title: "展店選址", subtitle: "HeatZone、Listing、SiteScore", href: "/expansion", keywords: ["expansion"] },
  { title: "干預決策", subtitle: "干預建議與觀察窗", href: "/interventions", keywords: ["interventions"] },
  { title: "定價", subtitle: "調價方案與保護線", href: "/pricing", keywords: ["pricing"] },
  { title: "廣告增益", subtitle: "treatment/control 與 iROMI", href: "/adlift", keywords: ["adlift"] },
  { title: "門市估值", subtitle: "AVM 公允價值與資料室", href: "/avm", keywords: ["avm"] },
  { title: "網路規劃", subtitle: "NetPlan 情境與 solver", href: "/netplan", keywords: ["netplan"] },
  { title: "模型與學習", subtitle: "模型版本、release、rollback", href: "/learning", keywords: ["learning"] },
  { title: "稽核軌跡", subtitle: "決策時間軸與證據包", href: "/audit", keywords: ["audit"] },
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
  const href = getNestedText(record, ["href", "url"]) || (workspace ? `/operator?ws=${workspace}` : "/tasks");
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

export function OperatorConsole({ searchParams = {} }: { searchParams?: Record<string, string | string[] | undefined> }) {
  const [activeRoleId, setActiveRoleId] = useState<OperatorRoleId>(DEFAULT_OPERATOR_ROLE_ID);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<WorkspaceId>(DEFAULT_WORKSPACE_ID);
  const [activeTabId, setActiveTabId] = useState("overview");
  const [activeStoreOpsDialog, setActiveStoreOpsDialog] = useState<StoreOpsWorkflowDialogType | null>(null);
  const [selectedStoreOpsIssue, setSelectedStoreOpsIssue] = useState<Issue | undefined>(undefined);
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
  const [isRoleMenuOpen, setIsRoleMenuOpen] = useState(false);
  const [isNotificationOpen, setIsNotificationOpen] = useState(false);
  const [isTaskCenterOpen, setIsTaskCenterOpen] = useState(false);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [taskCenterLoadState, setTaskCenterLoadState] = useState<TaskCenterLoadState>("idle");
  const [taskCenterSource, setTaskCenterSource] = useState<TaskCenterSource>("fixture");
  const [liveTasks, setLiveTasks] = useState<OperatorTask[]>(taskCenterFixtures);
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState("");
  const [activeCommandIndex, setActiveCommandIndex] = useState(0);
  const [searchActiveIndex, setSearchActiveIndex] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  const [searchValue, setSearchValue] = useState("");
  const [hasHydratedPreferences, setHasHydratedPreferences] = useState(false);
  const commandInputRef = useRef<HTMLInputElement>(null);

  const [shellEnvelope, setShellEnvelope] = useState<OperatorShellEnvelope>(() => normalizeShellEnvelope());
  const [liveNotifications, setLiveNotifications] = useState<any[]>(notifications);
  const [liveIssues, setLiveIssues] = useState<Issue[]>(ISSUE_FIXTURES);
  const [liveNetworkBindings, setLiveNetworkBindings] = useState<NetworkFindAreasBindings | null>(null);
  const [liveApprovals, setLiveApprovals] = useState<any[]>([]);
  const [liveGovernanceDecisions, setLiveGovernanceDecisions] = useState<any[]>([]);
  const [liveGovernanceAuditRows, setLiveGovernanceAuditRows] = useState<any[]>([]);

  const getSecurityHeaders = (roleId: OperatorRoleId) =>
    operatorSecurityHeaders(roleId);

  const applyOperatorEnvelope = (payload: unknown) => {
    const nextEnvelope = normalizeShellEnvelope(payload);
    const record = getRecord(payload);
    setShellEnvelope(nextEnvelope);
    setLiveNotifications(nextEnvelope.notifications.length ? nextEnvelope.notifications : notifications);
    setLiveApprovals(nextEnvelope.approvals);

    if (Array.isArray(record?.issues)) {
      setLiveIssues(record.issues as Issue[]);
    }
    if (Array.isArray(record?.governanceDecisions)) {
      setLiveGovernanceDecisions(record.governanceDecisions);
    }
    if (Array.isArray(record?.governanceAuditRows)) {
      setLiveGovernanceAuditRows(record.governanceAuditRows);
    }
  };

  const rolesForShell = useMemo(() => {
    const remoteRoles = shellEnvelope.navigation.roles.flatMap((role) =>
      isOperatorRoleId(role.id)
        ? [{
            allowedWorkspaces: role.allowedWorkspaces,
            id: role.id,
            label: role.label,
            subtitle: role.subtitle,
          }]
        : [],
    );
    return mergeOperatorRoles(remoteRoles);
  }, [shellEnvelope.navigation.roles]);

  const activeRole = useMemo(() => {
    return rolesForShell.find((role) => role.id === activeRoleId) ?? getOperatorRole(activeRoleId);
  }, [activeRoleId, rolesForShell]);

  const workspaceNavItems = useMemo(() => {
    return shellEnvelope.navigation.workspaces.length ? shellEnvelope.navigation.workspaces : WORKSPACES;
  }, [shellEnvelope.navigation.workspaces]);

  const activeWorkspace = getWorkspace(activeWorkspaceId);
  const isNetworkWorkspace = activeWorkspaceId === "network";
  const liveWorkQueue = shellEnvelope.workQueue;
  const liveDecisions = shellEnvelope.decisions;

  useEffect(() => {
    const storedRole = getOperatorRole(window.sessionStorage.getItem(roleStorageKey));
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

    if (isWorkspaceAllowed(storedRole, targetWorkspaceId)) {
      setActiveWorkspaceId(targetWorkspaceId);
      window.sessionStorage.setItem(workspaceStorageKey, targetWorkspaceId);
    } else {
      setActiveWorkspaceId(DEFAULT_WORKSPACE_ID);
      window.sessionStorage.setItem(workspaceStorageKey, DEFAULT_WORKSPACE_ID);
    }
    setSelectedEntityId(entity);
    setActiveTabId(tab);
    setHasHydratedPreferences(true);
  }, [searchParams]);

  useEffect(() => {
    if (!hasHydratedPreferences) return;

    async function loadShellEnvelope() {
      try {
        const headers = getSecurityHeaders(activeRoleId);
        const bootstrapRes = await fetch("/api/v1/operator/bootstrap", { headers });
        if (bootstrapRes.ok) {
          applyOperatorEnvelope(await bootstrapRes.json());
        }
      } catch (err) {
        console.error("Error loading operator shell envelope:", err);
      }
    }

    loadShellEnvelope();
  }, [activeRoleId, hasHydratedPreferences]);

  useEffect(() => {
    if (!isTaskCenterOpen) return undefined;

    let cancelled = false;
    async function loadTasks() {
      setTaskCenterLoadState("loading");
      try {
        const res = await fetch("/api/v1/tasks", {
          headers: getSecurityHeaders(activeRoleId),
        });
        if (!res.ok) throw new Error(`Task center API returned ${res.status}`);

        const data = await res.json();
        const tasks = normalizeTasksPayload(data, "api");
        if (cancelled) return;

        if (tasks.length > 0) {
          setLiveTasks(tasks);
          setTaskCenterSource("api");
          setTaskCenterLoadState("ready");
        } else {
          setLiveTasks(taskCenterFixtures);
          setTaskCenterSource("fixture");
          setTaskCenterLoadState("fallback");
        }
      } catch {
        if (cancelled) return;
        setLiveTasks(taskCenterFixtures);
        setTaskCenterSource("fixture");
        setTaskCenterLoadState("fallback");
      }
    }

    loadTasks();
    return () => {
      cancelled = true;
    };
  }, [activeRoleId, isTaskCenterOpen]);

  useEffect(() => {
    if (activeWorkspaceId !== "network" || liveNetworkBindings !== null) return;
    const client = createOdpApiClient({
      env: {
        ODP_API_BASE_URL: process.env.NEXT_PUBLIC_ODP_API_BASE_URL,
        NEXT_PUBLIC_ODP_API_BASE_URL: process.env.NEXT_PUBLIC_ODP_API_BASE_URL,
      },
    });
    loadNetworkFindAreasBindings(client)
      .then((bindings) => setLiveNetworkBindings(bindings))
      .catch(() => undefined);
  }, [activeWorkspaceId, liveNetworkBindings]);

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

  const workspaceCommands: CommandPaletteItem[] = WORKSPACES.map((workspace) => ({
    id: `workspace-${workspace.id}`,
    group: "pages",
    title: workspace.label,
    subtitle: `${workspace.description} workspace`,
    keywords: [workspace.id, workspace.shortLabel, workspace.description],
    execute: () => handleWorkspaceClick(workspace.id),
  }));

  const routeCommands: CommandPaletteItem[] = commandPageTargets.map((page) => ({
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
      subtitle: `${shellEnvelope.header.counts.notifications} notifications`,
      keywords: ["notifications", "alerts"],
      tone: liveNotifications.some((notification) => notification.tone === "danger") ? "danger" : "info",
      execute: () => {
        setIsNotificationOpen(true);
        setIsTaskCenterOpen(false);
      },
    },
    {
      id: "action-reset-demo",
      group: "actions",
      title: "重設示範資料",
      subtitle: "清除角色與 workspace session state",
      keywords: ["reset", "demo", "session"],
      execute: () => handleReset(),
    },
    ...OPERATOR_ROLES.map((role) => ({
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

  function showToast(message: string) {
    setToast(message);
  }

  function updateDeepLink(target: ShellTarget) {
    if (typeof window === "undefined") return;
    const nextUrl = new URL(window.location.href);
    nextUrl.searchParams.set("ws", target.workspace);
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
    window.history.replaceState({}, "", `${nextUrl.pathname}${nextUrl.search}`);
  }

  function handleTargetSelect(target: ShellTarget, label: string) {
    const workspaceId = isWorkspaceId(target.workspace) ? target.workspace : DEFAULT_WORKSPACE_ID;
    const workspace = getWorkspace(workspaceId);

    if (!isWorkspaceAllowed(activeRole, workspaceId)) {
      showToast(`${activeRole.label} 暫無 ${workspace.label} 權限`);
      return;
    }

    setActiveWorkspaceId(workspaceId);
    setSelectedEntityId(target.entityId ?? null);
    setActiveTabId(target.tab ?? "overview");
    window.sessionStorage.setItem(workspaceStorageKey, workspaceId);
    updateDeepLink({ ...target, workspace: workspaceId });
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

  function handleRoleSelect(roleId: OperatorRoleId) {
    const nextRole = getOperatorRole(roleId);
    const roleSwitchPlan = planOperatorRoleSwitch(roleId, activeWorkspaceId);
    setActiveRoleId(nextRole.id);
    window.sessionStorage.setItem(roleStorageKey, nextRole.id);
    setIsRoleMenuOpen(false);

    if (!roleSwitchPlan.preserveDeepLink) {
      setActiveWorkspaceId(roleSwitchPlan.workspaceId);
      window.sessionStorage.setItem(workspaceStorageKey, roleSwitchPlan.workspaceId);
      updateDeepLink({ workspace: DEFAULT_WORKSPACE_ID, tab: "overview" });
      setSelectedEntityId(null);
      setActiveTabId("overview");
    }

    showToast(`已切換為 ${nextRole.label}`);
  }

  function handleReset() {
    window.sessionStorage.removeItem(roleStorageKey);
    window.sessionStorage.removeItem(workspaceStorageKey);
    setActiveRoleId(DEFAULT_OPERATOR_ROLE_ID);
    setActiveWorkspaceId(DEFAULT_WORKSPACE_ID);
    setSelectedEntityId(null);
    setActiveTabId("overview");
    setSearchValue("");
    setCommandQuery("");
    updateDeepLink({ workspace: DEFAULT_WORKSPACE_ID, tab: "overview" });
    showToast("已重置為營運主管 Today");
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
    const freshRes = await fetch("/api/v1/operator/bootstrap", {
      headers: getSecurityHeaders(activeRoleId),
    });
    if (freshRes.ok) {
      applyOperatorEnvelope(await freshRes.json());
    }
  }

  async function handleApprovalDecision(approvalId: string, status: string, payload: any) {
    const correlationId = "corr-" + Math.random().toString(36).substring(2, 11);
    const idempotencyKey = "idem-" + Math.random().toString(36).substring(2, 11);
    try {
      const body = {
        actorName: shellEnvelope.today.hero.name,
        actorRoleId: activeRoleId,
        reason: payload?.reason ?? `Decision from Operator Console for ${approvalId}`,
        ...payload,
        status,
      };
      const res = await fetch(`/api/v1/operator/approvals/${approvalId}/decision`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
          "X-Correlation-Id": correlationId,
          ...getSecurityHeaders(activeRoleId),
        },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        showToast("決策已送出");
        await refreshOperatorEnvelope();
      }
    } catch (err) {
      console.error("Error submitting approval decision:", err);
    }
  }

  return (
    <div
      className={[styles.console, isNetworkWorkspace ? styles.consoleNetworkParity : ""].join(" ")}
      data-testid="operator-console"
    >
      <header className={styles.topbar} data-screen-label="Top Navigation">
        <div className={styles.brandCluster} aria-label="Oday Plus Operator Console">
          <span className={styles.brandMark}>O+</span>
          <span className={styles.brandText}>
            <strong>Oday Plus</strong>
            <small>Operator Console</small>
          </span>
          <Chip tone="accent">POC DEMO</Chip>
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
            <label className={styles.searchBox}>
              <span aria-hidden="true">/</span>
              <input
                aria-label="Global search"
                onBlur={() => window.setTimeout(() => setIsSearchOpen(false), 120)}
                onChange={(event) => {
                  setSearchValue(event.target.value);
                  setIsSearchOpen(true);
                }}
                onFocus={() => setIsSearchOpen(true)}
                onKeyDown={handleSearchKeyDown}
                placeholder="搜尋門市、案件、物件..."
                value={searchValue}
              />
            </label>
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

          <Button
            aria-label="Open command palette"
            data-testid="operator-command-trigger"
            onClick={() => openCommandPalette(searchValue)}
            size="sm"
            variant="ghost"
          >
            ⌘K
          </Button>

          <div className={styles.popoverAnchor}>
            <Button
              aria-expanded={isNotificationOpen}
              aria-label="Open notifications"
              onClick={() => {
                setIsNotificationOpen((open) => !open);
                setIsTaskCenterOpen(false);
                setIsRoleMenuOpen(false);
              }}
              size="sm"
              variant="ghost"
            >
              ! {shellEnvelope.header.counts.notifications}
            </Button>
            {isNotificationOpen ? (
              <div className={styles.notificationPanel} data-screen-label="Notifications">
                <div className={styles.popoverTitle}>Notifications</div>
                {liveNotifications.map((notification) => (
                  <article className={styles.notificationItem} key={notification.id ?? notification.title}>
                    <StatusBadge tone={notification.tone}>{notification.title}</StatusBadge>
                    <p>{notification.detail}</p>
                  </article>
                ))}
              </div>
            ) : null}
          </div>

          <div className={styles.popoverAnchor}>
            <button
              aria-expanded={isTaskCenterOpen}
              aria-label="Open task center"
              className={styles.taskCenterButton}
              data-testid="operator-task-center-button"
              onClick={() => (isTaskCenterOpen ? setIsTaskCenterOpen(false) : openTaskCenter())}
              type="button"
            >
              <span data-testid="operator-task-center-count">
                Task Center <strong>{shellEnvelope.header.counts.taskCenter}</strong>
              </span>
              {taskSummary.urgent > 0 ? <span>{taskSummary.urgent} urgent</span> : null}
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
                  {liveTasks.slice(0, 8).map((task) => (
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
                  <span>{taskCenterLoadState === "fallback" ? "/api/v1/tasks fallback active" : "/api/v1/tasks live"}</span>
                </div>
              </div>
            ) : null}
          </div>

          <button
            className={styles.approvalChip}
            data-testid="operator-approval-count"
            onClick={() => handleWorkspaceClick("govern")}
            type="button"
          >
            待核准 <strong>{shellEnvelope.header.counts.approvals}</strong>
          </button>

          <div className={styles.popoverAnchor}>
            <Button
              aria-expanded={isRoleMenuOpen}
              onClick={() => {
                setIsRoleMenuOpen((open) => !open);
                setIsNotificationOpen(false);
                setIsTaskCenterOpen(false);
              }}
              size="sm"
              variant="secondary"
            >
              {activeRole.label}
            </Button>
            {isRoleMenuOpen ? (
              <div className={styles.roleMenu} data-screen-label="Role Switch Menu">
                <div className={styles.popoverTitle}>Role switcher</div>
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
                      {role.intakeModeLabel ? (
                        <small data-testid={`intake-role-mode-${role.id}`}>
                          {role.intakeModeLabel}
                        </small>
                      ) : null}
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
              </div>
            ) : null}
          </div>

          <Button onClick={() => showToast("POC console 尚未串接登入服務")} size="sm" variant="ghost">
            Logout
          </Button>
        </div>
      </header>

      <div className={styles.pocBanner}>
        <div>
          <strong>API-backed</strong>
          <span>
            Shell、Today、通知與核准數字由 Operator API envelope 驅動；寫入後即時刷新本 session。
          </span>
        </div>
        <Button onClick={handleReset} size="sm" variant="secondary">
          重設視角
        </Button>
      </div>

      <main className={styles.shell}>
        {activeWorkspaceId === "today" ? (
          <ApiTodayWorkspace
            envelope={shellEnvelope}
            onApprovalDecision={(approvalId, status, payload) => handleApprovalDecision(approvalId, status, payload)}
            onTargetSelect={handleTargetSelect}
          />
        ) : activeWorkspaceId === "store" ? (
          <DesignStoreOpsWorkspace
            initialIssueId={selectedEntityId ?? undefined}
            initialTabId={activeTabId}
            issues={liveIssues}
            onOpenWorkflow={openStoreOpsWorkflow}
          />
        ) : activeWorkspaceId === "network" ? (
          <NetworkFindAreasWorkspace
            activeRoleId={activeRoleId}
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
          <WorkspaceChrome activeRoleLabel={activeRole.label} workspace={activeWorkspace}>
            <GovernanceWorkspace
              approvals={liveApprovals.length ? liveApprovals : undefined}
              auditRows={liveGovernanceAuditRows.length ? liveGovernanceAuditRows : undefined}
              callbacks={{
                onApprove: (payload) => handleApprovalDecision(payload.approvalId, "approved", payload),
                onReject: (payload) => handleApprovalDecision(payload.approvalId, "rejected", payload),
                onReturn: (payload) => handleApprovalDecision(payload.approvalId, "returned", payload),
                onSelectApproval: (approval) => showToast(`${approval.id} selected`),
              }}
              decisions={liveGovernanceDecisions.length ? liveGovernanceDecisions : undefined}
              role={activeRole.label}
            />
          </WorkspaceChrome>
        ) : activeWorkspaceId === "growth" ? (
          <GrowthWorkspace searchParams={searchParams} basePath="/operator" />
        ) : (
          <WorkspaceChrome activeRoleLabel={activeRole.label} workspace={activeWorkspace}>
            <WorkspacePlaceholder workspaceId={activeWorkspaceId} />
          </WorkspaceChrome>
        )}
      </main>

      <StoreOpsWorkflowDialogs
        activeDialog={activeStoreOpsDialog}
        callbacks={{
          onSubmit: async (event) => {
            showToast(`${event.payload.issueId} ${event.type} submitted to POC shell`);
            const correlationId = "corr-" + Math.random().toString(36).substring(2, 11);
            const idempotencyKey = "idem-" + Math.random().toString(36).substring(2, 11);
            let endpoint = "";
            if (event.type === "triage") endpoint = "triage";
            else if (event.type === "assign") endpoint = "assign";
            else if (event.type === "action") endpoint = "actions";
            else if (event.type === "fieldReport") endpoint = "field-report";
            else if (event.type === "outcome") endpoint = "outcome";
            else if (event.type === "escalate") endpoint = "escalate";
            else if (event.type === "cameraPurpose") endpoint = "purpose";

            let path = "";
            if (endpoint === "purpose") {
              path = `/api/v1/operator/evidence/EVD-101/purpose`;
            } else if (endpoint) {
              path = `/api/v1/operator/issues/${event.payload.issueId}/${endpoint}`;
            }

            if (path) {
              try {
                const res = await fetch(path, {
                  method: "POST",
                  headers: {
                    "Content-Type": "application/json",
                    "Idempotency-Key": idempotencyKey,
                    "X-Correlation-Id": correlationId,
                    ...getSecurityHeaders(activeRoleId),
                  },
                  body: JSON.stringify({
                    actorName: shellEnvelope.today.hero.name,
                    actorRoleId: activeRoleId,
                    ...event.payload,
                  }),
                });
                if (res.ok) {
                  await refreshOperatorEnvelope();
                }
              } catch (err) {
                console.error("Error submitting workflow write:", err);
              }
            }
          },
        }}
        issue={selectedStoreOpsIssue}
        onClose={() => setActiveStoreOpsDialog(null)}
      />

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
          <Chip tone="success">Fixture healthy</Chip>
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
