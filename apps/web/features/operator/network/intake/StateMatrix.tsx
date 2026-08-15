"use client";

import styles from "./intake.module.css";
import type { IntakeTone } from "./intakeTypes";
import { useModalDialogBehavior } from "../useModalDialogBehavior";

export type StateMatrixItem = {
  code: string;
  label: string;
  note: string;
  tone: IntakeTone;
};

export const INTAKE_STAGE_MATRIX: readonly StateMatrixItem[] = [
  { code: "SUBMITTED", label: "已送出", tone: "neutral", note: "送件成立，取得 intake ID 與 correlation ID" },
  {
    code: "CHECKING_IDENTITY",
    label: "識別檢查",
    tone: "neutral",
    note: "canonical URL／來源 ID 命中即導向既有紀錄，不擷取",
  },
  {
    code: "CHECKING_SOURCE_POLICY",
    label: "來源政策",
    tone: "neutral",
    note: "決定 retrieval／assisted entry／fail-closed",
  },
  {
    code: "AWAITING_ASSISTED_ENTRY",
    label: "待人工補錄",
    tone: "info",
    note: "不擷取來源；保留 URL，等待人工輸入",
  },
  { code: "RETRIEVING", label: "擷取中", tone: "info", note: "建立來源快照；逾時／驗證牆轉入可重試失敗" },
  { code: "PARSING", label: "解析中", tone: "info", note: "parser 版本記錄於證據；部分失敗標低信心" },
  {
    code: "MATCHING",
    label: "比對中",
    tone: "info",
    note: "來源 ID／canonical URL／地址／坪數／樓層／租金",
  },
  {
    code: "NEEDS_REVIEW",
    label: "需人工覆核",
    tone: "watch",
    note: "POSSIBLE_MATCH 或識別矛盾；不得自動合併",
  },
  { code: "READY", label: "就緒", tone: "good", note: "等待人工確認結果" },
  { code: "QUARANTINED", label: "已隔離", tone: "risk", note: "受控可重開；治理判定後可 release" },
  { code: "FAILED", label: "失敗", tone: "risk", note: "區分可重試／不可重試；重試耗盡轉入 DLQ" },
  { code: "CANCELLED", label: "已取消", tone: "neutral", note: "terminal；送件人撤回" },
] as const;

export const SOURCE_POLICY_MATRIX: readonly StateMatrixItem[] = [
  { code: "APPROVED_RETRIEVAL", label: "可擷取", tone: "good", note: "顯示政策版本與效期" },
  { code: "ASSISTED_ENTRY_ONLY", label: "僅人工補錄", tone: "watch", note: "不 fetch；保留 URL" },
  { code: "AUTH_REQUIRED", label: "需核准存取", tone: "watch", note: "不在 UI 索取帳密、cookie 或 token" },
  { code: "SOURCE_BLOCKED", label: "來源封鎖", tone: "risk", note: "顯示治理原因與下一步" },
  { code: "POLICY_UNKNOWN", label: "政策未知", tone: "risk", note: "fail-closed，送交治理覆核" },
] as const;

export const MATCH_OUTCOME_MATRIX: readonly StateMatrixItem[] = [
  { code: "NEW", label: "新物件", tone: "good", note: "建立新物件前顯示即將建立摘要" },
  { code: "EXACT_DUPLICATE", label: "完全重複", tone: "neutral", note: "開啟既有物件；不得再建立" },
  { code: "REVISION", label: "版本更新", tone: "info", note: "preview 變更欄位後 append revision" },
  { code: "POSSIBLE_MATCH", label: "疑似重複", tone: "watch", note: "人工比對決策；絕不自動合併" },
  { code: "QUARANTINED", label: "已隔離", tone: "risk", note: "顯示原因與允許的下一步" },
] as const;

export const INTAKE_ERROR_MATRIX: readonly StateMatrixItem[] = [
  { code: "428 PRECONDITION_REQUIRED", label: "缺版本前提", tone: "watch", note: "要求帶 If-Match 版本後重送" },
  {
    code: "409 VERSION_CONFLICT",
    label: "版本衝突",
    tone: "risk",
    note: "顯示伺服器目前版本；保留操作員輸入",
  },
  {
    code: "409 IDEMPOTENCY_KEY_REUSED",
    label: "重複請求",
    tone: "watch",
    note: "回查原持久化結果，不重複建立",
  },
  { code: "409 OWNER_CONFLICT", label: "擁有權衝突", tone: "watch", note: "顯示目前 owner 與 refresh 動作" },
  { code: "409 REVIEW_CONFLICT", label: "審查衝突", tone: "watch", note: "另一審查已先完成" },
  { code: "409 WORK_INCOMPLETE", label: "前置未完成", tone: "watch", note: "列出尚未完成項目" },
  { code: "409 LEGAL_HOLD_CONFLICT", label: "法務保留", tone: "risk", note: "受 hold 保護，不可變更" },
  { code: "403 SELF_REVIEW_DENIED", label: "不可自審", tone: "risk", note: "promotion 提出者不可核准" },
  { code: "403 SOURCE_POLICY_DENIED", label: "來源政策拒絕", tone: "risk", note: "顯示政策依據" },
  { code: "403 SCOPE_DENIED", label: "範圍拒絕", tone: "risk", note: "唯讀或越權動作" },
  { code: "422 CORRECTION_INVALID", label: "修正無效", tone: "watch", note: "欄位驗證錯誤連結至該列" },
  {
    code: "422 RISK_ACKNOWLEDGEMENT_REQUIRED",
    label: "需風險確認",
    tone: "watch",
    note: "高風險決策必須明確勾選",
  },
  { code: "FETCH-429-CHALLENGE", label: "驗證牆", tone: "risk", note: "可重試；保留操作員輸入" },
  { code: "PARSE-SCHEMA-DRIFT", label: "解析永久失敗", tone: "risk", note: "DLQ → mapping 修正 → replay" },
  { code: "SNAPSHOT-STALE", label: "快照過期", tone: "watch", note: "重放前需重新擷取" },
] as const;

function MatrixGroup({
  heading,
  items,
  testId,
}: {
  heading: string;
  items: readonly StateMatrixItem[];
  testId: string;
}) {
  return (
    <section aria-labelledby={`${testId}-heading`} className={styles.matrixGroup} data-testid={testId}>
      <h3 className={styles.matrixGroupTitle} id={`${testId}-heading`}>
        {heading}（{items.length}）
      </h3>
      <div className={styles.matrixGrid} role="list">
        {items.map((item) => (
          <div className={styles.matrixItem} data-testid={`${testId}-${item.code}`} key={item.code} role="listitem">
            <div className={styles.matrixItemHead}>
              <span className={styles.chip} data-tone={item.tone}>
                {item.tone === "risk" ? "✕" : item.tone === "watch" ? "▲" : item.tone === "good" ? "✓" : "●"}{" "}
                {item.label}
              </span>
              <code className={styles.matrixCode}>{item.code}</code>
            </div>
            <p className={styles.matrixNote}>{item.note}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

export function StateMatrix({ onClose }: { onClose: () => void }) {
  const panelRef = useModalDialogBehavior({ onClose });

  return (
    <div
      className={`${styles.overlay} ${styles.overlayStacked}`}
      data-screen-label="Intake 狀態矩陣"
      data-testid="intake-state-matrix"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        aria-label="Intake Canonical State Matrix"
        aria-modal="true"
        className={styles.matrixDialog}
        ref={panelRef}
        role="dialog"
      >
        <header className={styles.matrixHeader}>
          <div>
            <h2 className={styles.matrixTitle}>Intake Canonical State Matrix</h2>
            <p className={styles.matrixSubtitle}>文字與圖標共同表達狀態；繁中標籤不取代 canonical English code。</p>
          </div>
          <button
            aria-label="關閉 Intake 狀態矩陣"
            className={styles.dialogClose}
            data-autofocus
            data-testid="intake-state-matrix-close"
            onClick={onClose}
            type="button"
          >
            ×
          </button>
        </header>

        <div className={styles.matrixBody}>
          <MatrixGroup heading="INTAKE STAGES" items={INTAKE_STAGE_MATRIX} testId="matrix-stages" />
          <div className={styles.matrixSplit}>
            <MatrixGroup heading="SOURCE POLICY" items={SOURCE_POLICY_MATRIX} testId="matrix-source-policy" />
            <MatrixGroup heading="MATCH OUTCOMES" items={MATCH_OUTCOME_MATRIX} testId="matrix-match-outcomes" />
          </div>
          <MatrixGroup heading="ERROR / CONFLICT CONTRACT" items={INTAKE_ERROR_MATRIX} testId="matrix-errors" />
          <p className={styles.matrixContract}>
            每個錯誤 surface 必須同時呈現摘要、錯誤碼、correlation ID、發生時間與 next action；重試與衝突不得清除操作員輸入。
          </p>
        </div>
      </div>
    </div>
  );
}
