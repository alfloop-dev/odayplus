"use client";

import { useEffect, useState } from "react";
import { PageHeader } from "@oday-plus/ui";
import { ShellNotificationCenter, type ShellNotificationCenterProps } from "./ShellNotificationCenter";
import { ShellSupervisorReport } from "./ShellSupervisorReport";
import { ShellNotificationPreferences } from "./ShellNotificationPreferences";
import styles from "./notificationsPage.module.css";

type Props = Omit<ShellNotificationCenterProps, "fullPage"> & { expectedSubjectId: string | null; preferencesPage?: boolean; reportId?: string };
export function ShellNotificationPage(props: Props) {
  const [preferencesOnly, setPreferencesOnly] = useState(props.preferencesPage === true);
  const [confirmed, setConfirmed] = useState<{ scope: string; unread: number } | null>(null);
  useEffect(() => {
    const restore = () => setPreferencesOnly(window.location.pathname === "/settings/notifications");
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  if (props.reportId !== undefined) return <section className={styles.page} id="canonical-notifications-page" tabIndex={-1}>
    <ShellSupervisorReport reportId={props.reportId} scopeKey={props.scopeKey} headers={props.headers} createAuthenticatedReadGuard={props.createAuthenticatedReadGuard} />
  </section>;
  return <section id="canonical-notifications-page" tabIndex={-1} className={styles.page} data-testid="canonical-notifications-page">
    <PageHeader title={preferencesOnly ? "通知偏好" : "通知"}
      summary={preferencesOnly ? "個人設定由目前登入帳號的後端紀錄提供。" : "通知是來源入口；事件狀態與核准仍以原工作流程為準。"}
      actions={preferencesOnly ? <a href="/notifications">返回通知中心</a> : <a href="/settings/notifications">個人通知偏好</a>} />
    {preferencesOnly ? <ShellNotificationPreferences scopeKey={props.scopeKey} expectedSubjectId={props.expectedSubjectId}
      headers={props.headers} createAuthenticatedReadGuard={props.createAuthenticatedReadGuard} allowFixtureData={props.allowFixtureFallback} />
      : <>
        <p aria-live="polite">未讀通知：{confirmed?.scope === props.scopeKey ? confirmed.unread : "尚未核對"}</p>
        <ShellNotificationCenter {...props} fullPage onConfirmedUnreadCount={(count) => {
          setConfirmed({ scope: props.scopeKey, unread: count }); props.onConfirmedUnreadCount(count);
        }} />
      </>}
  </section>;
}
