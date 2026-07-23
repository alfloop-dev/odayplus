"use client";

import React, { useMemo, useRef, useState } from "react";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import { existingListingHref, intakeDetailHref } from "./IntakeInboxMap";
import type {
  AuthoritativeInboxError,
  IntakeInboxHeatZone,
} from "./inboxContracts";

// "Dialog 從網址新增物件" (UX-SCR-EXP-003A).
//
// Owned layer  : URL submission form + client-side URL validation and the
//                double-submit guard.
// Not changing : source policy detection — the SERVER decides the policy and
//                whether a page may be retrieved. The client deliberately does
//                not pre-judge it (the archived demo simulated detection in the
//                browser; doing that here would let the UI claim a retrieval
//                permission the backend has not granted).

const URL_HINT = "請確認網址格式（需為 http(s):// 開頭的完整物件頁網址）。";

export function AddListingFromUrlDialog({
  busy,
  defaultHeatZoneId,
  error,
  heatZoneOptions,
  onClose,
  onOpenExisting,
  onSubmit,
  ownerLabel,
  scopeLabel,
  submitterLabel,
  tenantLabel,
}: {
  busy: boolean;
  defaultHeatZoneId?: string;
  error: AuthoritativeInboxError | null;
  heatZoneOptions?: IntakeInboxHeatZone[];
  onClose: () => void;
  onOpenExisting?: (listingId: string) => void;
  onSubmit: (
    input: { url: string; heatZoneId: string },
  ) => AssistedIntake | void | Promise<AssistedIntake | void>;
  ownerLabel: string;
  scopeLabel: string;
  submitterLabel: string;
  tenantLabel: string;
}) {
  const [url, setUrl] = useState("");
  const [heatZoneId, setHeatZoneId] = useState(defaultHeatZoneId ?? "");
  const [localError, setLocalError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<AssistedIntake | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submitLock = useRef(false);

  const trimmed = url.trim();
  const looksValid = useMemo(() => isHttpUrl(trimmed), [trimmed]);

  const sourceHost = useMemo(() => (looksValid ? detectSourceHost(trimmed) : null), [looksValid, trimmed]);
  const canonicalPreview = useMemo(() => (looksValid ? computeCanonicalUrlPreview(trimmed) : null), [looksValid, trimmed]);
  const heatZoneContextAvailable = heatZoneOptions !== undefined;

  async function handleSubmit() {
    if (busy || submitLock.current) return;
    if (!heatZoneContextAvailable) {
      setLocalError("HeatZone 與送件 context 尚未由伺服器提供，暫時無法送件。");
      return;
    }
    if (!trimmed) {
      setLocalError("請輸入物件頁網址");
      return;
    }
    if (!looksValid) {
      setLocalError(URL_HINT);
      return;
    }
    setLocalError(null);
    setReceipt(null);
    submitLock.current = true;
    setSubmitting(true);
    try {
      const result = await onSubmit({ url: trimmed, heatZoneId });
      if (result) setReceipt(result);
    } finally {
      submitLock.current = false;
      setSubmitting(false);
    }
  }

  const shownError = localError ?? error?.summary ?? null;
  const exactDuplicateListingId =
    receipt?.matchResult?.outcome === "EXACT_DUPLICATE"
      ? receipt.matchResult.targetListingId
      : null;
  const isProcessingConflict =
    error?.code === "ODP-INTAKE-CONFLICT" ||
    (error?.summary ?? "").includes("already being processed");

  return (
    <IntakeDialogShell
      ariaLabel="從網址新增物件"
      onClose={onClose}
      screenLabel="Dialog 從網址新增物件"
      testId="intake-add-dialog"
    >
      <div className={styles.dialogHead}>
        <span className={styles.dialogTitle}>從網址新增物件</span>
        <span className={styles.screenBadge}>UX-SCR-EXP-003A</span>
        <button
          aria-label="關閉"
          className={styles.dialogClose}
          disabled={busy || submitting}
          onClick={onClose}
          type="button"
        >
          ×
        </button>
      </div>

      <div className={styles.dialogBody}>
        <div>
          <label className={styles.fieldLabel} htmlFor="intake-url">
            物件頁網址
          </label>
          <input
            className={`${styles.input} ${styles.mono}`}
            data-autofocus
            data-testid="intake-url-input"
            id="intake-url"
            onChange={(event) => {
              setUrl(event.target.value);
              setReceipt(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") void handleSubmit();
            }}
            placeholder="https://listings.example.com/property/12345"
            type="url"
            value={url}
          />
        </div>

        {looksValid ? (
          <div className={styles.metaRow} data-testid="intake-source-preview">
            <span className={styles.metaLabel}>辨識來源：</span>
            <span className={styles.chip} data-tone="info">{sourceHost}</span>
            <span className={styles.metaLabel}>來源政策：</span>
            <span className={styles.chip} data-tone="info">送出後由伺服器判定</span>
            <span className={styles.metaValue}> — 瀏覽器不會推定來源已核准或允許擷取。</span>
          </div>
        ) : null}

        {canonicalPreview && canonicalPreview !== trimmed ? (
          <div className={styles.metaRow} data-testid="intake-canonical-preview">
            <span className={styles.metaLabel}>正規化 URL 預覽：</span>
            <span className={`${styles.rowUrl} ${styles.mono}`} title={canonicalPreview}>
              {canonicalPreview}
            </span>
          </div>
        ) : null}

        {looksValid ? (
          <dl className={styles.metaGrid} data-testid="intake-url-evidence-preview">
            <div>
              <dt>原始 URL</dt>
              <dd className={styles.mono}>{trimmed}</dd>
            </div>
            <div>
              <dt>Canonical URL 預覽</dt>
              <dd className={styles.mono}>{canonicalPreview ?? trimmed}</dd>
            </div>
          </dl>
        ) : null}

        <div className={styles.grid2}>
          <div>
            <label className={styles.fieldLabel} htmlFor="intake-area">
              HeatZone／指定區域（選填）
            </label>
            <select
              className={styles.select}
              data-testid="intake-area-select"
              disabled={!heatZoneContextAvailable}
              id="intake-area"
              onChange={(event) => setHeatZoneId(event.target.value)}
              value={heatZoneId}
            >
              <option value="">未指定</option>
              {(heatZoneOptions ?? []).map((option) => (
                <option key={option.id || "none"} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
            {!heatZoneContextAvailable ? (
              <span
                className={styles.fieldHint}
                data-testid="intake-heatzone-unavailable"
                role="status"
              >
                HeatZone 清單尚未由 authoritative bootstrap 提供。
              </span>
            ) : null}
          </div>
          <div>
            <span className={styles.fieldLabel}>送件與權責 context</span>
            <div className={styles.readOnlyBox} data-testid="intake-submitter">
              送件人 {submitterLabel}
              <br />
              Tenant {tenantLabel} · Scope {scopeLabel}
              <br />
              初始 owner {ownerLabel}
            </div>
          </div>
        </div>

        <div className={styles.noteBox} data-testid="intake-process-note">
          送出後：識別檢查（相同 URL 直接指向既有紀錄）→ 來源政策判定 →
          已核准來源才擷取解析 → 與既有物件比對。疑似重複不會自動合併；追蹤參數會正規化，
          原始 URL 保留為證據。你可以先離開，稍後從收件佇列回到此紀錄。
          系統僅進行使用者提交之單頁擷取或已核准推送，絕不進行定期爬取或要求提供 credentials。
        </div>

        {exactDuplicateListingId ? (
          <div className={styles.warnNote} data-testid="intake-exact-duplicate-intercept" role="alert">
            識別檢查攔截：此網址對應既有 Listing {exactDuplicateListingId}，未啟動新的 retrieval。
            {onOpenExisting ? (
              <button
                className={styles.secondaryButton}
                data-testid="intake-open-existing"
                onClick={() => onOpenExisting(exactDuplicateListingId)}
                type="button"
              >
                開啟既有 Listing {exactDuplicateListingId}
              </button>
            ) : (
              <a
                className={styles.secondaryButton}
                data-testid="intake-open-existing"
                href={existingListingHref(exactDuplicateListingId)}
              >
                開啟既有 Listing {exactDuplicateListingId}
              </a>
            )}
          </div>
        ) : null}

        {receipt ? (
          <section
            aria-label="URL 送件收據"
            className={styles.intakeActionReceipt}
            data-testid="intake-submission-receipt"
          >
            <strong>伺服器已接受送件</strong>
            <span>
              Intake {receipt.id} · version {receipt.version} · {receipt.stage}
            </span>
            <span>
              Source {receipt.sourceId} · Policy {receipt.policy}
            </span>
            <span>{receipt.policyReason}</span>
            <span>
              Correlation {receipt.correlationId ?? "伺服器未提供"}
              {receipt.auditEvents?.[0]?.occurredAt
                ? ` · submitted ${receipt.auditEvents[0].occurredAt}`
                : ""}
            </span>
            <span className={styles.mono}>Original {receipt.originalUrl}</span>
            <span className={styles.mono}>Canonical {receipt.canonicalUrl}</span>
            {!exactDuplicateListingId ? (
              <a
                className={styles.primaryButton}
                data-testid="intake-open-created"
                href={intakeDetailHref(receipt.id)}
              >
                開啟收件 {receipt.id}
              </a>
            ) : null}
          </section>
        ) : null}

        {isProcessingConflict ? (
          <div className={styles.warnNote} data-testid="intake-processing-conflict" role="alert">
            此 URL 已在處理中。請依錯誤資訊重新整理既有收件；系統不會將 Intake ID
            誤當成既有 Listing ID。
          </div>
        ) : null}

        {shownError ? (
          <div className={styles.errorPanel} data-testid="intake-add-error" role="alert">
            <span className={styles.errorSummary}>{shownError}</span>
            {error ? (
              <>
                <span className={styles.errorMeta}>
                  錯誤碼 {error.code}
                  {" · "}correlation {error.correlationId ?? "伺服器未提供"}
                  {" · "}發生於 {error.occurredAt ?? "伺服器未提供"}
                </span>
                <span className={styles.errorMeta}>
                  retryable {error.retryable ? "true" : "false"}
                  {" · "}currentVersion{" "}
                  {error.currentVersion ?? "伺服器未提供"}
                  {" · "}currentState {error.currentState ?? "伺服器未提供"}
                </span>
                <span className={styles.errorNext}>下一步：{error.nextAction}</span>
              </>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className={styles.dialogFooter}>
        <button
          className={styles.secondaryButton}
          disabled={busy || submitting}
          onClick={onClose}
          type="button"
        >
          取消
        </button>
        <button
          className={styles.primaryButton}
          data-testid="intake-submit-button"
          disabled={
            busy ||
            submitting ||
            !trimmed ||
            Boolean(receipt) ||
            !heatZoneContextAvailable
          }
          onClick={() => void handleSubmit()}
          type="button"
        >
          {busy || submitting
            ? "送出中…（防止重複送出）"
            : receipt
              ? "已收到伺服器收據"
              : "送出 URL"}
        </button>
      </div>
    </IntakeDialogShell>
  );
}

function isHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function detectSourceHost(url: string): string {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "未知來源";
  }
}

export function computeCanonicalUrlPreview(url: string): string {
  try {
    const parsed = new URL(url);
    const searchParams = new URLSearchParams(parsed.search);
    const trackingKeys = [
      "utm_source",
      "utm_medium",
      "utm_campaign",
      "utm_term",
      "utm_content",
      "utm_id",
      "fbclid",
      "gclid",
      "msclkid",
      "yclid",
      "ref",
      "referrer",
      "from",
      "source",
      "share_from",
      "tracking_id",
      "_gl",
    ];
    let changed = false;
    for (const key of trackingKeys) {
      if (searchParams.has(key)) {
        searchParams.delete(key);
        changed = true;
      }
    }
    if (changed) {
      parsed.search = searchParams.toString();
      return parsed.toString();
    }
    return url;
  } catch {
    return url;
  }
}
