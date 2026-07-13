"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import {
  AuditRow,
  Button,
  Chip,
  DecisionCard,
  EvidenceCard,
  MetricCard,
  QueueRow,
  RiskRow,
  SectionPanel,
  StatusBadge,
  TabBar,
  type Tone,
} from "./components";
import { DesignStoreOpsWorkspace, DesignTodayWorkspace } from "./DesignAlignedWorkspaces";
import { GovernanceWorkspace } from "./GovernanceWorkspace";
import { NetworkFindAreasWorkspace } from "./NetworkFindAreasWorkspace";
import { GrowthWorkspace } from "./GrowthWorkspace";
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
import styles from "./operator.module.css";
import { StoreOpsWorkflowDialogs } from "./StoreOpsWorkflowDialogs";
import type { StoreOpsWorkflowDialogType } from "./storeOpsWorkflowTypes";
import type { Issue } from "./types";
import { ISSUE_FIXTURES } from "./fixtures";

const roleStorageKey = "oday.operator.role";
const workspaceStorageKey = "oday.operator.workspace";

const kpis: Array<{ label: string; value: string; delta: string; meta: string; tone: Tone }> = [
  {
    label: "Critical SLA",
    value: "9",
    delta: "+3 since 09:00",
    meta: "4 due in 2h",
    tone: "danger",
  },
  {
    label: "待核准",
    value: "5",
    delta: "2 SiteScore",
    meta: "1 returned",
    tone: "warning",
  },
  {
    label: "高風險門市",
    value: "7",
    delta: "3 payment",
    meta: "2 hygiene",
    tone: "accent",
  },
  {
    label: "今日待處理",
    value: "18",
    delta: "-6 vs yesterday",
    meta: "72% owned",
    tone: "info",
  },
  {
    label: "AI 建議",
    value: "12",
    delta: "8 high confidence",
    meta: "v2.6",
    tone: "success",
  },
  {
    label: "觀察中",
    value: "6",
    delta: "3 outcome-ready",
    meta: "M3/M6 watch",
    tone: "neutral",
  },
];

const workQueue: Array<{
  description: string;
  id: string;
  meta: string;
  owner: string;
  status: string;
  time: string;
  title: string;
  tone: Tone;
  workspace: WorkspaceId;
}> = [
  {
    id: "ISS-1024",
    title: "支付失敗率異常升高",
    description: "大安復興店 12 分鐘內連續 18 筆失敗，收銀機 A3 需 triage。",
    meta: "Payment + Google review + ForecastOps 四燈號",
    owner: "營運",
    status: "SLA 1h",
    time: "09:42",
    tone: "danger",
    workspace: "store",
  },
  {
    id: "ISS-1021",
    title: "Kiosk offline 影響午尖峰",
    description: "板橋中山店設備離線 24 分鐘，工務主任可直接指派現場處理。",
    meta: "IoT device state + CS cases",
    owner: "工務",
    status: "New",
    time: "09:20",
    tone: "warning",
    workspace: "store",
  },
  {
    id: "GRW-201",
    title: "夜間會員回流活動建議",
    description: "忠孝商圈夜間需求未滿足，建議 20:00-23:00 定向券。",
    meta: "Segment fit 84 / conflict clear",
    owner: "行銷",
    status: "Draft",
    time: "08:55",
    tone: "success",
    workspace: "growth",
  },
  {
    id: "APR-501",
    title: "CS-1002 SiteScore WAIT",
    description: "候選點信心 76，需要營運主管判定是否進入複審。",
    meta: "Model SiteScore v2.3 / snapshot FS-20260703-0600",
    owner: "展店",
    status: "Review",
    time: "08:30",
    tone: "info",
    workspace: "govern",
  },
  {
    id: "RV-701",
    title: "物件看板照片缺漏",
    description: "Listing Radar 已完成去重，仍缺路口可視性佐證。",
    meta: "Source compliance checked",
    owner: "展店",
    status: "Need data",
    time: "08:18",
    tone: "warning",
    workspace: "network",
  },
  {
    id: "NET-305",
    title: "低效門市重配建議",
    description: "西門小南門店進入 AVM request，NetPlan 三方案待比較。",
    meta: "Rent pressure + cannibalization risk",
    owner: "PM",
    status: "Observe",
    time: "07:54",
    tone: "accent",
    workspace: "network",
  },
];

const decisions: Array<{
  cta: string;
  id: string;
  meta: string;
  status: string;
  title: string;
  tone: Tone;
}> = [
  {
    id: "APR-501",
    title: "SiteScore 複審",
    meta: "CS-1002 WAIT 76，租金合理但競品密度偏高。",
    status: "2h SLA",
    cta: "Open Govern",
    tone: "warning",
  },
  {
    id: "APR-487",
    title: "Google review 回覆",
    meta: "負評涉及付款失敗，客服主管已補充草稿。",
    status: "Needs reason",
    cta: "Review",
    tone: "danger",
  },
  {
    id: "GRW-207",
    title: "PriceOps 折扣上限",
    meta: "模型建議 8%，需確認毛利保護線。",
    status: "Policy",
    cta: "Compare",
    tone: "info",
  },
];

const riskRows: Array<{ label: string; score: number; signal: string; tone: Tone }> = [
  {
    label: "大安復興店",
    score: 92,
    signal: "Payment failure + queue spike",
    tone: "danger",
  },
  {
    label: "板橋中山店",
    score: 78,
    signal: "Kiosk offline + CS wait",
    tone: "warning",
  },
  {
    label: "忠孝敦化店",
    score: 64,
    signal: "Demand gap with staff buffer",
    tone: "accent",
  },
  {
    label: "台北車站店",
    score: 38,
    signal: "Recovered after remote restart",
    tone: "success",
  },
];

const auditFeed = [
  {
    actor: "system / ForecastOps",
    category: "Model snapshot",
    detail: "Updated four-light evidence for ISS-1024 with payment confidence 0.91.",
    time: "09:46",
  },
  {
    actor: "客服主管",
    category: "Decision log",
    detail: "Returned APR-487 reply draft for clearer compensation reason.",
    time: "09:33",
  },
  {
    actor: "展店經理",
    category: "Network review",
    detail: "Marked RV-701 as pending street-front visibility evidence.",
    time: "09:12",
  },
  {
    actor: "PM／稽核",
    category: "Audit trail",
    detail: "Exported approval packet for CS-1002 SiteScore comparison.",
    time: "08:41",
  },
];

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
    id,
    title,
    summary,
    owner,
    status: getTaskStatusLabel(rawStatus),
    priority,
    dueLabel,
    tone,
    workspace,
    href,
    source,
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
  const [activeStoreOpsDialog, setActiveStoreOpsDialog] = useState<StoreOpsWorkflowDialogType | null>(null);
  const [selectedStoreOpsIssue, setSelectedStoreOpsIssue] = useState<Issue | undefined>(undefined);
  const [isRoleMenuOpen, setIsRoleMenuOpen] = useState(false);
  const [isNotificationOpen, setIsNotificationOpen] = useState(false);
  const [isTaskCenterOpen, setIsTaskCenterOpen] = useState(false);
  const [taskCenterLoadState, setTaskCenterLoadState] = useState<TaskCenterLoadState>("idle");
  const [taskCenterSource, setTaskCenterSource] = useState<TaskCenterSource>("fixture");
  const [liveTasks, setLiveTasks] = useState<OperatorTask[]>(taskCenterFixtures);
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState("");
  const [activeCommandIndex, setActiveCommandIndex] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  const [searchValue, setSearchValue] = useState("");
  const commandInputRef = useRef<HTMLInputElement>(null);

  const [liveKpis, setLiveKpis] = useState(kpis);
  const [liveWorkQueue, setLiveWorkQueue] = useState(workQueue);
  const [liveDecisions, setLiveDecisions] = useState(decisions);
  const [liveRiskRows, setLiveRiskRows] = useState(riskRows);
  const [liveAuditFeed, setLiveAuditFeed] = useState(auditFeed);
  const [liveNotifications, setLiveNotifications] = useState(notifications);
  const [liveIssues, setLiveIssues] = useState<Issue[]>(ISSUE_FIXTURES);

  const getSecurityHeaders = (roleId: string) => {
    let systemRole = "operations_manager";
    if (roleId === "opsLead") systemRole = "operations_manager";
    else if (roleId === "supportLead") systemRole = "operations_manager";
    else if (roleId === "facilitiesLead") systemRole = "regional_supervisor";
    else if (roleId === "marketingManager") systemRole = "marketing_manager";
    else if (roleId === "expansionManager") systemRole = "expansion_user";
    else if (roleId === "auditPm") systemRole = "auditor";

    return {
      "X-Subject-Id": `operator-${roleId}`,
      "X-Roles": systemRole,
      "X-Tenant-Id": "tenant-a",
    };
  };

  const activeRole = getOperatorRole(activeRoleId);
  const activeWorkspace = getWorkspace(activeWorkspaceId);

  useEffect(() => {
    async function loadBootstrap() {
      try {
        const res = await fetch("/api/v1/operator/bootstrap", {
          headers: getSecurityHeaders(activeRoleId),
        });
        if (res.ok) {
          const data = await res.json();
          if (data.kpis) setLiveKpis(data.kpis);
          if (data.workQueue) setLiveWorkQueue(data.workQueue);
          if (data.decisions) setLiveDecisions(data.decisions);
          if (data.riskRows) setLiveRiskRows(data.riskRows);
          if (data.auditFeed) setLiveAuditFeed(data.auditFeed);
          if (data.notifications) setLiveNotifications(data.notifications);
          if (data.issues) setLiveIssues(data.issues);
        }
      } catch (err) {
        console.error("Error loading operator bootstrap:", err);
      }
    }
    loadBootstrap();
  }, []);

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

    if (isWorkspaceAllowed(storedRole, targetWorkspaceId)) {
      setActiveWorkspaceId(targetWorkspaceId);
      window.sessionStorage.setItem(workspaceStorageKey, targetWorkspaceId);
    } else {
      setActiveWorkspaceId(DEFAULT_WORKSPACE_ID);
      window.sessionStorage.setItem(workspaceStorageKey, DEFAULT_WORKSPACE_ID);
    }
  }, [searchParams?.ws]);

  useEffect(() => {
    if (!toast) return undefined;

    const timeout = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  useEffect(() => {
    function handleGlobalKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        openCommandPalette();
      } else if (event.key === "Escape") {
        if (isCommandPaletteOpen) {
          closeCommandPalette();
        } else {
          setIsNotificationOpen(false);
          setIsTaskCenterOpen(false);
          setIsRoleMenuOpen(false);
        }
      }
    }

    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, [isCommandPaletteOpen]);

  useEffect(() => {
    if (!isCommandPaletteOpen) return undefined;
    const timeout = window.setTimeout(() => commandInputRef.current?.focus(), 0);
    return () => window.clearTimeout(timeout);
  }, [isCommandPaletteOpen]);

  const queueForRole = useMemo(() => {
    return liveWorkQueue.filter((item) => item.workspace === "today" || isWorkspaceAllowed(activeRole, item.workspace));
  }, [activeRole, liveWorkQueue]);

  const taskSummary = useMemo(() => {
    const activeTasks = liveTasks.filter((task) => !/(done|closed|complete|resolved)/i.test(task.status));
    const urgentTasks = activeTasks.filter((task) => task.tone === "danger" || task.priority.toUpperCase() === "P0");
    return {
      active: activeTasks.length,
      urgent: urgentTasks.length,
    };
  }, [liveTasks]);

  const commandItems = useMemo<CommandPaletteItem[]>(() => {
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

    const taskCommands: CommandPaletteItem[] = liveTasks.slice(0, 12).map((task) => ({
      id: `task-${task.id}`,
      group: "entities",
      title: task.title,
      subtitle: `${task.id} · ${task.owner} · ${task.status}`,
      keywords: [task.id, task.owner, task.priority, task.summary, task.workspace ?? "tasks"],
      tone: task.tone,
      execute: () => handleTaskSelect(task),
    }));

    const queueCommands: CommandPaletteItem[] = queueForRole.slice(0, 8).map((item) => ({
      id: `queue-${item.id}`,
      group: "entities",
      title: item.title,
      subtitle: `${item.id} · ${item.owner} · ${item.status}`,
      keywords: [item.id, item.meta, item.description, item.workspace],
      tone: item.tone,
      execute: () => handleWorkspaceClick(item.workspace),
    }));

    const decisionCommands: CommandPaletteItem[] = liveDecisions.slice(0, 6).map((decision) => ({
      id: `decision-${decision.id}`,
      group: "entities",
      title: decision.title,
      subtitle: `${decision.id} · ${decision.status}`,
      keywords: [decision.id, decision.meta, decision.cta],
      tone: decision.tone,
      execute: () => handleWorkspaceClick("govern"),
    }));

    const issueCommands: CommandPaletteItem[] = liveIssues.slice(0, 8).map((issue) => ({
      id: `issue-${issue.id}`,
      group: "entities",
      title: issue.title,
      subtitle: `${issue.id} · ${issue.storeName}`,
      keywords: [issue.id, issue.storeName, issue.summary],
      tone: issue.severity === "critical" ? "danger" : issue.severity === "high" ? "warning" : "info",
      execute: () => handleWorkspaceClick("store"),
    }));

    const actionCommands: CommandPaletteItem[] = [
      {
        id: "action-task-center",
        group: "actions",
        title: "開啟任務中心",
        subtitle: `${taskSummary.active} active tasks · ${taskSummary.urgent} urgent`,
        keywords: ["task center", "tasks", "todo", "inbox"],
        tone: taskSummary.urgent > 0 ? "danger" : "info",
        execute: () => openTaskCenter(),
      },
      {
        id: "action-notifications",
        group: "actions",
        title: "開啟通知",
        subtitle: `${liveNotifications.length} notifications`,
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

    return [
      ...workspaceCommands,
      ...routeCommands,
      ...taskCommands,
      ...queueCommands,
      ...decisionCommands,
      ...issueCommands,
      ...actionCommands,
    ];
  }, [liveDecisions, liveIssues, liveNotifications, liveTasks, queueForRole, taskSummary.active, taskSummary.urgent]);

  const filteredCommandItems = useMemo(() => {
    const query = commandQuery.trim().toLowerCase();
    const matches = query
      ? commandItems.filter((item) => commandSearchText(item).includes(query))
      : commandItems.filter((item) => item.group !== "entities").slice(0, 10);
    return matches.slice(0, 18);
  }, [commandItems, commandQuery]);

  const commandGroups = useMemo(() => {
    const orderedGroups: CommandGroup[] = ["pages", "entities", "actions"];
    return orderedGroups
      .map((group) => ({
        group,
        items: filteredCommandItems.filter((item) => item.group === group),
      }))
      .filter((group) => group.items.length > 0);
  }, [filteredCommandItems]);

  useEffect(() => {
    setActiveCommandIndex(0);
  }, [commandQuery, isCommandPaletteOpen]);

  function showToast(message: string) {
    setToast(message);
  }

  function openCommandPalette(query = "") {
    setCommandQuery(query);
    setSearchValue(query);
    setActiveCommandIndex(0);
    setIsCommandPaletteOpen(true);
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
      handleWorkspaceClick(task.workspace);
      showToast(`${task.id} opened in ${getWorkspace(task.workspace).label}`);
      return;
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

  async function handleApprovalDecision(approvalId: string, status: string, payload: any) {
    const correlationId = "corr-" + Math.random().toString(36).substring(2, 11);
    const idempotencyKey = "idem-" + Math.random().toString(36).substring(2, 11);
    try {
      const res = await fetch(`/api/v1/operator/approvals/${approvalId}/decision`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
          "X-Correlation-Id": correlationId,
          ...getSecurityHeaders(activeRoleId),
        },
        body: JSON.stringify({ status, ...payload }),
      });
      if (res.ok) {
        showToast("決策已送出");
        const freshRes = await fetch("/api/v1/operator/bootstrap", {
          headers: getSecurityHeaders(activeRoleId),
        });
        if (freshRes.ok) {
          const freshData = await freshRes.json();
          if (freshData.decisions) setLiveDecisions(freshData.decisions);
          if (freshData.auditFeed) setLiveAuditFeed(freshData.auditFeed);
          if (freshData.workQueue) setLiveWorkQueue(freshData.workQueue);
        }
      }
    } catch (err) {
      console.error("Error submitting approval decision:", err);
    }
  }

  function handleWorkspaceClick(workspaceId: WorkspaceId) {
    const workspace = getWorkspace(workspaceId);

    if (!isWorkspaceAllowed(activeRole, workspaceId)) {
      showToast(`${activeRole.label} 暫無 ${workspace.label} 權限`);
      return;
    }

    setActiveWorkspaceId(workspaceId);
    window.sessionStorage.setItem(workspaceStorageKey, workspaceId);
    if (workspaceId !== "store") {
      setActiveStoreOpsDialog(null);
    }

    if (workspaceId === "growth") {
      showToast("營收成長 shell 已就緒，等待 ODP-OC-007 接工作台");
    }
  }

  function handleRoleSelect(roleId: OperatorRoleId) {
    const nextRole = getOperatorRole(roleId);
    setActiveRoleId(nextRole.id);
    window.sessionStorage.setItem(roleStorageKey, nextRole.id);
    setIsRoleMenuOpen(false);

    if (!isWorkspaceAllowed(nextRole, activeWorkspaceId)) {
      setActiveWorkspaceId(DEFAULT_WORKSPACE_ID);
      window.sessionStorage.setItem(workspaceStorageKey, DEFAULT_WORKSPACE_ID);
    }

    showToast(`已切換為 ${nextRole.label}`);
  }

  function handleReset() {
    window.sessionStorage.removeItem(roleStorageKey);
    window.sessionStorage.removeItem(workspaceStorageKey);
    setActiveRoleId(DEFAULT_OPERATOR_ROLE_ID);
    setActiveWorkspaceId(DEFAULT_WORKSPACE_ID);
    setSearchValue("");
    showToast("POC session 已重置為營運主管 Today");
  }

  function openStoreOpsWorkflow(dialog: StoreOpsWorkflowDialogType, issue: Issue) {
    setSelectedStoreOpsIssue(issue);
    setActiveStoreOpsDialog(dialog);
  }

  return (
    <div className={styles.console} data-testid="operator-console">
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
          {WORKSPACES.map((workspace) => {
            const isActive = activeWorkspaceId === workspace.id;
            const isLocked = !isWorkspaceAllowed(activeRole, workspace.id);

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
                onClick={() => handleWorkspaceClick(workspace.id)}
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
          <label className={styles.searchBox}>
            <span aria-hidden="true">⌘K</span>
            <input
              aria-label="Open command palette"
              data-testid="operator-command-trigger"
              onChange={(event) => openCommandPalette(event.target.value)}
              onFocus={() => openCommandPalette(searchValue)}
              placeholder="搜尋或執行命令..."
              value={searchValue}
            />
          </label>

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
              ! {liveNotifications.length}
            </Button>
            {isNotificationOpen ? (
              <div className={styles.notificationPanel}>
                <div className={styles.popoverTitle}>Notifications</div>
                {liveNotifications.map((notification) => (
                  <article className={styles.notificationItem} key={notification.title}>
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
              任務中心 <strong>{taskSummary.active}</strong>
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
                    <strong>{taskSummary.active}</strong>
                    Active
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
              <div className={styles.roleMenu}>
                <div className={styles.popoverTitle}>Role switcher</div>
                {OPERATOR_ROLES.map((role) => (
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
          <strong>• POC Demo</strong>
          <span>資料為 mock，操作僅保存在本 session，用於驗證流程與使用情境。</span>
        </div>
        <Button onClick={handleReset} size="sm" variant="secondary">
          重設示範資料
        </Button>
      </div>

      <main className={styles.shell}>
        {activeWorkspaceId === "today" ? (
          <DesignTodayWorkspace
            onQueueSelect={(workspaceId) => handleWorkspaceClick(workspaceId)}
            kpis={liveKpis}
            todayRows={queueForRole}
            decisions={liveDecisions}
            riskStores={liveRiskRows}
            auditFeed={liveAuditFeed}
          />
        ) : activeWorkspaceId === "store" ? (
          <DesignStoreOpsWorkspace onOpenWorkflow={openStoreOpsWorkflow} issues={liveIssues} />
        ) : activeWorkspaceId === "network" ? (
          <WorkspaceChrome activeRoleLabel={activeRole.label} workspace={activeWorkspace}>
            <NetworkFindAreasWorkspace
              callbacks={{
                onChangeLens: (lens) => showToast(`Network lens: ${lens}`),
                onScoreCandidate: (candidate, heatZone) => showToast(`${candidate.id} scoring opened for ${heatZone.id}`),
                onSelectHeatZone: (heatZone) => showToast(`${heatZone.id} selected`),
                onSourceListings: (heatZone) => showToast(`${heatZone.id} source listings callback recorded`),
                onSubmitReview: (heatZone) => showToast(`${heatZone.id} review submitted to POC shell`),
                onToggleTracked: (heatZone, tracked) => showToast(`${heatZone.id} ${tracked ? "tracked" : "untracked"}`),
                onDecideReview: (reviewId, status, reason) =>
                  handleApprovalDecision(reviewId, status === "approved" ? "approved" : status === "rejected" ? "rejected" : "returned", { reason }),
              }}
            />
          </WorkspaceChrome>
        ) : activeWorkspaceId === "govern" ? (
          <WorkspaceChrome activeRoleLabel={activeRole.label} workspace={activeWorkspace}>
            <GovernanceWorkspace
              callbacks={{
                onApprove: (payload) => handleApprovalDecision(payload.approvalId, "approved", payload),
                onReject: (payload) => handleApprovalDecision(payload.approvalId, "rejected", payload),
                onReturn: (payload) => handleApprovalDecision(payload.approvalId, "returned", payload),
                onSelectApproval: (approval) => showToast(`${approval.id} selected`),
              }}
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
                  body: JSON.stringify(event.payload),
                });
                if (res.ok) {
                  const data = await res.json();
                  if (data.workQueue) setLiveWorkQueue(data.workQueue);
                  if (data.decisions) setLiveDecisions(data.decisions);
                  if (data.auditFeed) setLiveAuditFeed(data.auditFeed);
                  // Refresh bootstrap
                  const freshRes = await fetch("/api/v1/operator/bootstrap", {
                    headers: getSecurityHeaders(activeRoleId),
                  });
                  if (freshRes.ok) {
                    const freshData = await freshRes.json();
                    if (freshData.workQueue) setLiveWorkQueue(freshData.workQueue);
                    if (freshData.decisions) setLiveDecisions(freshData.decisions);
                    if (freshData.auditFeed) setLiveAuditFeed(freshData.auditFeed);
                    if (freshData.issues) setLiveIssues(freshData.issues);
                  }
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
                aria-label="Command palette search"
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
            <div className={styles.commandResults} role="listbox">
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

function TodayWorkspace({
  onQueueSelect,
  queueRows,
}: {
  onQueueSelect: (workspaceId: WorkspaceId) => void;
  queueRows: typeof workQueue;
}) {
  return (
    <>
      <section className={styles.metricGrid} aria-label="Today KPI cards">
        {kpis.map((metric) => (
          <MetricCard
            delta={metric.delta}
            key={metric.label}
            label={metric.label}
            meta={metric.meta}
            tone={metric.tone}
            value={metric.value}
          />
        ))}
      </section>

      <div className={styles.todayGrid}>
        <div className={styles.todayPrimary}>
          <SectionPanel
            actions={<Chip tone="danger">{queueRows.length} urgent</Chip>}
            eyebrow="Role-aware queue"
            title="Today most important"
          >
            <div className={styles.queueList}>
              {queueRows.map((item) => (
                <QueueRow
                  description={item.description}
                  id={item.id}
                  key={item.id}
                  meta={item.meta}
                  onClick={() => onQueueSelect(item.workspace)}
                  owner={item.owner}
                  status={item.status}
                  time={item.time}
                  title={item.title}
                  tone={item.tone}
                />
              ))}
            </div>
          </SectionPanel>

          <div className={styles.lowerGrid}>
            <SectionPanel eyebrow="Store signal" title="Risk snapshot">
              <div className={styles.riskList}>
                {riskRows.map((row) => (
                  <RiskRow key={row.label} label={row.label} score={row.score} signal={row.signal} tone={row.tone} />
                ))}
              </div>
            </SectionPanel>

            <SectionPanel eyebrow="Evidence health" title="Operational signals">
              <div className={styles.evidenceGrid}>
                <EvidenceCard label="Data freshness" tone="success" value="06:00">
                  ForecastOps, payment, review connectors refreshed.
                </EvidenceCard>
                <EvidenceCard label="Camera privacy" tone="warning" value="Locked">
                  Purpose flow required before any media surface.
                </EvidenceCard>
                <EvidenceCard label="Model confidence" tone="info" value="0.84">
                  8 recommendations above confidence threshold.
                </EvidenceCard>
              </div>
            </SectionPanel>
          </div>
        </div>

        <aside className={styles.todayRail}>
          <SectionPanel eyebrow="Approval center" title="Decision rail">
            <div className={styles.decisionList}>
              {decisions.map((decision) => (
                <DecisionCard
                  cta={decision.cta}
                  id={decision.id}
                  key={decision.id}
                  meta={decision.meta}
                  status={decision.status}
                  title={decision.title}
                  tone={decision.tone}
                />
              ))}
            </div>
          </SectionPanel>

          <SectionPanel eyebrow="Traceability" title="Recent audit feed">
            <div className={styles.auditList}>
              {auditFeed.map((event) => (
                <AuditRow
                  actor={event.actor}
                  category={event.category}
                  detail={event.detail}
                  key={`${event.time}-${event.category}`}
                  time={event.time}
                />
              ))}
            </div>
          </SectionPanel>
        </aside>
      </div>
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
