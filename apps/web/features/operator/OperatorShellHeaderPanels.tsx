"use client";

import { Chip, StatusBadge } from "./components";
import { ADMIN_PATH } from "./operatorSession";
import { WORKSPACES, type OperatorRole, type OperatorRoleId } from "./navigation";
import type { OperatorTask } from "./OperatorConsole";
import styles from "./operator.module.css";

export function OperatorTaskPopover({ shellEnvelope, taskCenterSource, taskCenterLoadState, taskSummary, liveTasks, handleTaskSelect }: {
  shellEnvelope: { header: { counts: { taskCenter: number } } }; taskCenterSource: "api" | "fixture";
  taskCenterLoadState: "idle" | "loading" | "ready" | "fallback" | "empty" | "error"; taskSummary: { urgent: number }; liveTasks: OperatorTask[]; handleTaskSelect: (task: OperatorTask) => void;
}) { return (<div className={styles.taskCenterPanel} data-testid="operator-task-center">
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
