"use client";

import { useState } from "react";
import { Button, StatusBadge } from "./componentPrimitives";
import {
  classifyLoadFailure,
  unavailableDataMessage,
  type OperatorDataAvailability,
  type OperatorLoadFailure,
} from "./operatorDataMode";
import styles from "./operator.module.css";
import { ADMIN_PATH, LOGIN_PATH } from "./operatorSession";

function reloginHref(): string {
  if (typeof window === "undefined") return LOGIN_PATH;
  const returnTo = `${window.location.pathname}${window.location.search}`;
  return `${LOGIN_PATH}?returnTo=${encodeURIComponent(returnTo)}`;
}

export function OperatorDataUnavailableGate({
  detail,
  failure,
  loadingDetail,
  onRetry,
  status,
}: {
  /** Technical detail (exception text, blocked source); never the headline. */
  detail?: string | null;
  failure?: OperatorLoadFailure | null;
  /** Replaces the generic loading explanation, e.g. while the API cold-starts. */
  loadingDetail?: string;
  onRetry?: () => void;
  status: Exclude<OperatorDataAvailability, "ready" | "fixture">;
}) {
  const technicalDetail = failure?.technicalDetail ?? detail ?? null;
  const failureKind = failure?.kind ?? (technicalDetail ? classifyLoadFailure(technicalDetail) : undefined);
  const message = unavailableDataMessage(status, failureKind);
  const isLoading = status === "loading";

  const diagnostics = JSON.stringify({
    error_code: message.code,
    correlation_id: failure?.correlationId,
    http_status: failure?.httpStatus,
    occurred_at: failure?.occurredAt,
  }, null, 2);
  const [copyResult, setCopyResult] = useState<{ diagnostics: string; copied: boolean } | null>(null);

  async function copyDiagnostics() {
    try {
      await navigator.clipboard.writeText(diagnostics);
      setCopyResult({ diagnostics, copied: true });
    } catch {
      setCopyResult({ diagnostics, copied: false });
    }
  }

  return (
    <section
      aria-live={isLoading ? "polite" : "assertive"}
      className={styles.dataUnavailableGate}
      data-failure-kind={isLoading ? undefined : failureKind}
      data-status={status}
      data-testid="operator-data-unavailable"
      role={isLoading ? "status" : "alert"}
    >
      <div className={styles.dataUnavailableBody}>
        <StatusBadge tone={isLoading ? "info" : "warning"}>{message.badge}</StatusBadge>
        <h1>{message.title}</h1>
        <p data-testid="operator-data-unavailable-detail">
          {isLoading && loadingDetail ? loadingDetail : message.detail}
        </p>
        {isLoading ? null : <p className={styles.dataUnavailableNext}>{message.next}</p>}
        {isLoading ? null : (
          <details className={styles.dataUnavailableTechnical} data-testid="operator-data-unavailable-technical">
            <summary>技術資訊</summary>
            <dl>
              <dt>錯誤代碼</dt>
              <dd>{message.code}</dd>
              {failure?.correlationId ? (
                <>
                  <dt>追蹤編號</dt>
                  <dd data-testid="operator-data-unavailable-correlation">{failure.correlationId}</dd>
                </>
              ) : null}
              {failure?.httpStatus ? (
                <>
                  <dt>HTTP 狀態</dt>
                  <dd>{failure.httpStatus}</dd>
                </>
              ) : null}
              {failure?.occurredAt ? (
                <>
                  <dt>發生時間</dt>
                  <dd>{failure.occurredAt}</dd>
                </>
              ) : null}
              {technicalDetail ? (
                <>
                  <dt>原始訊息</dt>
                  <dd>{technicalDetail}</dd>
                </>
              ) : null}
            </dl>
            <div className={styles.dataUnavailableActions}>
              <Button onClick={() => void copyDiagnostics()} size="sm" variant="secondary">
                複製診斷資訊
              </Button>
              {copyResult?.diagnostics === diagnostics ? (
                <p role="status">
                  {copyResult.copied ? "已複製診斷資訊" : "無法自動複製，請選取上方診斷欄位。"}
                </p>
              ) : null}
            </div>
          </details>
        )}
      </div>
      <div className={styles.dataUnavailableActions}>
        {failureKind === "unauthenticated" && !isLoading ? (
          <a className={styles.dataUnavailableLink} data-testid="operator-data-unavailable-login-link" href={reloginHref()}>
            重新登入
          </a>
        ) : null}
        {failureKind === "forbidden" && !isLoading ? (
          <a className={styles.dataUnavailableLink} data-testid="operator-data-unavailable-admin-link" href={ADMIN_PATH}>
            前往管理後台
          </a>
        ) : null}
        {onRetry && !isLoading ? (
          <Button onClick={onRetry} size="sm" variant="secondary">
            重新載入
          </Button>
        ) : null}
      </div>
    </section>
  );
}
