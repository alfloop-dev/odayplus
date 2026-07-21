/**
 * Notifications (ODP-PGAP-SHELL-001, acceptance §3).
 *
 * Durable inbox state, severity, acknowledgement, preferences and source links.
 * Acknowledgement is per-role and server-held, so it survives a reload and does
 * not silently apply to a colleague sharing the same notification.
 */
import Link from "next/link";
import { Badge, PageHeader } from "@oday-plus/ui";
import type { ShellNotificationsResponse } from "@oday-plus/openapi-client";
import type { ApiResource } from "./resource.ts";
import { ShellDataSource, ShellResourceState, ShellState } from "./ShellStates.tsx";
import { AcknowledgeButton } from "./AcknowledgeButton.tsx";
import { PreferencesForm } from "./PreferencesForm.tsx";
import { SEVERITY_LABEL, SEVERITY_TONE } from "./vocabulary.ts";
import { formatStamp } from "./HomeWorkspace.tsx";
import styles from "./shell.module.css";

const SEVERITY_FILTERS = [
  { key: "", label: "全部" },
  { key: "critical", label: "嚴重" },
  { key: "warning", label: "警告" },
  { key: "info", label: "資訊" },
];

export type NotificationsSearchParams = { severity?: string; acknowledged?: string };

export function NotificationsWorkspace({
  inbox,
  searchParams,
}: {
  inbox: ApiResource<ShellNotificationsResponse>;
  searchParams: NotificationsSearchParams;
}) {
  const data = inbox.data;

  return (
    <>
      <PageHeader
        title="通知收件匣"
        summary="依嚴重度分級的通知：確認狀態、來源連結與個人通知偏好。"
        status={
          data
            ? {
                label: `${data.unacknowledged} 則未讀`,
                tone: data.unacknowledged > 0 ? "orange" : "green",
                marker: "•",
              }
            : { label: "無法取得", tone: "red", marker: "•" }
        }
        breadcrumb={[{ label: "總覽", href: "/" }, { label: "通知" }]}
        lastUpdated={data ? formatStamp(data.meta.generatedAt) : "—"}
      />
      <div className="odp-content" data-testid="shell-notifications">
        {data ? (
          <NotificationsBody data={data} inbox={inbox} searchParams={searchParams} />
        ) : (
          <ShellResourceState resource={inbox} testId="notifications-state" />
        )}
      </div>
    </>
  );
}

function NotificationsBody({
  data,
  inbox,
  searchParams,
}: {
  data: ShellNotificationsResponse;
  inbox: ApiResource<ShellNotificationsResponse>;
  searchParams: NotificationsSearchParams;
}) {
  return (
    <>
      <div className={styles.statusRow}>
        <ShellDataSource
          resource={inbox}
          endpoint="/operator/shell/notifications"
          testId="notifications-data-source"
        />
      </div>

      <nav className={styles.filters} aria-label="嚴重度篩選" data-testid="notifications-filters">
        {SEVERITY_FILTERS.map((filter) => (
          <Link
            key={filter.key || "all"}
            href={filter.key ? `/notifications?severity=${filter.key}` : "/notifications"}
            className={styles.filterLink}
            aria-current={(searchParams.severity ?? "") === filter.key ? "true" : undefined}
            data-testid={`notifications-filter-${filter.key || "all"}`}
          >
            {filter.label}
            {filter.key ? <span aria-hidden="true">（{data.facets.severity[filter.key] ?? 0}）</span> : null}
          </Link>
        ))}
        <Link
          href="/notifications?acknowledged=false"
          className={styles.filterLink}
          aria-current={searchParams.acknowledged === "false" ? "true" : undefined}
          data-testid="notifications-filter-unacked"
        >
          未確認
        </Link>
      </nav>

      {data.items.length === 0 ? (
        <ShellState
          kind="empty"
          testId="notifications-empty"
          detail="這個篩選條件下沒有通知。"
          actions={<Link href="/notifications">清除篩選</Link>}
        />
      ) : (
        <ul className={styles.list} data-testid="notifications-list">
          {data.items.map((item) => (
            <li
              key={item.notificationId}
              className={styles.row}
              data-testid={`notification-${item.notificationId}`}
              data-acknowledged={item.acknowledged ? "true" : "false"}
              data-severity={item.severity}
            >
              <div className={styles.rowMain}>
                <p className={styles.rowTitle}>{item.title}</p>
                <p className={styles.rowMeta}>{item.detail}</p>
                <p className={styles.rowMeta}>
                  <Link
                    href={item.sourceHref}
                    data-testid={`notification-source-${item.notificationId}`}
                  >
                    查看來源
                  </Link>
                  {item.acknowledged && item.acknowledgedAt
                    ? ` ・ 已於 ${formatStamp(item.acknowledgedAt)} 確認`
                    : ""}
                </p>
              </div>
              <div className={styles.rowActions}>
                <Badge
                  label={SEVERITY_LABEL[item.severity]}
                  tone={SEVERITY_TONE[item.severity]}
                  marker="●"
                />
                {item.acknowledged ? (
                  <Badge
                    label="已確認"
                    tone="green"
                    marker="✓"
                    data-testid={`notification-acked-${item.notificationId}`}
                  />
                ) : (
                  <AcknowledgeButton
                    notificationId={item.notificationId}
                    roleId={data.meta.role?.id ?? null}
                  />
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <section className={styles.section} data-testid="notifications-preferences">
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle}>通知偏好</h2>
        </div>
        <PreferencesForm
          preferences={data.preferences}
          roleId={data.meta.role?.id ?? null}
        />
      </section>
    </>
  );
}
