"use client";

import React, { useEffect, useMemo, useState } from "react";
import type { AssistedIntake, IntakeInboxPage, IntakeInboxQuery } from "@oday-plus/openapi-client";
import type { OperatorRoleId } from "../../navigation";
import { getOperatorRole } from "../../navigation";
import styles from "./intake.module.css";
import { AddListingFromUrlDialog, type IntakeHeatZoneOptions } from "./AddListingFromUrlDialog";
import { StateMatrix } from "./StateMatrix";
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
import { useIntakeInboxQuery, type IntakeInboxFilterState, type SavedViewType } from "./useIntakeInboxQuery";
import { INBOX_FILTER_KEYS, toIntakeInboxQuery } from "./inboxUrlState";

export function ListingInboxIntakeView({
  activeRoleId,
  records,
  loadState,
  loadError,
  actionError,
  busy,
  selectedHeatZoneId,
  heatZoneOptions,
  onAddSubmit,
  onOpenDetail,
  onRetryLoad,
  onRetryIntake,
  pageData,
  onQueryChange,
  queryFilters,
  onFilterChange,
  addDialogRequested = false,
  onAddDialogClosed,
}: {
  activeRoleId: OperatorRoleId;
  records: AssistedIntake[];
  loadState: "loading" | "ready" | "error";
  loadError?: IntakeApiError | null;
  actionError?: IntakeApiError | null;
  busy: boolean;
  selectedHeatZoneId?: string;
  heatZoneOptions?: IntakeHeatZoneOptions;
  onAddSubmit: (input: { url: string; heatZoneId: string }) => Promise<void>;
  onOpenDetail: (intakeId: string) => void;
  onRetryLoad?: () => void;
  onRetryIntake?: (intakeId: string) => void;
  pageData?: IntakeInboxPage;
  onQueryChange?: (query: IntakeInboxQuery) => void;
  queryFilters?: IntakeInboxFilterState;
  onFilterChange?: (next: IntakeInboxFilterState) => void;
  /** URL asked for the add dialog (`dialog=add`, e.g. from Find Areas). */
  addDialogRequested?: boolean;
  onAddDialogClosed?: () => void;
}) {
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [isStateMatrixOpen, setIsStateMatrixOpen] = useState(false);

  const role = getOperatorRole(activeRoleId);
  const submitterLabel = `${role.label}`;
  const readOnly = isReadOnly(activeRoleId);
  const permitted = canView(activeRoleId);
  const canSubmit = canPerform("submit", activeRoleId);
  const canRetry = canPerform("retry", activeRoleId);

  useEffect(() => { setIsStateMatrixOpen(false); }, [activeRoleId]);

  useEffect(() => {
    if (addDialogRequested && canSubmit) setIsAddDialogOpen(true);
  }, [addDialogRequested, canSubmit]);

  const closeAddDialog = () => {
    setIsAddDialogOpen(false);
    onAddDialogClosed?.();
  };

  const {
    filters,
    updateFilters,
    resetFilters,
    toggleSort,
  } = useIntakeInboxQuery(queryFilters && onFilterChange ? { filters: queryFilters, onChange: onFilterChange } : undefined);
  useEffect(() => {
    onQueryChange?.(toIntakeInboxQuery(filters, selectedHeatZoneId));
  }, [filters, onQueryChange, selectedHeatZoneId]);
  const paginatedRecords = records;
  const totalRecords = pageData?.total ?? records.length;
  const pageCount = Math.max(1, Math.ceil(totalRecords / filters.pageSize));
  const counts = pageData?.counts;
  const effectiveHeatZoneId = filters.heatZoneId ?? selectedHeatZoneId ?? "";
  const hasActiveFilters = INBOX_FILTER_KEYS.some((key) =>
    key === "savedView" ? filters.savedView !== "all"
      : key === "heatZoneId" ? Boolean(effectiveHeatZoneId) : filters[key] !== "");

  const savedViewTabs: { id: SavedViewType; label: string; count: number | null }[] = useMemo(
    () => [
      { id: "all", label: "全部物件", count: !hasActiveFilters ? pageData?.total ?? null : null },
      { id: "needsReview", label: "需覆核", count: counts?.needsReview ?? null },
      { id: "awaitingEntry", label: "待補錄", count: counts?.awaitingEntry ?? null },
      { id: "processing", label: "處理中", count: counts?.processing ?? null },
      { id: "blocked", label: "隔離／失敗", count: counts?.blocked ?? null },
    ],
    [hasActiveFilters, pageData?.total, counts],
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

        <div className={styles.inboxPaginationControls}>
          <button
            aria-haspopup="dialog"
            className={styles.secondaryButton}
            data-testid="intake-state-matrix-trigger"
            onClick={() => setIsStateMatrixOpen(true)}
            type="button"
          >
            收件狀態矩陣
          </button>
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

          {canSubmit ? (
            <button
              className={styles.addButton}
              data-testid="intake-add-button"
              onClick={() => setIsAddDialogOpen(true)}
              type="button"
            >
              ＋ 從網址新增物件
            </button>
          ) : null}
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
            {tab.label}{tab.count === null ? "" : ` (${tab.count})`}
          </button>
        ))}
      </div>

      {/* Filter Bar */}
      <div className={styles.inboxFilterBar}>
        <input
          aria-label="搜尋收件 URL、編號、來源或送件人"
          className={[styles.input, styles.inboxFilterSearch].join(" ")}
          data-testid="intake-search-input"
          onChange={(e) => updateFilters({ search: e.target.value })}
          placeholder="搜尋 URL / ID / 來源 / 送件人…"
          type="text"
          value={filters.search}
        />

        <div className={styles.inboxFilterControls}>
          <select
            aria-label="依收件方式篩選"
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
            aria-label="依處理階段篩選"
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
            aria-label="依比對結果篩選"
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

          {([
            ["sourceId", "來源 ID", "intake-filter-source"],
            ["submittedBy", "送件者 ID", "intake-filter-submitted-by"],
            ["ownerId", "負責人 ID", "intake-filter-owner"],
            ["heatZoneId", "HeatZone ID", "intake-filter-heat-zone"],
            ["assignedAreaId", "負責區域 ID", "intake-filter-area"],
          ] as const).map(([key, label, testId]) => (
            <label className={styles.inboxFilterField} key={key}>
              <span>{label}</span>
              <input className={styles.input} data-testid={testId} type="text"
                value={key === "heatZoneId" ? effectiveHeatZoneId : filters[key]} placeholder="不限"
                onChange={(event) => updateFilters({ [key]: event.target.value })} />
            </label>
          ))}

          <label className={styles.inboxFilterField}>
            <span>指派狀態</span>
            <select className={styles.select} data-testid="intake-filter-assignment"
              value={filters.assignmentStatus} onChange={(event) => updateFilters({ assignmentStatus: event.target.value as IntakeInboxFilterState["assignmentStatus"] })}>
              <option value="">不限</option>
              <option value="UNASSIGNED">尚未指派</option>
              <option value="ASSIGNED">已指派</option>
              <option value="CLAIMED">已認領</option>
              <option value="TRANSFERRED">已轉交</option>
              <option value="ESCALATED">已升級處理</option>
              <option value="COMPLETED">已完成</option>
            </select>
          </label>

          <label className={styles.inboxFilterField}>
            <span>SLA 狀態</span>
            <select className={styles.select} data-testid="intake-filter-sla"
              value={filters.slaState} onChange={(event) => updateFilters({ slaState: event.target.value })}>
              <option value="">不限</option>
              <option value="ON_TRACK">進度內</option>
              <option value="DUE_SOON">即將到期</option>
              <option value="OVERDUE">已逾期</option>
              <option value="BREACHED">已違反 SLA</option>
              <option value="PAUSED">已暫停</option>
              <option value="COMPLETED">已完成</option>
            </select>
          </label>

          {([
            ["observedFrom", "最近觀察起始", "intake-filter-observed-from"],
            ["observedTo", "最近觀察截止", "intake-filter-observed-to"],
            ["updatedFrom", "更新起始", "intake-filter-updated-from"],
            ["updatedTo", "更新截止", "intake-filter-updated-to"],
          ] as const).map(([key, label, testId]) => (
            <label className={styles.inboxFilterField} key={key}>
              <span>{label}（本地時間）</span>
              <input className={styles.input} data-testid={testId} type="datetime-local" step="0.001"
                value={localFilterTime(filters[key])}
                onChange={(event) => updateFilters({ [key]: filterTimeIso(event.target.value) })} />
            </label>
          ))}

          {([
            ["masked", "欄位遮蔽", "intake-filter-masked", "有遮蔽", "無遮蔽"],
            ["restricted", "受限資料", "intake-filter-restricted", "受限制", "未受限制"],
            ["retryable", "重試狀態", "intake-filter-retryable", "可重試", "不可重試"],
          ] as const).map(([key, label, testId, yes, no]) => (
            <label className={styles.inboxFilterField} key={key}>
              <span>{label}</span>
              <select className={styles.select} data-testid={testId} value={filters[key]}
                onChange={(event) => updateFilters({ [key]: event.target.value as "" | "true" | "false" })}>
                <option value="">不限</option><option value="true">{yes}</option><option value="false">{no}</option>
              </select>
            </label>
          ))}

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
              <div className={`${styles.rowMeta} ${styles.inboxRowMetadata}`}>
                <span>送件人／負責人／時程</span>
                {([
                  ["submittedAt", "送件時間"], ["lastObservedAt", "最近觀察"], ["dueAt", "到期時間"], ["updatedAt", "更新時間"],
                ] as const).map(([key, label]) => (
                  <button className={styles.secondaryButton} key={key} type="button"
                    data-testid={`intake-sort-${key}`} onClick={() => toggleSort(key)}
                    style={{ padding: 0, border: "none", background: "none" }}>
                    {label} {filters.sortBy === key ? (filters.sortOrder === "asc" ? "▲" : "▼") : ""}
                  </button>
                ))}
              </div>
              <span>動作</span>
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
                >
                  <button type="button" className={styles.rowId} aria-label={`開啟收件 ${record.id}`}
                    data-testid={`intake-row-open-${record.id}`}
                    style={{ background: "none", border: 0, padding: 0, textAlign: "left", cursor: "pointer" }}
                    onClick={(event) => { event.stopPropagation(); onOpenDetail(record.id); }}
                  >{record.id}</button>
                  <span className={styles.srcChip}>{record.sourceId ?? "尚未提供"}</span>
                  <span className={styles.rowUrl} title={`正規化 URL: ${record.canonicalUrl}`}>
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
                  <div className={`${styles.rowMeta} ${styles.inboxRowMetadata}`}>
                    <div className={styles.inboxMetadataGroup}>
                      <span>收件方式 {record.intakeMethod ?? "尚未提供"}</span>
                      <span data-testid={`intake-row-owner-${record.id}`}>負責人 {record.ownerSubjectId ?? "尚未提供"}</span>
                      <span>指派 {record.assignmentStatus ?? "尚未提供"}</span>
                    </div>
                    <div className={styles.inboxMetadataGroup}>
                      <span>SLA {record.slaState ?? "尚未提供"}</span>
                      <span data-testid={`intake-row-due-${record.id}`}>到期 {record.dueAt ?? "尚未提供"}</span>
                      <span>區域 {record.assignedAreaId ?? "尚未提供"}</span>
                    </div>
                    <div className={styles.inboxMetadataGroup}>
                      <span data-testid={`intake-row-submitted-by-${record.id}`}>送件者 {record.submittedBy ?? "尚未提供"}</span>
                      <span data-testid={`intake-row-submitted-at-${record.id}`}>送件時間 {record.submittedAt ?? "尚未提供"}</span>
                      <span data-testid={`intake-row-observed-${record.id}`}>最近觀察 {record.lastObservedAt ?? "尚未提供"}</span>
                      <span data-testid={`intake-row-updated-${record.id}`}>更新 {record.updatedAt ?? "尚未提供"}</span>
                    </div>
                    <div className={styles.inboxMetadataGroup}>
                      <span data-testid={`intake-row-masking-${record.id}`}>{record.masked === true ? "欄位已遮蔽" : record.masked === false ? "欄位未遮蔽" : "遮蔽狀態尚未提供"}</span>
                      <span data-testid={`intake-row-restriction-${record.id}`}>{record.restricted === true ? "資料受限制" : record.restricted === false ? "資料未受限制" : "資料限制尚未提供"}</span>
                      <span>{record.failure?.retryable === true ? "可重試" : record.failure?.retryable === false ? "不可重試" : "重試狀態尚未提供"}</span>
                      <span>{record.failure?.nextAction ?? (record.stage === "NEEDS_REVIEW" ? "待人工覆核" : rowActionLabel(record))}</span>
                    </div>
                  </div>
                  <button
                    className={styles.primaryButton}
                    data-testid={`intake-row-action-${record.id}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (record.stage === "FAILED" && record.failure?.retryable && canRetry && onRetryIntake) onRetryIntake(record.id);
                      else onOpenDetail(record.id);
                    }}
                    type="button"
                    style={{ fontSize: "0.8rem", padding: "0.2rem 0.5rem" }}
                  >
                    {readOnly
                      ? "開啟"
                      : record.stage === "FAILED" && record.failure?.retryable
                        ? "重試"
                        : record.stage === "NEEDS_REVIEW"
                          ? "覆核"
                          : !record.owner
                            ? "認領"
                            : record.stage === "AWAITING_ASSISTED_ENTRY"
                              ? "要求補正"
                              : rowActionLabel(record)} →
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

          <div className={styles.inboxPaginationControls}>
            <select
              aria-label="每頁筆數"
              className={styles.select}
              data-testid="intake-page-size-select"
              onChange={(e) => updateFilters({ pageSize: parseInt(e.target.value, 10), page: 1 })}
              value={filters.pageSize}
            >
              <option value="10">每頁 10 筆</option>
              <option value="20">每頁 20 筆</option>
              <option value="50">每頁 50 筆</option>
              {![10, 20, 50].includes(filters.pageSize) ? <option value={filters.pageSize}>每頁 {filters.pageSize} 筆</option> : null}
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

      {isStateMatrixOpen ? <StateMatrix onClose={() => setIsStateMatrixOpen(false)} /> : null}

      {/* Add Dialog */}
      {isAddDialogOpen ? (
        <AddListingFromUrlDialog
          busy={busy}
          defaultHeatZoneId={selectedHeatZoneId}
          heatZoneOptions={heatZoneOptions}
          error={actionError ?? null}
          onClose={closeAddDialog}
          onOpenExisting={(intakeId) => {
            setIsAddDialogOpen(false);
            onOpenDetail(intakeId);
          }}
          onSubmit={async (input) => {
            // The submit handler moves the URL on (dialog=detail on success),
            // so only the local open state is cleared here.
            await onAddSubmit(input);
            setIsAddDialogOpen(false);
          }}
          submitterLabel={submitterLabel}
        />
      ) : null}
    </section>
  );
}

function localFilterTime(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  const pad = (part: number, width = 2) => String(part).padStart(width, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`;
}

function filterTimeIso(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toISOString() : "";
}
