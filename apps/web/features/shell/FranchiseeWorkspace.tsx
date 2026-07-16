/**
 * Franchisee portal (ODP-PGAP-SHELL-001, acceptance §6).
 *
 * Mobile-first by construction, not by breakpoint: a single narrow column, no
 * operator table, no KPI strip. A franchisee is a store owner on a phone, not
 * an operator at a desk.
 *
 * The payload is already projected server-side onto a franchisee allow-list, so
 * this screen cannot leak operator-only data even if it tried to render it —
 * the fields simply are not there.
 */
import { Badge, PageHeader } from "@oday-plus/ui";
import type { ShellFranchiseeResponse } from "@oday-plus/openapi-client";
import type { ApiResource } from "./resource.ts";
import { ShellDataSource, ShellResourceState } from "./ShellStates.tsx";
import { FranchiseeActions } from "./FranchiseeActions.tsx";
import { SEVERITY_LABEL, SEVERITY_TONE, REPORT_CATEGORY_LABEL } from "./vocabulary.ts";
import { formatStamp } from "./HomeWorkspace.tsx";
import styles from "./shell.module.css";

export function FranchiseeWorkspace({
  view,
}: {
  view: ApiResource<ShellFranchiseeResponse>;
}) {
  const data = view.data;

  return (
    <>
      <PageHeader
        title="加盟主入口"
        summary="你的門市狀態、通知確認與現場回報。"
        status={
          data
            ? { label: data.store.label, tone: "blue", marker: "•" }
            : { label: "無法取得", tone: "red", marker: "•" }
        }
        breadcrumb={[{ label: "加盟主入口" }]}
        lastUpdated={data ? formatStamp(data.meta.generatedAt) : "—"}
      />
      <div className="odp-content" data-testid="shell-franchisee">
        {data ? (
          <div className={styles.franchisee}>
            <div className={styles.statusRow}>
              <ShellDataSource
                resource={view}
                endpoint="/operator/shell/franchisee"
                testId="franchisee-data-source"
              />
            </div>

            <section className={styles.section} data-testid="franchisee-notifications">
              <div className={styles.sectionHead}>
                <h2 className={styles.sectionTitle}>通知</h2>
              </div>
              {data.notifications.length === 0 ? (
                <p className="odp-muted" data-testid="franchisee-notifications-empty">
                  目前沒有需要你確認的通知。
                </p>
              ) : (
                <ul className={styles.list}>
                  {data.notifications.map((item) => (
                    <li
                      key={item.notificationId}
                      className={styles.card}
                      data-testid={`franchisee-notification-${item.notificationId}`}
                      data-acknowledged={item.acknowledged ? "true" : "false"}
                    >
                      <p className={styles.rowTitle}>
                        {item.title}{" "}
                        <Badge
                          label={SEVERITY_LABEL[item.severity]}
                          tone={SEVERITY_TONE[item.severity]}
                          marker="●"
                        />
                      </p>
                      <p className={styles.rowMeta}>{item.detail}</p>
                      <FranchiseeActions
                        mode="acknowledge"
                        notificationId={item.notificationId}
                        acknowledged={item.acknowledged}
                        storeId={data.store.id}
                        categories={data.reportCategories}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className={styles.section} data-testid="franchisee-tasks">
              <div className={styles.sectionHead}>
                <h2 className={styles.sectionTitle}>門市待辦</h2>
              </div>
              {data.tasks.length === 0 ? (
                <p className="odp-muted" data-testid="franchisee-tasks-empty">
                  目前沒有待辦事項。
                </p>
              ) : (
                <ul className={styles.list}>
                  {data.tasks.map((task) => (
                    <li
                      key={task.id}
                      className={styles.card}
                      data-testid={`franchisee-task-${task.id}`}
                    >
                      <p className={styles.rowTitle}>{task.title}</p>
                      <p className={styles.rowMeta}>
                        {task.id} ・ {task.status}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className={styles.section} data-testid="franchisee-report">
              <div className={styles.sectionHead}>
                <h2 className={styles.sectionTitle}>現場回報</h2>
              </div>
              <FranchiseeActions
                mode="report"
                storeId={data.store.id}
                categories={data.reportCategories}
              />
              {data.reports.length > 0 ? (
                <ul className={styles.list} data-testid="franchisee-reports">
                  {data.reports.map((report) => (
                    <li
                      key={report.reportId}
                      className={styles.card}
                      data-testid={`franchisee-report-${report.reportId}`}
                    >
                      <p className={styles.rowTitle}>
                        {REPORT_CATEGORY_LABEL[report.category] ?? report.category}
                      </p>
                      <p className={styles.rowMeta}>{report.message}</p>
                      <p className={styles.rowMeta}>
                        {formatStamp(report.createdAt)} ・ 狀態：{report.status}
                      </p>
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
          </div>
        ) : (
          <ShellResourceState resource={view} testId="franchisee-state" />
        )}
      </div>
    </>
  );
}
