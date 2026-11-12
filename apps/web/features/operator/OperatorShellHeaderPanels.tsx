"use client";

import { Chip, StatusBadge } from "./components";
import { ADMIN_PATH } from "./operatorSession";
import { WORKSPACES, type OperatorRole, type OperatorRoleId } from "./navigation";
import type { OperatorTask } from "./OperatorConsole";
import type { OperatorShellEnvelope, ShellTarget } from "./shellEnvelope";
import styles from "./operator.module.css";

export function OperatorTaskPopover({ taskCenterCount, taskCenterSource, taskCenterLoadState, taskSummary, liveTasks, handleTaskSelect }: {
  taskCenterCount: number | null; taskCenterSource: "api" | "fixture";
  taskCenterLoadState: "idle" | "loading" | "ready" | "fallback" | "empty" | "error"; taskSummary: { urgent: number }; liveTasks: OperatorTask[]; handleTaskSelect: (task: OperatorTask) => void;
}) {
  // Only a completed Task API read (or explicit local fixture mode) may claim
  // API status or counts. Urgent/Visible describe the rows actually shown.
  const apiReadCompleted = taskCenterSource === "api" && (taskCenterLoadState === "ready" || taskCenterLoadState === "empty");
  const countsClaimable = apiReadCompleted || taskCenterSource === "fixture";
  const rowsShown = countsClaimable || taskCenterLoadState === "error";
  const sourceLabel = taskCenterLoadState === "loading" ? "SYNCING"
    : taskCenterSource === "fixture" ? "FIXTURE"
      : apiReadCompleted ? "API" : taskCenterLoadState === "error" ? "UNAVAILABLE" : "NOT LOADED";
  return (<div className={styles.taskCenterPanel} data-testid="operator-task-center" data-task-read-state={taskCenterLoadState}>
                  <div className={styles.taskCenterHeader}>
                    <div>
                      <div className={styles.popoverTitle}>Task center</div>
                      <strong>我的待辦與核准</strong>
                    </div>
                    <Chip tone={apiReadCompleted ? "success" : "neutral"}>
                      {sourceLabel}
                    </Chip>
                  </div>
                  <div className={styles.taskCenterStats} aria-label="Task center summary">
                    <span>
                      <strong>{countsClaimable ? taskCenterCount ?? "—" : "—"}</strong>
                      API
                    </span>
                    <span>
                      <strong>{rowsShown ? taskSummary.urgent : "—"}</strong>
                      Urgent
                    </span>
                    <span>
                      <strong>{rowsShown ? liveTasks.length : "—"}</strong>
                      Visible
                    </span>
                  </div>
                  <div className={styles.taskList}>
                    {taskCenterLoadState === "idle" && taskCenterSource === "api" ? (
                      <div className={styles.commandEmpty} data-testid="operator-task-center-not-loaded">
                        Task API 尚未讀取；目前不顯示任務數。
                      </div>
                    ) : taskCenterLoadState === "empty" || taskCenterLoadState === "error" ? (
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
                </div>); }

export function OperatorRoleMenu({ rolesForShell, activeRole, handleRoleSelect, handleLogout }: {
  rolesForShell: OperatorRole[]; activeRole: OperatorRole; handleRoleSelect: (roleId: OperatorRoleId) => void; handleLogout: () => Promise<void>;
}) { return (<div className={styles.roleMenu} data-screen-label="Role Switch Menu">
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
                </div>); }

export function OperatorSearchResults({ matches, activeIndex, onSelect }: {
  matches: OperatorShellEnvelope["search"]["items"]; activeIndex: number;
  onSelect: (target: ShellTarget, label: string) => void;
}) { return (
                <div className={styles.searchPanel} data-testid="operator-search-results" role="listbox">
                  <div className={styles.popoverTitle}>Search</div>
                  {matches.length ? (
                    matches.map((item, index) => (
                      <button
                        aria-selected={index === activeIndex}
                        className={styles.searchResult}
                        data-target-entity={item.target.entityId}
                        data-target-tab={item.target.tab}
                        data-target-workspace={item.target.workspace}
                        key={item.id}
                        onMouseDown={(event) => {
                          event.preventDefault();
                          onSelect(item.target, item.entityId);
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
); }
