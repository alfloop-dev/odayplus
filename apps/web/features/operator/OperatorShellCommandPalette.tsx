"use client";

import { useLayoutEffect, type Dispatch, type KeyboardEvent as ReactKeyboardEvent, type RefObject, type SetStateAction } from "react";
import { Button, StatusBadge, type Tone } from "./components";
import { WORKSPACES, isWorkspaceAllowed, type OperatorRole, type OperatorRoleId, type WorkspaceId } from "./navigation";
import { NETWORK_TAB_IDS, parseNetworkTabIndex } from "./network/networkUrlState";
import type { OperatorShellEnvelope, ShellTarget } from "./shellEnvelope";
import type { Issue } from "./types";
import type { OperatorTask } from "./OperatorConsole";
import styles from "./operator.module.css";

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

function commandSearchText(item: CommandPaletteItem): string {
  return [item.title, item.subtitle, item.group, ...item.keywords].join(" ").toLowerCase();
}

function toDomSafeId(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "-");
}

export function OperatorShellCommandPalette({ activeRole, rolesForShell, shellEnvelope, fixturesAllowed, liveTasks, liveIssues, liveWorkQueue, liveDecisions, liveNotifications, taskSummary, confirmedNotificationCount, commandQuery, isCommandPaletteOpen, activeCommandIndex, setActiveCommandIndex, setCommandQuery, setSearchValue, setIsNotificationOpen, setIsTaskCenterOpen, commandInputRef, closeCommandPalette, handleWorkspaceClick, handleTargetSelect, handleTaskSelect, openTaskCenter, handleReset, handleRoleSelect }: {
  activeRole: OperatorRole; rolesForShell: OperatorRole[]; shellEnvelope: OperatorShellEnvelope; fixturesAllowed: boolean;
  liveTasks: OperatorTask[]; liveIssues: Issue[]; liveWorkQueue: OperatorShellEnvelope["workQueue"]; liveDecisions: OperatorShellEnvelope["decisions"]; liveNotifications: OperatorShellEnvelope["notifications"];
  taskSummary: { urgent: number }; confirmedNotificationCount: number | null;
  commandQuery: string; isCommandPaletteOpen: boolean; activeCommandIndex: number;
  setActiveCommandIndex: Dispatch<SetStateAction<number>>; setCommandQuery: Dispatch<SetStateAction<string>>; setSearchValue: Dispatch<SetStateAction<string>>;
  setIsNotificationOpen: Dispatch<SetStateAction<boolean>>; setIsTaskCenterOpen: Dispatch<SetStateAction<boolean>>;
  commandInputRef: RefObject<HTMLInputElement>; closeCommandPalette: () => void;
  handleWorkspaceClick: (workspaceId: WorkspaceId) => void; handleTargetSelect: (target: ShellTarget, label: string) => void;
  handleTaskSelect: (task: OperatorTask) => void; openTaskCenter: () => void; handleReset: () => void; handleRoleSelect: (roleId: OperatorRoleId) => void;
}) {
  useLayoutEffect(() => { commandInputRef.current?.focus(); }, [commandInputRef]);

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

function executeCommand(item: CommandPaletteItem) {
    closeCommandPalette();
    item.execute();
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

  return (<div
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
        </div>);
}
