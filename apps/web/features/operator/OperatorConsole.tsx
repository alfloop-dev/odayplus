"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
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
import {
  TodayWorkspace as ApiTodayWorkspace,
  normalizeShellEnvelope,
  type OperatorShellEnvelope,
  type ShellTarget,
} from "./TodayWorkspace";
import { StoreOpsWorkflowDialogs } from "./StoreOpsWorkflowDialogs";
import type { StoreOpsWorkflowDialogType } from "./storeOpsWorkflowTypes";
import type { Issue } from "./types";
import { ISSUE_FIXTURES } from "./fixtures";

const roleStorageKey = "oday.operator.role";
const workspaceStorageKey = "oday.operator.workspace";

const rolePermissionHeaders: Record<OperatorRoleId, string> = {
  "cs-lead": "operations_manager",
  "expansion-manager": "expansion_user",
  "field-lead": "regional_supervisor",
  "marketing-manager": "marketing_manager",
  "ops-lead": "operations_manager",
  "pm-audit": "auditor",
};

function isOperatorRoleId(value: string): value is OperatorRoleId {
  return OPERATOR_ROLES.some((role) => role.id === value);
}

function isWorkspaceId(value: string): value is WorkspaceId {
  return WORKSPACES.some((workspace) => workspace.id === value);
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

export function OperatorConsole({ searchParams = {} }: { searchParams?: Record<string, string | string[] | undefined> }) {
  const [activeRoleId, setActiveRoleId] = useState<OperatorRoleId>(DEFAULT_OPERATOR_ROLE_ID);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<WorkspaceId>(DEFAULT_WORKSPACE_ID);
  const [activeTabId, setActiveTabId] = useState("overview");
  const [activeStoreOpsDialog, setActiveStoreOpsDialog] = useState<StoreOpsWorkflowDialogType | null>(null);
  const [selectedStoreOpsIssue, setSelectedStoreOpsIssue] = useState<Issue | undefined>(undefined);
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
  const [isRoleMenuOpen, setIsRoleMenuOpen] = useState(false);
  const [isNotificationOpen, setIsNotificationOpen] = useState(false);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [searchActiveIndex, setSearchActiveIndex] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  const [searchValue, setSearchValue] = useState("");
  const [hasHydratedPreferences, setHasHydratedPreferences] = useState(false);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  const [shellEnvelope, setShellEnvelope] = useState<OperatorShellEnvelope>(() => normalizeShellEnvelope());
  const [liveNotifications, setLiveNotifications] = useState<any[]>(notifications);
  const [liveIssues] = useState<Issue[]>(ISSUE_FIXTURES);

  const getSecurityHeaders = (roleId: OperatorRoleId) => {
    return {
      "X-Operator-Role": roleId,
      "X-Roles": rolePermissionHeaders[roleId],
      "X-Subject-Id": `operator-${roleId}`,
      "X-Tenant-Id": "tenant-a",
    };
  };

  const applyOperatorEnvelope = (payload: unknown) => {
    const nextEnvelope = normalizeShellEnvelope(payload);
    setShellEnvelope(nextEnvelope);
    setLiveNotifications(nextEnvelope.notifications);
  };

  const rolesForShell = useMemo(() => {
    return shellEnvelope.navigation.roles.length
      ? shellEnvelope.navigation.roles.map((role) => ({
          allowedWorkspaces: role.allowedWorkspaces,
          id: isOperatorRoleId(role.id) ? role.id : DEFAULT_OPERATOR_ROLE_ID,
          label: role.label,
          subtitle: role.subtitle,
        }))
      : OPERATOR_ROLES;
  }, [shellEnvelope.navigation.roles]);

  const activeRole = useMemo(() => {
    return rolesForShell.find((role) => role.id === activeRoleId) ?? getOperatorRole(activeRoleId);
  }, [activeRoleId, rolesForShell]);

  const workspaceNavItems = useMemo(() => {
    return shellEnvelope.navigation.workspaces.length ? shellEnvelope.navigation.workspaces : WORKSPACES;
  }, [shellEnvelope.navigation.workspaces]);

  const activeWorkspace = getWorkspace(activeWorkspaceId);

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
  }, [searchParams?.ws]);

  useEffect(() => {
    if (!hasHydratedPreferences) return;

    async function loadShellEnvelope() {
      try {
        const headers = getSecurityHeaders(activeRoleId);
        const bootstrapRes = await fetch("/api/v1/operator/bootstrap", { headers });
        if (bootstrapRes.ok) {
          applyOperatorEnvelope(await bootstrapRes.json());
        }

        const todayRes = await fetch("/api/v1/operator/today", { headers });
        if (todayRes.ok) {
          applyOperatorEnvelope(await todayRes.json());
        }
      } catch (err) {
        console.error("Error loading operator shell envelope:", err);
      }
    }

    loadShellEnvelope();
  }, [activeRoleId, hasHydratedPreferences]);

  useEffect(() => {
    if (!toast) return undefined;

    const timeout = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(timeout);
  }, [toast]);

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
    setSearchValue("");

    if (workspaceId === "store") {
      const issue = liveIssues.find((item) => item.id === target.entityId);
      setSelectedStoreOpsIssue(issue);
    } else {
      setActiveStoreOpsDialog(null);
    }

    showToast(`${label} opened: ${workspace.label}${target.tab ? ` / ${target.tab}` : ""}`);
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
        applyOperatorEnvelope(await res.json());
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
    setSelectedEntityId(null);
    setActiveTabId("overview");
    updateDeepLink({ workspace: workspaceId, tab: "overview" });
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
      updateDeepLink({ workspace: DEFAULT_WORKSPACE_ID, tab: "overview" });
    }
    setSelectedEntityId(null);
    setActiveTabId("overview");

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
    updateDeepLink({ workspace: DEFAULT_WORKSPACE_ID, tab: "overview" });
    showToast("POC session 已重置為營運主管 Today");
  }

  function openStoreOpsWorkflow(dialog: StoreOpsWorkflowDialogType, issue: Issue) {
    setSelectedStoreOpsIssue(issue);
    setActiveStoreOpsDialog(dialog);
  }

  useEffect(() => {
    function handleGlobalKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
        event.preventDefault();
        setIsSearchOpen(true);
        searchInputRef.current?.focus();
      }
    }

    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, []);

  function handleSearchKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSearchActiveIndex((index) => Math.min(index + 1, Math.max(searchMatches.length - 1, 0)));
      setIsSearchOpen(true);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setSearchActiveIndex((index) => Math.max(index - 1, 0));
      setIsSearchOpen(true);
      return;
    }
    if (event.key === "Escape") {
      setIsSearchOpen(false);
      return;
    }
    if (event.key === "Enter" && searchMatches[searchActiveIndex]) {
      event.preventDefault();
      const selected = searchMatches[searchActiveIndex];
      handleTargetSelect(selected.target, selected.entityId);
    }
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
          <label className={styles.searchBox}>
            <span aria-hidden="true">/</span>
            <input
              aria-activedescendant={
                isSearchOpen && searchMatches[searchActiveIndex] ? `operator-search-${searchMatches[searchActiveIndex].id}` : undefined
              }
              aria-controls="operator-search-results"
              aria-label="Global search"
              aria-expanded={isSearchOpen}
              aria-haspopup="listbox"
              onBlur={() => window.setTimeout(() => setIsSearchOpen(false), 120)}
              onChange={(event) => {
                setSearchValue(event.target.value);
                setIsSearchOpen(true);
              }}
              onFocus={() => setIsSearchOpen(true)}
              onKeyDown={handleSearchKeyDown}
              placeholder="搜尋門市、案件、物件..."
              ref={searchInputRef}
              role="combobox"
              value={searchValue}
            />
            {isSearchOpen && searchMatches.length ? (
              <div className={styles.searchResults} id="operator-search-results" role="listbox">
                {searchMatches.map((item, index) => (
                  <button
                    aria-selected={index === searchActiveIndex}
                    className={index === searchActiveIndex ? styles.searchResult_active : styles.searchResult}
                    data-target-entity={item.target.entityId}
                    data-target-tab={item.target.tab}
                    data-target-workspace={item.target.workspace}
                    id={`operator-search-${item.id}`}
                    key={item.id}
                    onMouseDown={(event) => {
                      event.preventDefault();
                      handleTargetSelect(item.target, item.entityId);
                    }}
                    role="option"
                    type="button"
                  >
                    <strong>{item.label}</strong>
                    <span>{item.description}</span>
                  </button>
                ))}
              </div>
            ) : null}
          </label>

          <div className={styles.popoverAnchor}>
            <Button
              aria-expanded={isNotificationOpen}
              aria-label="Open notifications"
              onClick={() => setIsNotificationOpen((open) => !open)}
              size="sm"
              variant="ghost"
            >
              ! {shellEnvelope.header.counts.notifications}
            </Button>
            {isNotificationOpen ? (
              <div className={styles.notificationPanel}>
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

          <button
            className={styles.approvalChip}
            data-testid="operator-approval-count"
            onClick={() => handleWorkspaceClick("govern")}
            type="button"
          >
            待核准 <strong>{shellEnvelope.header.counts.approvals}</strong>
          </button>

          <button
            className={styles.taskCenterChip}
            data-testid="operator-task-center-count"
            onClick={() => handleWorkspaceClick("today")}
            type="button"
          >
            Task Center <strong>{shellEnvelope.header.counts.taskCenter}</strong>
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
          <span>Shell、Today、通知與核准數字由 Operator API envelope 驅動；寫入後即時刷新本 session。</span>
        </div>
        <Button onClick={handleReset} size="sm" variant="secondary">
          重設示範資料
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
            onOpenWorkflow={openStoreOpsWorkflow}
            issues={liveIssues}
          />
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
                  applyOperatorEnvelope(await res.json());
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
