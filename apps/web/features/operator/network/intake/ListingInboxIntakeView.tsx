"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import type {
  AssistedIntake,
  AssignmentReceipt,
  IntakeInboxPage,
  IntakeInboxQuery,
} from "@oday-plus/openapi-client";
import type { OperatorRoleId } from "../../navigation";
import { getOperatorRole } from "../../navigation";
import {
  operatorSecurityHeaders,
  operatorSubjectId,
} from "../../operatorSecurityHeaders";
import styles from "./intake.module.css";
import {
  AddListingFromUrlDialog,
  computeCanonicalUrlPreview,
} from "./AddListingFromUrlDialog";
import {
  existingListingHref,
  IntakeInboxMap,
  intakeDetailHref,
} from "./IntakeInboxMap";
import {
  matchLabel,
  matchTone,
  shortUrl,
  stageLabel,
  stageTone,
} from "./intakeTypes";
import {
  canPerform,
  canView,
  isReadOnly,
  NO_ACCESS_NOTE,
  READ_ONLY_NOTE,
} from "./intakePermissions";
import {
  buildIntakeClient,
  intakeApi,
  newIntakeActionIdempotencyKey,
  type IntakeApiError,
} from "./intakeClient";
import {
  useIntakeInboxQuery,
  type IntakeInboxFilterState,
  type SavedViewType,
} from "./useIntakeInboxQuery";

type ListingInboxIntakeViewProps = {
  activeRoleId: OperatorRoleId;
  activeSubjectId?: string;
  records: AssistedIntake[];
  loadState: "loading" | "ready" | "error";
  loadError?: IntakeApiError | null;
  actionError?: IntakeApiError | null;
  busy: boolean;
  selectedHeatZoneId?: string;
  onAddSubmit: (
    input: { url: string; heatZoneId: string },
  ) => AssistedIntake | void | Promise<AssistedIntake | void>;
  /** Legacy preview callback retained for integration compatibility; direct actions use durable routes. */
  onOpenDetail?: (intakeId: string) => void;
  onRetryLoad?: () => void;
  onRetryIntake?: (intakeId: string) => void | Promise<void>;
  onClaimCompleted?: (intakeId: string, receipt: AssignmentReceipt) => void;
  pageData?: IntakeInboxPage;
  onQueryChange?: (query: IntakeInboxQuery) => void;
};

const savedViewDefinition: Array<{ id: SavedViewType; label: string }> = [
  { id: "all", label: "全部物件" },
  { id: "needsReview", label: "需覆核" },
  { id: "awaitingEntry", label: "待補錄" },
  { id: "processing", label: "處理中" },
  { id: "blocked", label: "隔離／失敗" },
  { id: "ready", label: "可決策" },
];

const stageOptions = [
  "SUBMITTED",
  "CHECKING_IDENTITY",
  "CHECKING_SOURCE_POLICY",
  "AWAITING_ASSISTED_ENTRY",
  "RETRIEVING",
  "PARSING",
  "MATCHING",
  "NEEDS_REVIEW",
  "READY",
  "QUARANTINED",
  "FAILED",
  "CANCELLED",
];

export function ListingInboxIntakeView({
  activeRoleId,
  activeSubjectId,
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
  onClaimCompleted,
  pageData,
  onQueryChange,
}: ListingInboxIntakeViewProps) {
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [claimingId, setClaimingId] = useState<string | null>(null);
  const [claimedOwners, setClaimedOwners] = useState<Record<string, string>>({});
  const [directActionError, setDirectActionError] = useState<IntakeApiError | null>(null);
  const [claimReceipt, setClaimReceipt] = useState<AssignmentReceipt | null>(null);
  const [submissionReceipt, setSubmissionReceipt] =
    useState<AssistedIntake | null>(null);
  const recordsRef = useRef(records);
  const pendingSubmissionRef = useRef<{
    resolve: (record: AssistedIntake | void) => void;
    timeout: ReturnType<typeof setTimeout>;
    url: string;
  } | null>(null);
  recordsRef.current = records;

  const role = getOperatorRole(activeRoleId);
  const readOnly = isReadOnly(activeRoleId);
  const permitted = canView(activeRoleId);
  const canSubmit = canPerform("submit", activeRoleId);
  const canRetry = canPerform("retry", activeRoleId);
  const canClaim = !readOnly && canPerform("decide", activeRoleId);
  const subjectId = operatorSubjectId(activeRoleId, activeSubjectId);
  const client = useMemo(
    () => buildIntakeClient(activeRoleId, activeSubjectId),
    [activeRoleId, activeSubjectId],
  );

  const { filters, updateFilters, resetFilters, toggleSort } = useIntakeInboxQuery();
  useEffect(() => {
    onQueryChange?.(buildIntakeInboxServerQuery(filters, selectedHeatZoneId));
  }, [filters, onQueryChange, selectedHeatZoneId]);

  useEffect(() => {
    const pending = pendingSubmissionRef.current;
    if (!pending) return;
    const submitted = findSubmittedRecord(records, pending.url);
    if (!submitted) return;
    clearTimeout(pending.timeout);
    pendingSubmissionRef.current = null;
    setSubmissionReceipt(submitted);
    setIsAddDialogOpen(false);
    pending.resolve(submitted);
  }, [records]);

  useEffect(
    () => () => {
      const pending = pendingSubmissionRef.current;
      if (pending) {
        clearTimeout(pending.timeout);
        pending.resolve();
        pendingSubmissionRef.current = null;
      }
    },
    [],
  );

  const totalRecords = pageData?.total ?? records.length;
  const currentPage = pageData?.page ?? filters.page;
  const pageCount = Math.max(
    1,
    Math.ceil(totalRecords / (pageData?.pageSize ?? filters.pageSize)),
  );
  const counts = pageData?.counts ?? {
    needsReview: 0,
    awaitingEntry: 0,
    processing: 0,
    blocked: 0,
    ready: 0,
  };
  const savedViewCounts: Record<SavedViewType, number> = {
    all: totalRecords,
    needsReview: counts.needsReview,
    awaitingEntry: counts.awaitingEntry,
    processing: counts.processing,
    blocked: counts.blocked,
    ready: counts.ready,
  };

  async function claimIntake(record: AssistedIntake) {
    if (!client || !canClaim || claimingId) return;
    setClaimingId(record.id);
    setClaimReceipt(null);
    setDirectActionError(null);

    const key = newIntakeActionIdempotencyKey(record.id, "inbox-claim");
    const result = record.assignmentId
      ? await intakeApi.claimAssignment(
          client,
          record.assignmentId,
          { reason: "Direct claim from Listing Inbox" },
          { idempotencyKey: key },
        )
      : await intakeApi.assign(
          client,
          record.id,
          {
            owner_subject_id: subjectId,
            owner_role: activeRoleId,
            due_at: new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString(),
            reason: "Direct claim from Listing Inbox",
            handoff_note: "Claimed directly from the inbox row action",
          },
          { idempotencyKey: key },
        );

    setClaimingId(null);
    if (!result.ok) {
      setDirectActionError(result.error);
      return;
    }
    setClaimedOwners((current) => ({ ...current, [record.id]: result.value.owner_subject_id }));
    setClaimReceipt(result.value);
    onClaimCompleted?.(record.id, result.value);
  }

  async function submitUrl(
    input: { url: string; heatZoneId: string },
  ): Promise<AssistedIntake | void> {
    const directReceipt = await onAddSubmit(input);
    if (directReceipt) {
      setSubmissionReceipt(directReceipt);
      setIsAddDialogOpen(false);
      return directReceipt;
    }

    const alreadyApplied = findSubmittedRecord(recordsRef.current, input.url);
    if (alreadyApplied) {
      setSubmissionReceipt(alreadyApplied);
      setIsAddDialogOpen(false);
      return alreadyApplied;
    }

    return new Promise<AssistedIntake | void>((resolve) => {
      const timeout = setTimeout(() => {
        if (pendingSubmissionRef.current?.resolve === resolve) {
          pendingSubmissionRef.current = null;
          resolve();
        }
      }, 5_000);
      pendingSubmissionRef.current = { resolve, timeout, url: input.url };
    });
  }

  if (!permitted) {
    return (
      <section
        className={styles.queue}
        data-screen-label="Listing Inbox 收件匣"
        data-testid="intake-inbox-view"
      >
        <div className={styles.emptyState} data-testid="intake-no-access" role="status">
          {NO_ACCESS_NOTE}
        </div>
      </section>
    );
  }

  return (
    <section
      className={styles.queue}
      data-screen-label="Listing Inbox 收件匣"
      data-testid="intake-inbox-view"
    >
      {readOnly ? (
        <div className={styles.warnNote} data-testid="intake-read-only" role="status">
          {READ_ONLY_NOTE}
        </div>
      ) : null}

      <header className={styles.queueHeader}>
        <div>
          <span className={styles.dialogTitle}>Listing Inbox 收件匣</span>
          <span className={styles.screenBadge}>UX-SCR-EXP-003</span>
        </div>
        <div className={styles.intakeTableActions}>
          <div aria-label="檢視模式切換" data-testid="intake-view-mode-toggle">
            <button
              aria-pressed={filters.viewMode === "list"}
              className={filters.viewMode === "list" ? styles.primaryButton : styles.secondaryButton}
              data-testid="intake-view-mode-list"
              onClick={() => updateFilters({ viewMode: "list" })}
              type="button"
            >
              列表
            </button>
            <button
              aria-pressed={filters.viewMode === "map"}
              className={filters.viewMode === "map" ? styles.primaryButton : styles.secondaryButton}
              data-testid="intake-view-mode-map"
              onClick={() => updateFilters({ viewMode: "map" })}
              type="button"
            >
              地圖
            </button>
          </div>
          {canSubmit ? (
            <button
              className={styles.addButton}
              data-testid="intake-add-button"
              onClick={() => {
                setSubmissionReceipt(null);
                setIsAddDialogOpen(true);
              }}
              type="button"
            >
              從網址新增物件
            </button>
          ) : null}
        </div>
      </header>

      <nav aria-label="收件 saved views" className={styles.intakeTableActions}>
        {savedViewDefinition.map((tab) => (
          <button
            aria-current={filters.savedView === tab.id ? "page" : undefined}
            className={filters.savedView === tab.id ? styles.primaryButton : styles.secondaryButton}
            data-testid={`intake-tab-${tab.id}`}
            key={tab.id}
            onClick={() => updateFilters({ savedView: tab.id })}
            type="button"
          >
            {tab.label} ({savedViewCounts[tab.id]})
          </button>
        ))}
      </nav>

      <div className={styles.intakeFilterGrid}>
        <FilterInput
          label="搜尋"
          testId="intake-search-input"
          value={filters.search}
          onChange={(value) => updateFilters({ search: value })}
          placeholder="URL / Intake / Listing / source / owner"
        />
        <FilterSelect
          label="收件方式"
          testId="intake-filter-method"
          value={filters.intakeMethod}
          onChange={(value) => updateFilters({ intakeMethod: value })}
          options={[
            ["", "所有收件方式"],
            ["URL", "URL"],
            ["MANUAL", "Manual"],
            ["CSV", "CSV"],
            ["APPROVED_FEED", "Approved feed"],
          ]}
        />
        <FilterSelect
          label="處理階段"
          testId="intake-filter-stage"
          value={filters.intakeStage}
          onChange={(value) => updateFilters({ intakeStage: value })}
          options={[["", "所有處理階段"], ...stageOptions.map((value) => [value, value])]}
        />
        <FilterSelect
          label="比對結果"
          testId="intake-filter-outcome"
          value={filters.matchOutcome}
          onChange={(value) => updateFilters({ matchOutcome: value })}
          options={[
            ["", "所有比對結果"],
            ["NEW", "NEW"],
            ["EXACT_DUPLICATE", "EXACT_DUPLICATE"],
            ["REVISION", "REVISION"],
            ["POSSIBLE_MATCH", "POSSIBLE_MATCH"],
            ["QUARANTINED", "QUARANTINED"],
          ]}
        />
      </div>

      <details className={styles.intakeFilterAdvanced} data-testid="intake-advanced-filters">
        <summary>進階篩選</summary>
        <div className={styles.intakeFilterGrid}>
          <FilterInput label="來源" testId="intake-filter-source" value={filters.sourceId} onChange={(sourceId) => updateFilters({ sourceId })} />
          <FilterInput label="送件人" testId="intake-filter-submitter" value={filters.submittedBy} onChange={(submittedBy) => updateFilters({ submittedBy })} />
          <FilterInput label="Owner" testId="intake-filter-owner" value={filters.owner} onChange={(owner) => updateFilters({ owner })} />
          <FilterSelect label="Assignment" testId="intake-filter-assignment" value={filters.assignmentStatus} onChange={(assignmentStatus) => updateFilters({ assignmentStatus })} options={booleanOrEnumOptions(["UNASSIGNED", "ASSIGNED", "CLAIMED", "TRANSFERRED", "ESCALATED", "COMPLETED"])} />
          <FilterSelect label="需覆核" testId="intake-filter-needs-review" value={filters.needsReview} onChange={(needsReview) => updateFilters({ needsReview })} options={booleanOptions("全部", "需要", "不需要")} />
          <FilterSelect label="SLA" testId="intake-filter-sla" value={filters.slaState} onChange={(slaState) => updateFilters({ slaState })} options={booleanOrEnumOptions(["ON_TRACK", "DUE_SOON", "OVERDUE", "BREACHED", "PAUSED", "COMPLETED"])} />
          <FilterInput label="HeatZone" testId="intake-filter-heatzone" value={filters.heatZoneId} onChange={(heatZoneId) => updateFilters({ heatZoneId })} />
          <FilterInput label="Area" testId="intake-filter-area" value={filters.areaId} onChange={(areaId) => updateFilters({ areaId })} />
          <FilterInput label="Observed from" testId="intake-filter-observed-from" type="datetime-local" value={filters.observedFrom} onChange={(observedFrom) => updateFilters({ observedFrom })} />
          <FilterInput label="Observed to" testId="intake-filter-observed-to" type="datetime-local" value={filters.observedTo} onChange={(observedTo) => updateFilters({ observedTo })} />
          <FilterInput label="Updated from" testId="intake-filter-updated-from" type="datetime-local" value={filters.updatedFrom} onChange={(updatedFrom) => updateFilters({ updatedFrom })} />
          <FilterInput label="Updated to" testId="intake-filter-updated-to" type="datetime-local" value={filters.updatedTo} onChange={(updatedTo) => updateFilters({ updatedTo })} />
          <FilterSelect label="Restricted data" testId="intake-filter-restricted" value={filters.restrictedData} onChange={(restrictedData) => updateFilters({ restrictedData })} options={booleanOptions("全部", "含 restricted", "不含 restricted")} />
          <FilterSelect label="Quarantined" testId="intake-filter-quarantined" value={filters.quarantined} onChange={(quarantined) => updateFilters({ quarantined })} options={booleanOptions("全部", "已隔離", "未隔離")} />
          <FilterSelect label="Failed" testId="intake-filter-failed" value={filters.failed} onChange={(failed) => updateFilters({ failed })} options={booleanOptions("全部", "失敗", "未失敗")} />
          <FilterSelect label="Retryability" testId="intake-filter-retryable" value={filters.retryable} onChange={(retryable) => updateFilters({ retryable })} options={booleanOptions("全部", "可重試", "不可重試")} />
        </div>
      </details>
      <button className={styles.secondaryButton} data-testid="intake-filter-reset" onClick={resetFilters} type="button">
        重置篩選
      </button>

      {submissionReceipt ? (
        <SubmissionReceipt record={submissionReceipt} />
      ) : null}

      {claimReceipt ? (
        <div className={styles.intakeActionReceipt} data-testid="intake-claim-receipt" role="status">
          <strong>認領已寫入</strong>
          <span>
            Assignment {claimReceipt.assignment_id} · {claimReceipt.status} · version {claimReceipt.version}
          </span>
          <span>Owner {claimReceipt.owner_subject_id} · due {claimReceipt.due_at}</span>
          <span>Audit {claimReceipt.audit_event_id}</span>
        </div>
      ) : null}

      {directActionError ?? actionError ? (
        <div className={styles.errorPanel} data-testid="intake-direct-action-error" role="alert">
          <span className={styles.errorSummary}>{(directActionError ?? actionError)!.summary}</span>
          <span className={styles.errorMeta}>
            {(directActionError ?? actionError)!.code} · {(directActionError ?? actionError)!.occurredAt}
          </span>
          <span className={styles.errorNext}>下一步：{(directActionError ?? actionError)!.nextAction}</span>
        </div>
      ) : null}

      {loadState === "loading" ? (
        <div className={styles.loadingState} data-testid="intake-inbox-loading" role="status">
          載入 Listing Inbox 收件資料中…
        </div>
      ) : null}
      {loadState === "error" ? (
        <div className={styles.errorPanel} data-testid="intake-inbox-error" role="alert">
          <span className={styles.errorSummary}>{loadError?.summary ?? "無法載入 Listing Inbox 收件資料。"}</span>
          <span className={styles.errorNext}>下一步：請重試載入；此畫面不會顯示模擬資料。</span>
          {onRetryLoad ? <button className={styles.secondaryButton} onClick={onRetryLoad} type="button">重新載入</button> : null}
        </div>
      ) : null}
      {loadState === "ready" && pageData && pageData.evidenceState !== "complete" ? (
        <div className={pageData.evidenceState === "degraded" ? styles.errorPanel : styles.warnNote} data-testid={`intake-evidence-${pageData.evidenceState}`} role="status">
          {pageData.evidenceState === "degraded"
            ? "證據降級：部分收件處理失敗；請查看可重試性與 correlation ID。"
            : "證據部分可用：部分收件尚未產生來源快照。"}
        </div>
      ) : null}

      {loadState === "ready" && filters.viewMode === "map" ? (
        <IntakeInboxMap records={records} />
      ) : null}

      {loadState === "ready" && filters.viewMode === "list" ? (
        records.length === 0 ? (
          <div className={styles.emptyState} data-testid="intake-inbox-empty">目前無符合條件的收件紀錄。</div>
        ) : (
          <IntakeTable
            canClaim={canClaim}
            canRetry={canRetry}
            claimingId={claimingId}
            claimedOwners={claimedOwners}
            filters={filters}
            onClaim={(record) => void claimIntake(record)}
            onPreview={onOpenDetail}
            onRetry={(record) => {
              if (onRetryIntake) void onRetryIntake(record.id);
            }}
            records={records}
            readOnly={readOnly}
            toggleSort={toggleSort}
            updateFilters={updateFilters}
          />
        )
      ) : null}

      {loadState === "ready" && totalRecords > 0 ? (
        <nav aria-label="收件分頁" className={styles.queueHeader} data-testid="intake-pagination">
          <span className={styles.queueHint}>
            共 {totalRecords} 筆（第 {currentPage} / {pageCount} 頁） · stable sort {filters.sortBy} {filters.sortOrder}
          </span>
          <div className={styles.intakeTableActions}>
            <select
              aria-label="每頁筆數"
              className={styles.select}
              data-testid="intake-page-size-select"
              onChange={(event) => updateFilters({ pageSize: Number(event.target.value), page: 1, cursor: "" })}
              value={filters.pageSize}
            >
              <option value="10">每頁 10 筆</option>
              <option value="20">每頁 20 筆</option>
              <option value="50">每頁 50 筆</option>
            </select>
            <button
              className={styles.secondaryButton}
              data-testid="intake-prev-page"
              disabled={pageData?.previousCursor === null || (!pageData?.previousCursor && currentPage <= 1)}
              onClick={() => updateFilters({ cursor: pageData?.previousCursor ?? "", page: Math.max(1, currentPage - 1) })}
              type="button"
            >
              上一頁
            </button>
            <button
              className={styles.secondaryButton}
              data-testid="intake-next-page"
              disabled={pageData?.nextCursor === null || (!pageData?.nextCursor && currentPage >= pageCount)}
              onClick={() => updateFilters({ cursor: pageData?.nextCursor ?? "", page: currentPage + 1 })}
              type="button"
            >
              下一頁
            </button>
          </div>
        </nav>
      ) : null}

      {isAddDialogOpen ? (
        <AddListingFromUrlDialog
          busy={busy}
          defaultHeatZoneId={selectedHeatZoneId}
          error={actionError ?? null}
          onClose={() => setIsAddDialogOpen(false)}
          onSubmit={submitUrl}
          ownerLabel={subjectId}
          scopeLabel={selectedHeatZoneId ? `HeatZone ${selectedHeatZoneId}` : "Tenant-wide expansion intake"}
          submitterLabel={`${role.label} (${subjectId})`}
          tenantLabel={
            records[0]?.tenantId ??
            records[0]?.scope?.tenant_id ??
            operatorSecurityHeaders(activeRoleId, activeSubjectId)["X-Tenant-Id"]
          }
        />
      ) : null}
    </section>
  );
}

function SubmissionReceipt({ record }: { record: AssistedIntake }) {
  const duplicateListingId =
    record.matchResult?.outcome === "EXACT_DUPLICATE"
      ? record.matchResult.targetListingId
      : null;
  return (
    <section
      aria-label="最近 URL 送件收據"
      className={styles.intakeActionReceipt}
      data-testid="intake-inbox-submission-receipt"
    >
      <strong>伺服器送件收據</strong>
      <span>
        Intake {record.id} · version {record.version} · {record.stage}
      </span>
      <span>
        Source {record.sourceId} · Policy {record.policy} · Correlation{" "}
        {record.correlationId ?? "未提供"}
      </span>
      <span className={styles.mono}>Original {record.originalUrl}</span>
      <span className={styles.mono}>Canonical {record.canonicalUrl}</span>
      <a
        className={styles.primaryButton}
        data-testid="intake-receipt-primary-link"
        href={
          duplicateListingId
            ? existingListingHref(duplicateListingId)
            : intakeDetailHref(record.id)
        }
      >
        {duplicateListingId
          ? `開啟既有 Listing ${duplicateListingId}`
          : `開啟收件 ${record.id}`}
      </a>
    </section>
  );
}

function findSubmittedRecord(
  records: AssistedIntake[],
  originalUrl: string,
): AssistedIntake | undefined {
  return records.find(
    (record) =>
      record.originalUrl === originalUrl ||
      record.canonicalUrl === originalUrl ||
      record.canonicalUrl === computeCanonicalUrlPreview(originalUrl),
  );
}

export function buildIntakeInboxServerQuery(
  filters: IntakeInboxFilterState,
  selectedHeatZoneId?: string,
): IntakeInboxQuery {
  return {
    selectedHeatZoneId:
      selectedHeatZoneId || filters.heatZoneId || undefined,
    page: filters.page,
    pageSize: filters.pageSize,
    cursor: filters.cursor || undefined,
    search: filters.search || undefined,
    savedView: filters.savedView,
    intakeMethod: filters.intakeMethod || undefined,
    intakeStage: filters.intakeStage || undefined,
    matchOutcome: filters.matchOutcome || undefined,
    sourceId: filters.sourceId || undefined,
    submittedBy: filters.submittedBy || undefined,
    owner: filters.owner || undefined,
    assignmentStatus: filters.assignmentStatus || undefined,
    needsReview: filters.needsReview || undefined,
    slaState: filters.slaState || undefined,
    heatZoneId: filters.heatZoneId || undefined,
    areaId: filters.areaId || undefined,
    observedFrom: toApiTimestamp(filters.observedFrom),
    observedTo: toApiTimestamp(filters.observedTo),
    updatedFrom: toApiTimestamp(filters.updatedFrom),
    updatedTo: toApiTimestamp(filters.updatedTo),
    restrictedData: filters.restrictedData || undefined,
    quarantined: filters.quarantined || undefined,
    failed: filters.failed || undefined,
    retryable: filters.retryable || undefined,
    sortBy: filters.sortBy,
    sortOrder: filters.sortOrder,
  };
}

function toApiTimestamp(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString();
}

function IntakeTable({
  canClaim,
  canRetry,
  claimingId,
  claimedOwners,
  filters,
  onClaim,
  onPreview,
  onRetry,
  records,
  readOnly,
  toggleSort,
  updateFilters,
}: {
  canClaim: boolean;
  canRetry: boolean;
  claimingId: string | null;
  claimedOwners: Record<string, string>;
  filters: IntakeInboxFilterState;
  onClaim: (record: AssistedIntake) => void;
  onPreview?: (intakeId: string) => void;
  onRetry: (record: AssistedIntake) => void;
  records: AssistedIntake[];
  readOnly: boolean;
  toggleSort: (column: string) => void;
  updateFilters: (updates: Partial<IntakeInboxFilterState>) => void;
}) {
  return (
    <div className={styles.intakeTableWrap}>
      <table className={styles.intakeTable} data-testid="intake-table">
        <caption>Listing Inbox 收件、處理、比對、責任與 SLA</caption>
        <thead>
          <tr>
            <th scope="col">選取</th>
            <SortableHeader column="id" filters={filters} label="Listing / Intake" toggleSort={toggleSort} />
            <SortableHeader column="sourceId" filters={filters} label="來源" toggleSort={toggleSort} />
            <SortableHeader column="intakeMethod" filters={filters} label="方式" toggleSort={toggleSort} />
            <SortableHeader column="stage" filters={filters} label="階段" toggleSort={toggleSort} />
            <SortableHeader column="matchOutcome" filters={filters} label="比對結果" toggleSort={toggleSort} />
            <th scope="col">問題／下一步</th>
            <SortableHeader column="owner" filters={filters} label="Owner / Assignment" toggleSort={toggleSort} />
            <SortableHeader column="dueAt" filters={filters} label="Due / SLA" toggleSort={toggleSort} />
            <SortableHeader column="submitter" filters={filters} label="送件人" toggleSort={toggleSort} />
            <th scope="col">HeatZone / Area</th>
            <SortableHeader column="observedAt" filters={filters} label="Observed" toggleSort={toggleSort} />
            <SortableHeader column="updatedAt" filters={filters} label="Updated" toggleSort={toggleSort} />
            <th scope="col">資料限制</th>
            <th scope="col">直接動作</th>
          </tr>
        </thead>
        <tbody>
          {records.map((record) => {
            const outcome = record.matchResult?.outcome;
            const owner = claimedOwners[record.id] ?? record.owner;
            const retryable = record.stage === "FAILED" && record.failure?.retryable;
            return (
              <tr
                data-selected={record.id === filters.selectedIntakeId ? "true" : undefined}
                data-testid={`intake-inbox-row-${record.id}`}
                key={record.id}
                onClick={(event) => {
                  if (
                    event.target instanceof HTMLElement &&
                    event.target.closest("a, button, input, select, textarea")
                  ) {
                    return;
                  }
                  onPreview?.(record.id);
                }}
              >
                <td>
                  <input
                    aria-label={`選取收件 ${record.id}`}
                    checked={record.id === filters.selectedIntakeId}
                    name="selected-intake"
                    onChange={() => updateFilters({ selectedIntakeId: record.id })}
                    type="radio"
                  />
                </td>
                <td>
                  <a href={intakeDetailHref(record.id)}>{record.id}</a>
                  <br />
                  {record.listingId ? (
                    <a href={`/w/expansion/listings?selected=${encodeURIComponent(record.listingId)}&drawer=listing`}>
                      Listing {record.listingId}
                    </a>
                  ) : (
                    <span>尚無 Listing</span>
                  )}
                  <br />
                  <span className={styles.mono} title={record.canonicalUrl}>{shortUrl(record.canonicalUrl)}</span>
                </td>
                <td><span className={styles.srcChip}>{record.sourceId}</span></td>
                <td>{record.intakeMethod ?? "URL"}</td>
                <td><span className={styles.chip} data-tone={stageTone(record.stage)}>{stageLabel(record.stage)}<br />{record.stage}</span></td>
                <td>
                  {outcome ? <span className={styles.chip} data-tone={matchTone(outcome)}>{matchLabel(outcome)}<br />{outcome}</span> : "尚未比對"}
                </td>
                <td>{record.issue ?? record.failure?.nextAction ?? "依目前階段繼續處理"}</td>
                <td>{owner || "未認領"}<br />{record.assignmentStatus ?? "UNASSIGNED"}</td>
                <td>{formatTimestamp(record.dueAt)}<br />{record.slaState ?? "未提供"}</td>
                <td>{record.submitter}</td>
                <td>{record.heatZoneId ?? "未指定"}<br />{record.assignedAreaId ?? "未指定 area"}</td>
                <td>{formatTimestamp(record.lastObservedAt ?? record.capturedAt)}</td>
                <td>{formatTimestamp(record.lastUpdatedAt ?? record.auditEvents?.at(-1)?.occurredAt)}</td>
                <td>
                  {record.restrictedData ? `Restricted · ${(record.maskedFields ?? []).join(", ") || "masked"}` : "一般"}
                  <br />
                  {retryable ? "可重試" : record.stage === "FAILED" ? "不可重試" : "無失敗"}
                </td>
                <td>
                  <div className={styles.intakeTableActions}>
                    <a className={styles.secondaryButton} data-testid={`intake-open-${record.id}`} href={intakeDetailHref(record.id)}>開啟</a>
                    {canClaim && !owner ? (
                      <button className={styles.secondaryButton} data-testid={`intake-claim-${record.id}`} disabled={Boolean(claimingId)} onClick={() => onClaim(record)} type="button">
                        {claimingId === record.id ? "認領中…" : "認領"}
                      </button>
                    ) : null}
                    {!readOnly && record.stage === "NEEDS_REVIEW" ? (
                      <a className={styles.primaryButton} data-testid={`intake-review-${record.id}`} href={intakeDetailHref(record.id, "section=identity&compare=true")}>覆核</a>
                    ) : null}
                    {retryable && canRetry ? (
                      <button className={styles.secondaryButton} data-testid={`intake-retry-${record.id}`} onClick={() => onRetry(record)} type="button">重試</button>
                    ) : null}
                    {!readOnly && (record.stage === "AWAITING_ASSISTED_ENTRY" || record.stage === "NEEDS_REVIEW") ? (
                      <a className={styles.secondaryButton} data-testid={`intake-correction-${record.id}`} href={intakeDetailHref(record.id, "section=fields&action=correction")}>要求補正</a>
                    ) : null}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SortableHeader({
  column,
  filters,
  label,
  toggleSort,
}: {
  column: string;
  filters: IntakeInboxFilterState;
  label: string;
  toggleSort: (column: string) => void;
}) {
  const active = filters.sortBy === column;
  return (
    <th aria-sort={active ? (filters.sortOrder === "asc" ? "ascending" : "descending") : "none"} scope="col">
      <button className={styles.intakeTableSort} onClick={() => toggleSort(column)} type="button">
        {label} {active ? (filters.sortOrder === "asc" ? "▲" : "▼") : ""}
      </button>
    </th>
  );
}

function FilterInput({
  label,
  onChange,
  placeholder,
  testId,
  type = "text",
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  placeholder?: string;
  testId: string;
  type?: string;
  value: string;
}) {
  return (
    <label>
      <span className={styles.fieldLabel}>{label}</span>
      <input className={styles.input} data-testid={testId} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} type={type} value={value} />
    </label>
  );
}

function FilterSelect({
  label,
  onChange,
  options,
  testId,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  options: string[][];
  testId: string;
  value: string;
}) {
  return (
    <label>
      <span className={styles.fieldLabel}>{label}</span>
      <select className={styles.select} data-testid={testId} onChange={(event) => onChange(event.target.value)} value={value}>
        {options.map(([optionValue, optionLabel]) => <option key={optionValue || "all"} value={optionValue}>{optionLabel}</option>)}
      </select>
    </label>
  );
}

function booleanOptions(all: string, yes: string, no: string): string[][] {
  return [["", all], ["true", yes], ["false", no]];
}

function booleanOrEnumOptions(values: string[]): string[][] {
  return [["", "全部"], ...values.map((value) => [value, value])];
}

function formatTimestamp(value?: string | null): string {
  if (!value) return "未提供";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-TW", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "Asia/Taipei",
  }).format(parsed);
}
