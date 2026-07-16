/**
 * Home — the aggregated first screen (ODP-PGAP-SHELL-001, acceptance §1).
 *
 * Replaces the R0 placeholder grid of static links. Every region is served by
 * GET /api/v1/operator/shell/home: status, tasks, approvals, decisions, data
 * freshness, and the entry points the acting role may actually reach. Nothing
 * here is invented client-side — the role filter and the admin gate are the
 * server's answer, not a local guess.
 */
import Link from "next/link";
import { Badge, PageHeader } from "@oday-plus/ui";
import type { ShellHomeResponse } from "@oday-plus/openapi-client";
import type { ApiResource } from "./resource.ts";
import { ShellDataSource, ShellResourceState } from "./ShellStates.tsx";
import { SEVERITY_TONE, SLA_LABEL, SLA_TONE } from "./vocabulary.ts";
import styles from "./shell.module.css";

export function HomeWorkspace({ home }: { home: ApiResource<ShellHomeResponse> }) {
  const data = home.data;

  return (
    <>
      <PageHeader
        title="OpsBoard 總覽"
        summary="跨模組狀態、待辦、核准與最近決策的彙整，依你的角色權限呈現。"
        status={
          data
            ? { label: data.meta.role?.label ?? "Operator", tone: "blue", marker: "•" }
            : { label: "無法取得", tone: "red", marker: "•" }
        }
        breadcrumb={[{ label: "總覽" }]}
        lastUpdated={data ? formatStamp(data.meta.generatedAt) : "—"}
      />
      <div className="odp-content" data-testid="shell-home">
        {data ? <HomeBody data={data} home={home} /> : <ShellResourceState resource={home} testId="home-state" />}
      </div>
    </>
  );
}

function HomeBody({
  data,
  home,
}: {
  data: ShellHomeResponse;
  home: ApiResource<ShellHomeResponse>;
}) {
  return (
    <>
      <div className={styles.statusRow}>
        <h2 className={styles.statusHeadline} data-testid="home-status-headline">
          {data.status.headline}
        </h2>
        <Badge
          label={statusLabel(data.status.tone)}
          tone={data.status.tone === "danger" ? "red" : data.status.tone === "warning" ? "orange" : "green"}
          marker="●"
        />
        <ShellDataSource resource={home} endpoint="/operator/shell/home" testId="home-data-source" />
      </div>

      <ul className={styles.metrics} data-testid="home-metrics">
        <Metric label="待處理任務" value={data.status.openTasks} testId="metric-open-tasks" />
        <Metric label="SLA 已逾期" value={data.status.slaBreached} testId="metric-sla-breached" />
        <Metric label="SLA 即將到期" value={data.status.slaAtRisk} testId="metric-sla-at-risk" />
        <Metric label="待核准" value={data.status.pendingApprovals} testId="metric-approvals" />
        <Metric
          label="未讀通知"
          value={data.status.unacknowledgedNotifications}
          testId="metric-notifications"
        />
      </ul>

      <section className={styles.section} data-testid="home-entry-points">
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle}>你的工作區</h2>
          <span className={styles.rowMeta}>依角色授權顯示；未授權的工作區不會出現。</span>
        </div>
        <ul className={styles.grid}>
          {data.entryPoints.map((entry) => (
            <li key={entry.key}>
              <Link
                href={entry.href}
                className="odp-card"
                data-testid={`home-entry-${entry.key}`}
                style={{ display: "block", textDecoration: "none", color: "inherit" }}
              >
                <h3 className="odp-card__title">{entry.label}</h3>
                <p className="odp-muted" style={{ margin: 0 }}>
                  {entry.description}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section className={styles.section} data-testid="home-tasks">
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle}>待處理任務</h2>
          <Link href="/tasks" data-testid="home-tasks-all">
            前往任務中心
          </Link>
        </div>
        {data.tasks.length === 0 ? (
          <p className="odp-muted" data-testid="home-tasks-empty">
            目前沒有待處理任務。
          </p>
        ) : (
          <ul className={styles.list}>
            {data.tasks.map((task) => (
              <li key={task.taskId} className={styles.row} data-testid={`home-task-${task.taskId}`}>
                <div className={styles.rowMain}>
                  <p className={styles.rowTitle}>{task.title}</p>
                  <p className={styles.rowMeta}>
                    {task.taskId} ・ {task.status}
                    {task.assigneeName ? ` ・ 指派給 ${task.assigneeName}` : " ・ 未指派"}
                  </p>
                </div>
                <div className={styles.rowActions}>
                  <Badge
                    label={SLA_LABEL[task.slaState]}
                    tone={SLA_TONE[task.slaState]}
                    marker="◷"
                  />
                  <Badge label={task.severity} tone={SEVERITY_TONE[task.severity]} marker="●" />
                  <Link href={task.sourceHref}>開啟</Link>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={styles.section} data-testid="home-approvals">
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle}>待核准</h2>
        </div>
        {data.approvals.length === 0 ? (
          <p className="odp-muted" data-testid="home-approvals-empty">
            目前沒有待核准項目。
          </p>
        ) : (
          <ul className={styles.list}>
            {data.approvals.map((item) => (
              <li key={item.id} className={styles.row}>
                <div className={styles.rowMain}>
                  <p className={styles.rowTitle}>{item.title}</p>
                  <p className={styles.rowMeta}>
                    {item.id} ・ {item.status}
                    {item.meta ? ` ・ ${item.meta}` : ""}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={styles.section} data-testid="home-freshness">
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle}>資料新鮮度</h2>
          <span className={styles.rowMeta}>逐一列出來源，畫面不會宣稱比最慢的上游更新。</span>
        </div>
        <ul className={styles.list}>
          {data.freshness.map((row) => (
            <li key={row.source} className={styles.row} data-testid={`home-freshness-${row.source}`}>
              <div className={styles.rowMain}>
                <p className={styles.rowTitle}>{row.label}</p>
                <p className={styles.rowMeta}>
                  {row.source} ・ {row.records} 筆 ・ {formatStamp(row.generatedAt)}
                </p>
              </div>
              <Badge label={row.state} tone="green" marker="◆" />
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

function Metric({ label, value, testId }: { label: string; value: number; testId: string }) {
  return (
    <li className={styles.metric} data-testid={testId}>
      <span className={styles.metricValue} data-testid={`${testId}-value`}>
        {value}
      </span>
      <span className={styles.metricLabel}>{label}</span>
    </li>
  );
}

function statusLabel(tone: string): string {
  if (tone === "danger") return "有 SLA 逾期";
  if (tone === "warning") return "有 SLA 即將到期";
  return "SLA 正常";
}

/** ISO → a stable, locale-independent stamp (no hydration drift). */
export function formatStamp(iso: string): string {
  return iso.replace("T", " ").slice(0, 16) + " UTC";
}
