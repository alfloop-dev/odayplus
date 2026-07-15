/**
 * Task Center (ODP-PGAP-SHELL-001, acceptance §2).
 *
 * Durable assignment, SLA filtering, deep links and permission-aware actions.
 * Filters are links, not client state, so a filtered view is a shareable URL
 * and a deep link survives a reload — that is what "deep link" has to mean for
 * an operator handing a task to a colleague.
 *
 * The server decides which actions the role may take; this screen renders that
 * answer (including the refusal reason) and never re-derives it locally.
 */
import Link from "next/link";
import { Badge, PageHeader } from "@oday-plus/ui";
import type { ShellTasksResponse } from "@oday-plus/openapi-client";
import type { ApiResource } from "./resource.ts";
import { ShellDataSource, ShellResourceState, ShellState } from "./ShellStates.tsx";
import { AssignTaskForm } from "./AssignTaskForm.tsx";
import { SEVERITY_LABEL, SEVERITY_TONE, SLA_LABEL, SLA_TONE } from "./vocabulary.ts";
import { formatStamp } from "./HomeWorkspace.tsx";
import styles from "./shell.module.css";

const SLA_FILTERS: Array<{ key: string; label: string }> = [
  { key: "", label: "全部" },
  { key: "breached", label: "已逾期" },
  { key: "at-risk", label: "即將到期" },
  { key: "on-track", label: "時程正常" },
];

const ASSIGNEE_FILTERS: Array<{ key: string; label: string }> = [
  { key: "", label: "全部" },
  { key: "me", label: "指派給我" },
  { key: "unassigned", label: "未指派" },
];

export type TaskCenterSearchParams = {
  sla?: string;
  assignee?: string;
  status?: string;
  taskId?: string;
};

export function TaskCenterWorkspace({
  tasks,
  searchParams,
}: {
  tasks: ApiResource<ShellTasksResponse>;
  searchParams: TaskCenterSearchParams;
}) {
  const data = tasks.data;

  return (
    <>
      <PageHeader
        title="任務中心"
        summary="待核准、待補件與待觀察的決策任務：指派、SLA 與權限內動作。"
        status={
          data
            ? { label: `${data.count} / ${data.total}`, tone: "blue", marker: "•" }
            : { label: "無法取得", tone: "red", marker: "•" }
        }
        breadcrumb={[{ label: "總覽", href: "/" }, { label: "任務中心" }]}
        lastUpdated={data ? formatStamp(data.meta.generatedAt) : "—"}
      />
      <div className="odp-content" data-testid="shell-tasks">
        {data ? (
          <TaskCenterBody data={data} tasks={tasks} searchParams={searchParams} />
        ) : (
          <ShellResourceState resource={tasks} testId="tasks-state" />
        )}
      </div>
    </>
  );
}

function TaskCenterBody({
  data,
  tasks,
  searchParams,
}: {
  data: ShellTasksResponse;
  tasks: ApiResource<ShellTasksResponse>;
  searchParams: TaskCenterSearchParams;
}) {
  const assignAction = data.actions.find((action) => action.key === "task.assign");
  const canAssign = assignAction?.allowed ?? false;

  return (
    <>
      <div className={styles.statusRow}>
        <ShellDataSource
          resource={tasks}
          endpoint="/operator/shell/tasks"
          testId="tasks-data-source"
        />
        {assignAction && !assignAction.allowed ? (
          <span className={styles.rowMeta} data-testid="tasks-assign-denied">
            {assignAction.reason}
          </span>
        ) : null}
      </div>

      <nav className={styles.filters} aria-label="SLA 篩選" data-testid="tasks-filter-sla">
        {SLA_FILTERS.map((filter) => (
          <FilterLink
            key={filter.key || "all"}
            label={filter.label}
            count={filter.key ? data.facets.sla[filter.key] : data.total}
            href={buildHref(searchParams, { sla: filter.key || undefined })}
            active={(searchParams.sla ?? "") === filter.key}
            testId={`filter-sla-${filter.key || "all"}`}
          />
        ))}
      </nav>

      <nav className={styles.filters} aria-label="指派篩選" data-testid="tasks-filter-assignee">
        {ASSIGNEE_FILTERS.map((filter) => (
          <FilterLink
            key={filter.key || "all"}
            label={filter.label}
            count={filter.key === "me" ? data.facets.assignee.me : undefined}
            href={buildHref(searchParams, { assignee: filter.key || undefined })}
            active={(searchParams.assignee ?? "") === filter.key}
            testId={`filter-assignee-${filter.key || "all"}`}
          />
        ))}
      </nav>

      {data.items.length === 0 ? (
        <ShellState
          kind="empty"
          testId="tasks-empty"
          detail="這個篩選條件下沒有任務。清除篩選即可看到全部項目。"
          actions={<Link href="/tasks">清除篩選</Link>}
        />
      ) : (
        <ul className={styles.list} data-testid="tasks-list">
          {data.items.map((task) => (
            <li key={task.taskId} className={styles.row} data-testid={`task-row-${task.taskId}`}>
              <div className={styles.rowMain}>
                <p className={styles.rowTitle}>
                  <Link href={task.sourceHref} data-testid={`task-link-${task.taskId}`}>
                    {task.title}
                  </Link>
                </p>
                <p className={styles.rowMeta}>
                  {task.taskId} ・ {task.status}
                  {task.owner ? ` ・ ${task.owner}` : ""}
                </p>
                <p className={styles.rowMeta} data-testid={`task-assignee-${task.taskId}`}>
                  {task.assigneeName ? `指派給 ${task.assigneeName}` : "未指派"}
                  {task.slaDueAt ? ` ・ SLA ${formatStamp(task.slaDueAt)}` : ""}
                </p>
              </div>
              <div className={styles.rowActions}>
                <Badge
                  label={SLA_LABEL[task.slaState]}
                  tone={SLA_TONE[task.slaState]}
                  marker="◷"
                  data-testid={`task-sla-${task.taskId}`}
                />
                <Badge
                  label={SEVERITY_LABEL[task.severity]}
                  tone={SEVERITY_TONE[task.severity]}
                  marker="●"
                />
                {canAssign ? (
                  <AssignTaskForm
                    taskId={task.taskId}
                    roleId={data.meta.role?.id ?? null}
                    assignableRoles={data.assignableRoles}
                    currentAssigneeId={task.assigneeId}
                  />
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function FilterLink({
  label,
  count,
  href,
  active,
  testId,
}: {
  label: string;
  count?: number;
  href: string;
  active: boolean;
  testId: string;
}) {
  return (
    <Link
      href={href}
      className={styles.filterLink}
      aria-current={active ? "true" : undefined}
      data-testid={testId}
      data-active={active ? "true" : "false"}
    >
      {label}
      {count === undefined ? null : <span aria-hidden="true">（{count}）</span>}
    </Link>
  );
}

/** Preserve the other filters when toggling one — filters compose. */
function buildHref(current: TaskCenterSearchParams, patch: TaskCenterSearchParams): string {
  const next = { ...current, ...patch };
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(next)) {
    if (value) params.set(key, value);
  }
  const query = params.toString();
  return query ? `/tasks?${query}` : "/tasks";
}
