"use client";

import type { AssistedIntake } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import {
  matchLabel,
  matchTone,
  queueCounts,
  rowActionLabel,
  shortUrl,
  stageLabel,
  stageTone,
} from "./intakeTypes";

// "Network URL 收件佇列" (Package 7). Renders the durable intake queue that
// lives inside the Listing Radar tab, directly under the compliance banner.
//
// Owned layer  : queue presentation + the entry point to 從網址新增物件.
// Not changing : the radar's own 物件收件匣 list below it — that is a separate
//                surface with its own source filters.

export function AssistedIntakeQueuePanel({
  records,
  loadState,
  selectedIntakeId,
  canSubmit,
  onAdd,
  onOpen,
  errorSummary,
  onRetryLoad,
}: {
  records: AssistedIntake[];
  loadState: "loading" | "ready" | "error";
  selectedIntakeId?: string | null;
  canSubmit: boolean;
  onAdd: () => void;
  onOpen: (intakeId: string) => void;
  errorSummary?: string | null;
  onRetryLoad?: () => void;
}) {
  const counts = queueCounts(records);
  // Cap at 8 rows (design §3.3). The count strip above always reflects the
  // full set, so a truncated list can never read as "that's everything".
  const visible = records.slice(0, 8);
  const hiddenCount = records.length - visible.length;

  return (
    <section
      aria-label="URL 收件佇列"
      className={styles.queue}
      data-screen-label="Network URL 收件佇列"
      data-testid="intake-queue"
    >
      <div className={styles.queueHeader}>
        <button
          className={styles.addButton}
          data-testid="intake-add-button"
          disabled={!canSubmit}
          onClick={onAdd}
          type="button"
        >
          ＋ 從網址新增物件
        </button>
        <span className={styles.queueHint}>
          人工發現的物件貼上網址 — 系統判定新件／重複／版本更新，疑似重複一律由人工決策
        </span>
        <div aria-label="收件佇列統計" className={styles.counts} data-testid="intake-counts">
          <CountItem
            label="需覆核"
            testId="intake-count-needs-review"
            tone={counts.needsReview > 0 ? "#96610b" : "#98a1b3"}
            value={counts.needsReview}
          />
          <CountItem
            label="待補錄"
            testId="intake-count-awaiting"
            tone={counts.awaitingEntry > 0 ? "#2e3a97" : "#98a1b3"}
            value={counts.awaitingEntry}
          />
          <CountItem
            label="處理中"
            testId="intake-count-processing"
            tone="#5a6472"
            value={counts.processing}
          />
          <CountItem
            label="隔離／失敗"
            testId="intake-count-blocked"
            tone={counts.blocked > 0 ? "#b3261e" : "#98a1b3"}
            value={counts.blocked}
          />
        </div>
      </div>

      {loadState === "loading" ? (
        <div className={styles.loadingState} data-testid="intake-queue-loading" role="status">
          載入收件佇列中…
        </div>
      ) : null}

      {loadState === "error" ? (
        <div className={styles.errorPanel} data-testid="intake-queue-error" role="alert">
          <span className={styles.errorSummary}>{errorSummary ?? "無法載入收件佇列。"}</span>
          <span className={styles.errorNext}>
            下一步：請重試載入；此畫面不會以模擬資料代替真實收件紀錄。
          </span>
          {onRetryLoad ? (
            <div>
              <button
                className={styles.secondaryButton}
                data-testid="intake-queue-retry"
                onClick={onRetryLoad}
                type="button"
              >
                重新載入
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {loadState === "ready" && visible.length === 0 ? (
        <div className={styles.emptyState} data-testid="intake-queue-empty">
          尚無 URL 收件紀錄 — 按「＋ 從網址新增物件」貼上物件頁網址即可開始。
        </div>
      ) : null}

      {loadState === "ready" && visible.length > 0 ? (
        <div className={styles.rows} data-testid="intake-queue-rows">
          {visible.map((record) => (
            <QueueRow
              key={record.id}
              onOpen={onOpen}
              record={record}
              selected={record.id === selectedIntakeId}
            />
          ))}
          {hiddenCount > 0 ? (
            <div className={styles.emptyState} data-testid="intake-queue-truncated">
              另有 {hiddenCount} 筆收件未顯示（佇列僅顯示最新 8 筆）。
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function CountItem({
  label,
  testId,
  tone,
  value,
}: {
  label: string;
  testId: string;
  tone: string;
  value: number;
}) {
  return (
    <div className={styles.countItem}>
      <span className={styles.countValue} data-testid={testId} style={{ color: tone }}>
        {value}
      </span>
      <span className={styles.countLabel}>{label}</span>
    </div>
  );
}

function QueueRow({
  onOpen,
  record,
  selected,
}: {
  onOpen: (intakeId: string) => void;
  record: AssistedIntake;
  selected: boolean;
}) {
  const outcome = record.matchResult?.outcome;

  return (
    <button
      className={styles.row}
      data-active={selected ? "true" : undefined}
      data-testid={`intake-row-${record.id}`}
      onClick={() => onOpen(record.id)}
      type="button"
    >
      <span className={styles.rowId}>{record.id}</span>
      <span className={styles.srcChip}>{record.sourceId}</span>
      <span className={styles.rowUrl} title={record.canonicalUrl}>
        {shortUrl(record.canonicalUrl)}
      </span>
      <span
        className={styles.chip}
        data-testid={`intake-row-stage-${record.id}`}
        data-tone={stageTone(record.stage)}
      >
        {stageLabel(record.stage)}
      </span>
      {outcome ? (
        <span
          className={styles.chip}
          data-testid={`intake-row-match-${record.id}`}
          data-tone={matchTone(outcome)}
        >
          {matchLabel(outcome)}
        </span>
      ) : null}
      <span className={styles.rowMeta}>
        {record.submitter} · {record.owner}
      </span>
      <span className={styles.rowAction}>{rowActionLabel(record)} →</span>
    </button>
  );
}
