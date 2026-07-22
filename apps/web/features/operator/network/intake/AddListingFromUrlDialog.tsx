"use client";

import React, { useMemo, useRef, useState } from "react";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import type { IntakeApiError } from "./intakeClient";

// "Dialog 從網址新增物件" (UX-SCR-EXP-003A).
//
// Owned layer  : URL submission form + client-side URL validation and the
//                double-submit guard.
// Not changing : source policy detection — the SERVER decides the policy and
//                whether a page may be retrieved. The client deliberately does
//                not pre-judge it (the archived demo simulated detection in the
//                browser; doing that here would let the UI claim a retrieval
//                permission the backend has not granted).

const HEAT_ZONE_OPTIONS = [
  { id: "", label: "未指定" },
  { id: "HZ-01", label: "HZ-01 信義松仁生活圈" },
  { id: "HZ-02", label: "HZ-02 板橋府中商圈" },
  { id: "HZ-03", label: "HZ-03 中壢中原學區" },
  { id: "HZ-04", label: "HZ-04 大安和平住宅圈" },
  { id: "HZ-05", label: "HZ-05 新莊副都心" },
];

const URL_HINT = "請確認網址格式（需為 http(s):// 開頭的完整物件頁網址）。";

export function AddListingFromUrlDialog({
  busy,
  defaultHeatZoneId,
  error,
  onClose,
  onOpenExisting,
  onSubmit,
  submitterLabel,
}: {
  busy: boolean;
  defaultHeatZoneId?: string;
  error: IntakeApiError | null;
  onClose: () => void;
  onOpenExisting?: (intakeId: string) => void;
  onSubmit: (input: { url: string; heatZoneId: string }) => void | Promise<void>;
  submitterLabel: string;
}) {
  const [url, setUrl] = useState("");
  const [heatZoneId, setHeatZoneId] = useState(defaultHeatZoneId ?? "");
  const [localError, setLocalError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
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
    setLocalError(null);
    submitLock.current = true;
    setSubmitting(true);
    try {
      await onSubmit({ url: trimmed, heatZoneId });
    } finally {
      submitLock.current = false;
      setSubmitting(false);
    }
  }

  const shownError = localError ?? error?.summary ?? null;
  const isExactDuplicate = error?.code === "ODP-INTAKE-CONFLICT" || (error?.summary ?? "").includes("已存在");
  const existingIntakeId = isExactDuplicate
    ? /\b(IN-[A-Za-z0-9-]+)\b/.exec(error?.summary ?? "")?.[1] ?? null
    : null;

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
        <button aria-label="關閉" className={styles.dialogClose} onClick={onClose} type="button">
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
            <label className={styles.fieldLabel} htmlFor="intake-area">
              HeatZone／指定區域（選填）
            </label>
            <select
              className={styles.select}
              data-testid="intake-area-select"
              id="intake-area"
              onChange={(event) => setHeatZoneId(event.target.value)}
              value={heatZoneId}
            >
              {HEAT_ZONE_OPTIONS.map((option) => (
                <option key={option.id || "none"} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <span className={styles.fieldLabel}>送件人</span>
            <div className={styles.readOnlyBox} data-testid="intake-submitter">
              {submitterLabel} · 送出後由你擁有此收件紀錄
            </div>
          </div>
        </div>

        <div className={styles.noteBox} data-testid="intake-process-note">
          送出後：識別檢查（相同 URL 直接指向既有紀錄）→ 來源政策判定 →
          已核准來源才擷取解析 → 與既有物件比對。疑似重複不會自動合併；追蹤參數會正規化，
          原始 URL 保留為證據。你可以先離開，稍後從收件佇列回到此紀錄。
          系統僅進行使用者提交之單頁擷取或已核准推送，絕不進行定期爬取或要求提供 credentials。
        </div>

        {isExactDuplicate ? (
          <div className={styles.warnNote} data-testid="intake-exact-duplicate-intercept" role="alert">
            ⚡ 識別檢查攔截：此網址已被收錄。系統已提供短路徑可直接導向既有物件紀錄。
            {existingIntakeId && onOpenExisting ? (
              <button
                className={styles.secondaryButton}
                data-testid="intake-open-existing"
                onClick={() => onOpenExisting(existingIntakeId)}
                type="button"
              >
                開啟既有收件 {existingIntakeId}
              </button>
            ) : null}
          </div>
        ) : null}

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
      </div>

      <div className={styles.dialogFooter}>
        <button className={styles.secondaryButton} onClick={onClose} type="button">
          取消
        </button>
        <button
          className={styles.primaryButton}
          data-testid="intake-submit-button"
          disabled={busy || submitting || !trimmed}
          onClick={() => void handleSubmit()}
          type="button"
        >
          {busy || submitting ? "送出中…（防止重複送出）" : "送出 URL"}
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
