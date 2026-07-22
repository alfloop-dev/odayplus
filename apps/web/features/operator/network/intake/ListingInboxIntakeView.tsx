"use client";

import React, { useEffect, useMemo, useState } from "react";
import type { AssistedIntake, IntakeInboxPage, IntakeInboxQuery } from "@oday-plus/openapi-client";
import type { OperatorRoleId } from "../../navigation";
import { getOperatorRole } from "../../navigation";
import styles from "./intake.module.css";
import { AddListingFromUrlDialog } from "./AddListingFromUrlDialog";
import {
  matchLabel,
  matchTone,
  rowActionLabel,
  shortUrl,
  stageLabel,
  stageTone,
} from "./intakeTypes";
import { canPerform, canView, isReadOnly, NO_ACCESS_NOTE, READ_ONLY_NOTE } from "./intakePermissions";
import { type IntakeApiError } from "./intakeClient";
import { useIntakeInboxQuery, type SavedViewType } from "./useIntakeInboxQuery";

export function ListingInboxIntakeView({
  activeRoleId,
  records,
  loadState,
  loadError,
  actionError,
  busy,
  selectedHeatZoneId,
  onAddSubmit,
  onOpenDetail,
  onRetryLoad,
  onRetryIntake,
  pageData,
  onQueryChange,
}: {
  activeRoleId: OperatorRoleId;
  records: AssistedIntake[];
  loadState: "loading" | "ready" | "error";
  loadError?: IntakeApiError | null;
  actionError?: IntakeApiError | null;
  busy: boolean;
  selectedHeatZoneId?: string;
  onAddSubmit: (input: { url: string; heatZoneId: string }) => Promise<void>;
  onOpenDetail: (intakeId: string) => void;
  onRetryLoad?: () => void;
  onRetryIntake?: (intakeId: string) => void;
  pageData?: IntakeInboxPage;
  onQueryChange?: (query: IntakeInboxQuery) => void;
}) {
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);

  const role = getOperatorRole(activeRoleId);
  const submitterLabel = `${role.label}`;
  const readOnly = isReadOnly(activeRoleId);
  const permitted = canView(activeRoleId);
  const canSubmit = canPerform("submit", activeRoleId);

  const {
    filters,
    updateFilters,
    resetFilters,
    toggleSort,
  } = useIntakeInboxQuery();
  useEffect(() => {
    onQueryChange?.({ ...filters, selectedHeatZoneId: selectedHeatZoneId || filters.heatZoneId || undefined });
  }, [filters, onQueryChange, selectedHeatZoneId]);
  const paginatedRecords = records;
  const totalRecords = pageData?.total ?? records.length;
  const pageCount = Math.max(1, Math.ceil(totalRecords / filters.pageSize));
  const counts = pageData?.counts ?? { needsReview: 0, awaitingEntry: 0, processing: 0, blocked: 0, ready: 0 };

  const savedViewTabs: { id: SavedViewType; label: string; count: number }[] = useMemo(
    () => [
      { id: "all", label: "全部物件", count: records.length },
      { id: "needsReview", label: "需覆核", count: counts.needsReview },
      { id: "awaitingEntry", label: "待補錄", count: counts.awaitingEntry },
      { id: "processing", label: "處理中", count: counts.processing },
      { id: "blocked", label: "隔離／失敗", count: counts.blocked },
    ],
    [records.length, counts],
  );

  if (!permitted) {
    return (
      <section className={styles.queue} data-screen-label="Listing Inbox 收件匣" data-testid="intake-inbox-view">
        <div className={styles.emptyState} data-testid="intake-no-access" role="status">
          {NO_ACCESS_NOTE}
        </div>
      </section>
    );
  }

  return (
    <section className={styles.queue} data-screen-label="Listing Inbox 收件匣" data-testid="intake-inbox-view">
      {readOnly ? (
        <div className={styles.warnNote} data-testid="intake-read-only" role="status">
          {READ_ONLY_NOTE}
        </div>
      ) : null}

      {/* Header & Primary Actions */}
      <div className={styles.queueHeader}>
        <div className={styles.dialogHead} style={{ borderBottom: "none", padding: 0 }}>
          <span className={styles.dialogTitle}>Listing Inbox 收件匣</span>
          <span className={styles.screenBadge}>UX-SCR-EXP-003</span>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <div aria-label="檢視模式切換" className={styles.chip} data-testid="intake-view-mode-toggle">
            <button
              className={filters.viewMode === "list" ? styles.primaryButton : styles.secondaryButton}
              data-active={filters.viewMode === "list" ? "true" : undefined}
              data-testid="intake-view-mode-list"
              onClick={() => updateFilters({ viewMode: "list" })}
              type="button"
              style={{ padding: "0.25rem 0.5rem", fontSize: "0.8rem" }}
            >
              列表 Mode
            </button>
            <button
              className={filters.viewMode === "map" ? styles.primaryButton : styles.secondaryButton}
              data-active={filters.viewMode === "map" ? "true" : undefined}
              data-testid="intake-view-mode-map"
              onClick={() => updateFilters({ viewMode: "map" })}
              type="button"
              style={{ padding: "0.25rem 0.5rem", fontSize: "0.8rem" }}
            >
              地圖 Mode
            </button>
          </div>

          <button
            className={styles.addButton}
            data-testid="intake-add-button"
            disabled={!canSubmit}
            onClick={() => setIsAddDialogOpen(true)}
            type="button"
          >
            ＋ 從網址新增物件
          </button>
        </div>
      </div>

      {/* Saved View Tabs */}
      <div style={{ display: "flex", gap: "0.5rem", margin: "0.5rem 0", flexWrap: "wrap" }}>
        {savedViewTabs.map((tab) => (
          <button
            key={tab.id}
            className={filters.savedView === tab.id ? styles.primaryButton : styles.secondaryButton}
            data-active={filters.savedView === tab.id ? "true" : undefined}
            data-testid={`intake-tab-${tab.id}`}
            onClick={() => updateFilters({ savedView: tab.id })}
            type="button"
            style={{ fontSize: "0.85rem", padding: "0.3rem 0.75rem" }}
          >
            {tab.label} ({tab.count})
          </button>
        ))}
      </div>

      {/* Filter Bar */}
      <div className={styles.grid2} style={{ margin: "0.5rem 0", gap: "0.5rem" }}>
        <input
          className={styles.input}
          data-testid="intake-search-input"
          onChange={(e) => updateFilters({ search: e.target.value })}
          placeholder="搜尋 URL / ID / 來源 / 送件人…"
          type="text"
          value={filters.search}
        />

        <div style={{ display: "flex", gap: "0.5rem" }}>
          <select
            className={styles.select}
            data-testid="intake-filter-method"
            onChange={(e) => updateFilters({ intakeMethod: e.target.value })}
            value={filters.intakeMethod}
          >
            <option value="">所有來源方式</option>
            <option value="URL">網址單頁 (URL)</option>
            <option value="APPROVED_FEED">核准推送 (Feed)</option>
            <option value="MANUAL">人工補錄 (Manual)</option>
            <option value="CSV">批次匯入 (CSV)</option>
          </select>

          <select
            className={styles.select}
            data-testid="intake-filter-stage"
            onChange={(e) => updateFilters({ intakeStage: e.target.value })}
            value={filters.intakeStage}
          >
            <option value="">所有處理階段</option>
            <option value="NEEDS_REVIEW">需覆核 (NEEDS_REVIEW)</option>
            <option value="AWAITING_ASSISTED_ENTRY">待補錄 (AWAITING_ASSISTED_ENTRY)</option>
            <option value="RETRIEVING">擷取中 (RETRIEVING)</option>
            <option value="PARSING">解析中 (PARSING)</option>
            <option value="MATCHING">比對中 (MATCHING)</option>
            <option value="READY">已準備 (READY)</option>
            <option value="QUARANTINED">已隔離 (QUARANTINED)</option>
            <option value="FAILED">失敗 (FAILED)</option>
          </select>

          <select
            className={styles.select}
            data-testid="intake-filter-outcome"
            onChange={(e) => updateFilters({ matchOutcome: e.target.value })}
            value={filters.matchOutcome}
          >
            <option value="">所有比對結果</option>
            <option value="NEW">新物件 (NEW)</option>
            <option value="EXACT_DUPLICATE">精確重複 (EXACT_DUPLICATE)</option>
            <option value="REVISION">版本更新 (REVISION)</option>
            <option value="POSSIBLE_MATCH">疑似重複 (POSSIBLE_MATCH)</option>
            <option value="QUARANTINED">隔離 (QUARANTINED)</option>
          </select>

          <button
            className={styles.secondaryButton}
            data-testid="intake-filter-reset"
            onClick={resetFilters}
            type="button"
          >
            重置
          </button>
        </div>
      </div>

      {/* States handling */}
      {loadState === "loading" ? (
        <div className={styles.loadingState} data-testid="intake-inbox-loading" role="status">
          載入 Listing Inbox 收件資料中…
        </div>
      ) : null}

      {loadState === "error" ? (
        <div className={styles.errorPanel} data-testid="intake-inbox-error" role="alert">
          <span className={styles.errorSummary}>{loadError?.summary ?? "無法載入 Listing Inbox 收件資料。"}</span>
          <span className={styles.errorNext}>下一步：請重試載入；此畫面不會顯示模擬資料。</span>
          {onRetryLoad ? (
            <button className={styles.secondaryButton} onClick={onRetryLoad} type="button">
              重新載入
            </button>
          ) : null}
        </div>
      ) : null}

      {loadState === "ready" && pageData && pageData.evidenceState !== "complete" ? (
        <div className={pageData?.evidenceState === "degraded" ? styles.errorPanel : styles.warnNote} data-testid={`intake-evidence-${pageData?.evidenceState ?? "partial"}`} role="status">
          {pageData?.evidenceState === "degraded"
            ? "證據降級：部分收件處理失敗；請查看可重試性與 correlation ID。"
            : "證據部分可用：部分收件尚未產生來源快照，畫面不會將缺值視為完整證據。"}
        </div>
      ) : null}

      {loadState === "ready" && filters.viewMode === "map" ? (
        <div className={styles.rows} data-testid="intake-map-view-panel" aria-label="Listing Inbox 地圖結果">
          <div className={styles.noteBox}>🗺️ 依 HeatZone 顯示目前 server page 的收件位置；未定位項目明確列入「待定位」。</div>
          {paginatedRecords.map((record) => (
            <button className={styles.secondaryButton} data-testid={`intake-map-marker-${record.id}`} key={record.id} onClick={() => onOpenDetail(record.id)} type="button">
              {record.heatZoneId ?? "待定位"} · {record.id} · {shortUrl(record.canonicalUrl)}
            </button>
          ))}
        </div>
      ) : null}

      {/* Data Table */}
      {loadState === "ready" && filters.viewMode === "list" ? (
        paginatedRecords.length === 0 ? (
          <div className={styles.emptyState} data-testid="intake-inbox-empty">
            目前無符合條件的收件紀錄。
          </div>
        ) : (
          <div className={styles.rows} data-testid="intake-table">
            {/* Table Header */}
            <div
              className={styles.row}
              style={{ fontWeight: 600, background: "#f1f4f9", borderBottom: "1px solid #d0d7de" }}
            >
              <button
                className={styles.secondaryButton}
                onClick={() => toggleSort("id")}
                type="button"
                style={{ padding: 0, border: "none", background: "none" }}
              >
                收件 ID {filters.sortBy === "id" ? (filters.sortOrder === "asc" ? "▲" : "▼") : ""}
              </button>
              <button
                className={styles.secondaryButton}
                onClick={() => toggleSort("sourceId")}
                type="button"
                style={{ padding: 0, border: "none", background: "none" }}
              >
                來源 {filters.sortBy === "sourceId" ? (filters.sortOrder === "asc" ? "▲" : "▼") : ""}
              </button>
              <span className={styles.rowUrl}>物件頁 URL</span>
              <button
                className={styles.secondaryButton}
                onClick={() => toggleSort("stage")}
                type="button"
                style={{ padding: 0, border: "none", background: "none" }}
              >
                處理階段 {filters.sortBy === "stage" ? (filters.sortOrder === "asc" ? "▲" : "▼") : ""}
              </button>
              <span>比對結果</span>
              <span>送件人 / 擁有者</span>
              <button
                className={styles.secondaryButton}
                onClick={() => toggleSort("updatedAt")}
                type="button"
                style={{ padding: 0, border: "none", background: "none" }}
              >
                動作 / 更新 {filters.sortBy === "updatedAt" ? (filters.sortOrder === "asc" ? "▲" : "▼") : ""}
              </button>
            </div>

            {/* Table Rows */}
            {paginatedRecords.map((record) => {
              const outcome = record.matchResult?.outcome;
              const isSelected = record.id === filters.selectedIntakeId;

              return (
                <div
                  key={record.id}
                  className={styles.row}
                  data-active={isSelected ? "true" : undefined}
                  data-testid={`intake-inbox-row-${record.id}`}
                  onClick={() => onOpenDetail(record.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") onOpenDetail(record.id);
                  }}
                >
                  <span className={styles.rowId}>{record.id}</span>
                  <span className={styles.srcChip}>{record.sourceId}</span>
                  <span className={styles.rowUrl} title={`原始 URL: ${record.canonicalUrl}`}>
                    {shortUrl(record.canonicalUrl)}
                  </span>
                  <span
                    className={styles.chip}
                    data-testid={`intake-row-stage-${record.id}`}
                    data-tone={stageTone(record.stage)}
                  >
                    {stageLabel(record.stage)}
                  </span>
                  <span>
                    {outcome ? (
                      <span
                        className={styles.chip}
                        data-testid={`intake-row-match-${record.id}`}
                        data-tone={matchTone(outcome)}
                      >
                        {matchLabel(outcome)}
                      </span>
                    ) : (
                      "-"
                    )}
                  </span>
                  <span className={styles.rowMeta}>
                    {record.intakeMethod ?? "URL"} · 送件 {record.submitter} · 指派 {record.owner || "未認領"}<br />
                    SLA {record.slaState ?? "未提供"} · 更新 {record.auditEvents?.at(-1)?.occurredAt ?? record.capturedAt ?? "待處理"}<br />
                    {record.rawSnapshot ? "證據已擷取" : "證據待補"} · {record.stage === "QUARANTINED" ? "已隔離" : record.failure ? (record.failure.retryable ? "可重試" : "不可重試") : "未隔離"}
                  </span>
                  <button
                    className={styles.primaryButton}
                    data-testid={`intake-row-action-${record.id}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (record.stage === "FAILED" && record.failure?.retryable && onRetryIntake) onRetryIntake(record.id);
                      else onOpenDetail(record.id);
                    }}
                    type="button"
                    style={{ fontSize: "0.8rem", padding: "0.2rem 0.5rem" }}
                  >
                    {record.stage === "FAILED" && record.failure?.retryable ? "重試" : record.stage === "NEEDS_REVIEW" ? "覆核" : !record.owner ? "認領" : record.stage === "AWAITING_ASSISTED_ENTRY" ? "要求補正" : rowActionLabel(record)} →
                  </button>
                </div>
              );
            })}
          </div>
        )
      ) : null}

      {/* Pagination Bar */}
      {loadState === "ready" && totalRecords > 0 ? (
        <div
          aria-label="分頁控制"
          className={styles.queueHeader}
          data-testid="intake-pagination"
          style={{ borderTop: "1px solid #e1e4e8", marginTop: "0.5rem", paddingTop: "0.5rem" }}
        >
          <span className={styles.queueHint}>
            共 {totalRecords} 筆收件紀錄（第 {filters.page} / {pageCount} 頁）
          </span>

          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <select
              className={styles.select}
              data-testid="intake-page-size-select"
              onChange={(e) => updateFilters({ pageSize: parseInt(e.target.value, 10), page: 1 })}
              value={filters.pageSize}
            >
              <option value="10">每頁 10 筆</option>
              <option value="20">每頁 20 筆</option>
              <option value="50">每頁 50 筆</option>
            </select>

            <button
              className={styles.secondaryButton}
              data-testid="intake-prev-page"
              disabled={filters.page <= 1}
              onClick={() => updateFilters({ page: filters.page - 1 })}
              type="button"
            >
              上一頁
            </button>

            <button
              className={styles.secondaryButton}
              data-testid="intake-next-page"
              disabled={filters.page >= pageCount}
              onClick={() => updateFilters({ page: filters.page + 1 })}
              type="button"
            >
              下一頁
            </button>
          </div>
        </div>
      ) : null}

      {/* Add Dialog */}
      {isAddDialogOpen ? (
        <AddListingFromUrlDialog
          busy={busy}
          defaultHeatZoneId={selectedHeatZoneId}
          error={actionError ?? null}
          onClose={() => setIsAddDialogOpen(false)}
          onOpenExisting={(intakeId) => {
            setIsAddDialogOpen(false);
            onOpenDetail(intakeId);
          }}
          onSubmit={async (input) => {
            await onAddSubmit(input);
            setIsAddDialogOpen(false);
          }}
          submitterLabel={submitterLabel}
        />
      ) : null}
    </section>
  );
}
