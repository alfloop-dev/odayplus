"use client";

import { useState } from "react";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import type { IntakeApiError } from "./intakeClient";
import { decisionTitle, matchLabel, shortUrl, stageLabel, type IntakeDecisionKind } from "./intakeTypes";

// "Dialog 收件決策確認" (UX-SCR-EXP-003D commit step).
//
// Owned layer  : the review-summary-before-confirm gate for high-impact
//                merge/split/promotion decisions.
// Not changing : the decision's durable effects — those are the server's.
//
// Two rules from §8 are load-bearing here and are NOT negotiable:
//   1. A review summary is shown before confirmation (what will be written,
//      against which evidence).
//   2. No optimistic UI. The dialog stays open and busy until the server
//      answers, because a decision that only *looks* applied would leave the
//      operator believing an audit record exists when it does not.

export function IntakeDecisionDialog({
  busy,
  error,
  kind,
  onClose,
  onSubmit,
  record,
}: {
  busy: boolean;
  error: IntakeApiError | null;
  kind: IntakeDecisionKind;
  onClose: () => void;
  onSubmit: (input: { reason: string }) => void;
  record: AssistedIntake;
}) {
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const title = decisionTitle(kind, record);
  const outcome = record.matchResult?.outcome;
  const target = record.matchResult?.targetListingId ?? "";

  function handleSubmit() {
    if (busy) return;
    // decide_intake ALWAYS requires a reason server-side (422 otherwise), so
    // the dialog never treats it as optional regardless of decision kind.
    if (!reason.trim()) {
      setLocalError("此決策必須填寫原因。");
      return;
    }
    setLocalError(null);
    onSubmit({ reason: reason.trim() });
  }

  const summaryRows = buildSummaryRows(record, kind, target);
  const shownError = localError ?? error?.summary ?? null;

  return (
    <IntakeDialogShell
      ariaLabel={`確認決策：${title}`}
      className={styles.panelDecide}
      onClose={onClose}
      screenLabel="Dialog 收件決策確認"
      stacked
      testId="intake-decide-dialog"
    >
      <div className={styles.dialogHead}>
        <span className={styles.dialogTitle} data-testid="intake-decide-title">
          確認決策：{title}
        </span>
        <button aria-label="關閉" className={styles.dialogClose} onClick={onClose} type="button">
          ×
        </button>
      </div>

      <div className={styles.dialogBody}>
        <div className={styles.metaValue} data-testid="intake-decide-sub">
          {record.id} · {outcome ? matchLabel(outcome) : stageLabel(record.stage)} · 信心{" "}
          {formatConfidence(record)}
        </div>

        <div className={styles.sectionBox}>
          <div className={styles.sectionHead}>決策前檢視 REVIEW SUMMARY</div>
          <div data-testid="intake-decide-summary">
            {summaryRows.map((row) => (
              <div className={styles.summaryRow} key={row.key}>
                <span className={styles.summaryKey}>{row.key}</span>
                <span className={styles.summaryValue}>{row.value}</span>
              </div>
            ))}
          </div>
        </div>

        <div>
          <label className={styles.fieldLabel} htmlFor="intake-decide-reason">
            決策原因（必填 — 寫入 Audit 與 Decision Log）
          </label>
          <textarea
            className={styles.textarea}
            data-autofocus
            data-testid="intake-decide-reason"
            id="intake-decide-reason"
            onChange={(event) => setReason(event.target.value)}
            placeholder="例：樓層與提供者 ID 矛盾，實地確認為不同物件…"
            rows={3}
            value={reason}
          />
        </div>

        {shownError ? (
          <div className={styles.errorPanel} data-testid="intake-decide-error" role="alert">
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

        <div className={styles.noteBox} data-testid="intake-decide-note">
          此決策不使用 optimistic UI — 按下確認才會寫入，並記錄操作者、時間、前後值、快照與
          parser 版本。
        </div>
      </div>

      <div className={styles.dialogFooter}>
        <button className={styles.secondaryButton} onClick={onClose} type="button">
          取消
        </button>
        <button
          className={styles.primaryButton}
          data-testid="intake-decide-submit"
          disabled={busy}
          onClick={handleSubmit}
          type="button"
        >
          {busy ? "寫入中…" : `確認${title}`}
        </button>
      </div>
    </IntakeDialogShell>
  );
}

function buildSummaryRows(
  record: AssistedIntake,
  kind: IntakeDecisionKind,
  target: string,
): Array<{ key: string; value: string }> {
  const rows: Array<{ key: string; value: string }> = [
    {
      key: "收件",
      value: `${record.id} · ${record.sourceId} · ${shortUrl(record.canonicalUrl, 40)}`,
    },
    {
      key: "比對結果",
      value: `${record.matchResult ? matchLabel(record.matchResult.outcome) : "—"} · 信心 ${formatConfidence(record)}`,
    },
    {
      key: "證據",
      value: `快照 ${record.snapshotId ?? "—"} · ${record.parserVersion} · ${record.correlationId ?? "—"}`,
    },
  ];

  if (kind === "revise" && target) {
    rows.push({
      key: "寫入",
      value: `${target} 版本 v2：${record.matchResult?.summary ?? "同物件內容變動"}`,
    });
  }
  if (kind === "create") {
    rows.push({
      key: "寫入",
      value: "物件收件匣新增一筆（來源標記：URL 收件）— 不會自動升級為候選點",
    });
  }
  if (kind === "dup") {
    rows.push({
      key: "寫入",
      value: `標記為重複${target ? `（保留 ${target}）` : ""} — 不建立新物件`,
    });
  }
  if (kind === "steward") {
    rows.push({
      key: "寫入",
      value: "停止處理並送交人工品質判定 — 記錄進入隔離狀態，不建立新物件",
    });
  }
  return rows;
}

function formatConfidence(record: AssistedIntake): string {
  if (!record.matchResult) return "—";
  return record.matchResult.confidence.toFixed(2);
}
