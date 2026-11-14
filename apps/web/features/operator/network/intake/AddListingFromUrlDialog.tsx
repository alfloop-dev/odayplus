"use client";

import React, { useMemo, useRef, useState } from "react";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import type { IntakeApiError } from "./intakeClient";
import type { NativeIntakeScope } from "@oday-plus/openapi-client";

// "Dialog 從網址新增物件" (UX-SCR-EXP-003A).
//
// Owned layer  : URL submission form + client-side URL validation and the
//                double-submit guard.
// Not changing : source policy detection — the SERVER decides the policy and
//                whether a page may be retrieved. The client deliberately does
//                not pre-judge it (the archived demo simulated detection in the
//                browser; doing that here would let the UI claim a retrieval
//                permission the backend has not granted).

export type IntakeHeatZoneOptions = {
  state: "loading" | "ready" | "error";
  options: readonly { id: string; label: string }[];
};

const URL_HINT = "請確認網址格式（需為 http(s):// 開頭的完整物件頁網址）。";

export function AddListingFromUrlDialog({
  busy,
  error,
  onClose,
  onSubmit,
  submitterLabel,
  canonicalScopes,
  locked = false,
  errorContent,
}: {
  busy: boolean;
  error: IntakeApiError | null;
  onClose: () => void;
  onSubmit: (input: { url: string; reason: string; scope: NativeIntakeScope }) => void | Promise<void>;
  submitterLabel: string;
  canonicalScopes: ReadonlyArray<{ label: string; scope: NativeIntakeScope }>;
  locked?: boolean;
  errorContent?: React.ReactNode;
}) {
  const [url, setUrl] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [reason, setReason] = useState("");
  const [scopeIndex, setScopeIndex] = useState("");
  const submitLock = useRef(false);

  const trimmed = url.trim();
  const looksValid = useMemo(() => isHttpUrl(trimmed), [trimmed]);

  const sourceHost = useMemo(() => (looksValid ? detectSourceHost(trimmed) : null), [looksValid, trimmed]);
  const canonicalPreview = useMemo(() => (looksValid ? computeCanonicalUrlPreview(trimmed) : null), [looksValid, trimmed]);

  async function handleSubmit() {
    if (busy || submitLock.current) return;
    if (!trimmed) {
      setLocalError("請輸入物件頁網址");
      return;
    }
    if (!looksValid) {
      setLocalError(URL_HINT);
      return;
    }
    if (reason.trim().length < 3 || !canonicalScopes[Number(scopeIndex)] || scopeIndex === "") {
      setLocalError("請選取正式收件範圍，並填寫至少三字的送件原因。");
      return;
    }
    setLocalError(null);
    submitLock.current = true;
    setSubmitting(true);
    try {
      await onSubmit({ url: trimmed, reason: reason.trim(), scope: canonicalScopes[Number(scopeIndex)].scope });
    } finally {
      submitLock.current = false;
      setSubmitting(false);
    }
  }

  const shownError = localError ?? error?.summary ?? null;

  return (
    <IntakeDialogShell
      ariaLabel="從網址新增物件"
      onClose={onClose}
      dismissible={!busy && !submitting}
      stacked
      screenLabel="Dialog 從網址新增物件"
      testId="intake-add-dialog"
    >
      <div className={styles.dialogHead}>
        <span className={styles.dialogTitle}>從網址新增物件</span>
        <span className={styles.screenBadge}>UX-SCR-EXP-003A</span>
        <button aria-label="關閉" disabled={busy || submitting} className={styles.dialogClose} onClick={onClose} type="button">
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
            disabled={busy || submitting || locked}
            onChange={(event) => setUrl(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void handleSubmit();
            }}
            placeholder="https://www.591.com.tw/rent-detail-XXXXXXXX.html"
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

        <div className={styles.grid2}>
          <div>
            <label className={styles.fieldLabel}>正式收件範圍<select className={styles.select}
              disabled={busy || submitting || locked} value={scopeIndex} onChange={(event) => setScopeIndex(event.target.value)}>
              <option value="">請明確選取範圍</option>{canonicalScopes.map((option, index) => <option key={index} value={index}>{option.label}</option>)}
            </select></label>
          </div>
          <div>
            <span className={styles.fieldLabel}>送件人</span>
            <div className={styles.readOnlyBox} data-testid="intake-submitter">
              {submitterLabel} · 送出後由你擁有此收件紀錄
            </div>
          </div>
        </div>

        <label className={styles.fieldLabel}>送件原因<textarea className={styles.textarea}
          disabled={busy || submitting || locked} maxLength={4000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>

        <div className={styles.noteBox} data-testid="intake-process-note">
          提交回執僅表示已收件，後續處理以重新讀取為準。來源政策由伺服器判定，
          已核准來源才擷取解析並與既有物件比對。疑似重複不會自動合併；追蹤參數會正規化，
          原始 URL 保留為證據。你可以先離開，稍後從收件佇列回到此紀錄。
          系統僅進行使用者提交之單頁擷取或已核准推送，絕不進行定期爬取或要求提供 credentials。
        </div>

        {shownError ? (
          <div className={styles.errorPanel} data-testid="intake-add-error" role="alert">
            <span className={styles.errorSummary}>{shownError}</span>
            {error ? (
              <>
                <span className={styles.errorMeta}>
                  錯誤碼 {error.code}
                  {error.correlationId ? ` · correlation ${error.correlationId}` : ""} · 發生於{" "}
                  {error.occurredAt}
                </span>
                <span className={styles.errorNext}>下一步：{error.nextAction}</span>
              </>
            ) : null}
          </div>
        ) : null}
        {errorContent}
      </div>

      <div className={styles.dialogFooter}>
        <button className={styles.secondaryButton} disabled={busy || submitting} onClick={onClose} type="button">
          取消
        </button>
        <button
          className={styles.primaryButton}
          data-testid="intake-submit-button"
          disabled={busy || submitting || !trimmed}
          onClick={() => void handleSubmit()}
          type="button"
        >
          {busy || submitting ? "送出中…（防止重複送出）" : locked ? "重試同一請求" : "送出 URL"}
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

function computeCanonicalUrlPreview(url: string): string {
  try {
    const parsed = new URL(url);
    const searchParams = new URLSearchParams(parsed.search);
    const trackingKeys = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"];
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
