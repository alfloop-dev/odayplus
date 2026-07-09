"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
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

export function OperatorConsole({ searchParams = {} }: { searchParams?: Record<string, string | string[] | undefined> }) {
  const [activeRoleId, setActiveRoleId] = useState<OperatorRoleId>(DEFAULT_OPERATOR_ROLE_ID);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<WorkspaceId>(DEFAULT_WORKSPACE_ID);
  const [activeStoreOpsDialog, setActiveStoreOpsDialog] = useState<StoreOpsWorkflowDialogType | null>(null);
  const [selectedStoreOpsIssue, setSelectedStoreOpsIssue] = useState<Issue | undefined>(undefined);
  const [isRoleMenuOpen, setIsRoleMenuOpen] = useState(false);
  const [isNotificationOpen, setIsNotificationOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [searchValue, setSearchValue] = useState("");

  const activeRole = getOperatorRole(activeRoleId);
  const activeWorkspace = getWorkspace(activeWorkspaceId);

  useEffect(() => {
    if (searchParams && typeof searchParams.ws === "string") {
      const ws = searchParams.ws as WorkspaceId;
      if (WORKSPACES.some((w) => w.id === ws) && isWorkspaceAllowed(activeRole, ws)) {
        setActiveWorkspaceId(ws);
      }
    }
  }, [searchParams?.ws, activeRole]);

  useEffect(() => {
    const storedRole = getOperatorRole(window.sessionStorage.getItem(roleStorageKey));
    const storedWorkspace = getWorkspace(window.sessionStorage.getItem(workspaceStorageKey));

    setActiveRoleId(storedRole.id);
    setActiveWorkspaceId(isWorkspaceAllowed(storedRole, storedWorkspace.id) ? storedWorkspace.id : DEFAULT_WORKSPACE_ID);
  }, []);

  useEffect(() => {
    if (!toast) return undefined;

    const timeout = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const queueForRole = useMemo(() => {
    return workQueue.filter((item) => item.workspace === "today" || isWorkspaceAllowed(activeRole, item.workspace));
  }, [activeRole]);

  function showToast(message: string) {
    setToast(message);
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
            <span aria-hidden="true">/</span>
            <input
              aria-label="Global search"
              onChange={(event) => setSearchValue(event.target.value)}
              placeholder="搜尋門市、案件、物件..."
              value={searchValue}
            />
          </label>

          <div className={styles.popoverAnchor}>
            <Button
              aria-expanded={isNotificationOpen}
              aria-label="Open notifications"
              onClick={() => setIsNotificationOpen((open) => !open)}
              size="sm"
              variant="ghost"
            >
              ! 3
            </Button>
            {isNotificationOpen ? (
              <div className={styles.notificationPanel}>
                <div className={styles.popoverTitle}>Notifications</div>
                {notifications.map((notification) => (
                  <article className={styles.notificationItem} key={notification.title}>
                    <StatusBadge tone={notification.tone}>{notification.title}</StatusBadge>
                    <p>{notification.detail}</p>
                  </article>
                ))}
              </div>
            ) : null}
          </div>

          <button
            className={styles.approvalChip}
            onClick={() => handleWorkspaceClick("govern")}
            type="button"
          >
            待核准 <strong>5</strong>
          </button>

          <div className={styles.popoverAnchor}>
            <Button
              aria-expanded={isRoleMenuOpen}
              onClick={() => setIsRoleMenuOpen((open) => !open)}
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
          <DesignTodayWorkspace onQueueSelect={(workspaceId) => handleWorkspaceClick(workspaceId)} />
        ) : activeWorkspaceId === "store" ? (
          <DesignStoreOpsWorkspace onOpenWorkflow={openStoreOpsWorkflow} />
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
              }}
            />
          </WorkspaceChrome>
        ) : activeWorkspaceId === "govern" ? (
          <WorkspaceChrome activeRoleLabel={activeRole.label} workspace={activeWorkspace}>
            <GovernanceWorkspace
              callbacks={{
                onApprove: (payload) => showToast(`${payload.approvalId} approve callback recorded`),
                onReject: (payload) => showToast(`${payload.approvalId} reject callback recorded`),
                onReturn: (payload) => showToast(`${payload.approvalId} return callback recorded`),
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
          onSubmit: (event) => showToast(`${event.payload.issueId} ${event.type} submitted to POC shell`),
        }}
        issue={selectedStoreOpsIssue}
        onClose={() => setActiveStoreOpsDialog(null)}
      />

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
