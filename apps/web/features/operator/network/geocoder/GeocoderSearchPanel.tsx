"use client";

// Governed geocoder address search — operator surface (ODP-CAP-GEOCODER-SEARCH-001).
//
// Owned layer  : the address search interaction — query entry, candidate
//                presentation, the explicit-review gate on flagged candidates,
//                and the audit event emitted on every terminal action.
// Not changing : where the accepted coordinate is written. The parent surface
//                owns that; this panel hands it a candidate plus the assessment
//                and audit event that justify it.
// Composes with: geocoderClient (the only lookup path), geocoderPolicy (the
//                verdict), geocoderAudit (the record).
//
// Two rules drive the markup:
//
//  1. Nothing on screen is a coordinate this panel invented. Every latitude and
//     longitude rendered came from a provider row that supplied both. On any
//     error the candidate list is CLEARED rather than left stale, so a failed
//     retry can never leave the previous search's pins looking current.
//  2. State is never encoded by colour alone — each flagged candidate carries
//     text and a role="status"/"alert" region, matching the intake surface's
//     accessibility rule.

import { useCallback, useId, useMemo, useRef, useState } from "react";
import styles from "./geocoder.module.css";
import { searchAddress, isGeocoderConfigured, unconfiguredGeocoderError } from "./geocoderClient";
import type { GeocoderApiError } from "./geocoderClient";
import {
  MIN_REVIEW_REASON_LENGTH,
  assessCandidates,
  normalizeAddress,
  requiresExplicitReview,
  riskSummaryFor,
  validateQuery,
  validateSelection,
} from "./geocoderPolicy";
import { buildRejectionAuditEvent, buildSelectionAuditEvent } from "./geocoderAudit";
import type {
  CandidateAssessment,
  GeocodeAuditEvent,
  GeocodeCandidate,
  GeocodeSearchResult,
} from "./geocoderTypes";

export type GeocoderSearchPanelProps = {
  /** Travels verbatim onto the audit event as the acting role. */
  actorRoleId: string;
  /** Both gates are required rather than defaulted: the caller owns the role check. */
  canSearch: boolean;
  canSelect: boolean;
  /** Called once a candidate has cleared the review gate. */
  onSelect: (selection: {
    candidate: GeocodeCandidate;
    assessment: CandidateAssessment;
    audit: GeocodeAuditEvent;
  }) => void;
  /** Called for every terminal action, including a recorded rejection. */
  onAudit?: (event: GeocodeAuditEvent) => void;
  /** Test seam; production callers omit it. */
  searchImpl?: typeof searchAddress;
  /** Test seam mirroring isGeocoderConfigured(). */
  configuredOverride?: boolean;
};

const NO_PERMISSION_NOTE = "你的角色不可使用地址定位搜尋，請改由展店角色操作。";
const NO_SELECT_NOTE = "唯讀模式 — 你可以搜尋與檢視候選點，但不可採用座標。";

export function GeocoderSearchPanel({
  actorRoleId,
  canSearch,
  canSelect,
  onSelect,
  onAudit,
  searchImpl = searchAddress,
  configuredOverride,
}: GeocoderSearchPanelProps) {
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<GeocoderApiError | null>(null);
  const [result, setResult] = useState<GeocodeSearchResult | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reviewAcknowledged, setReviewAcknowledged] = useState(false);
  const [reviewReason, setReviewReason] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const headingId = useId();
  const configured = configuredOverride ?? isGeocoderConfigured();
  // The query that produced `result`, so the admin-mismatch comparison is made
  // against the address actually searched and not whatever is in the box now.
  const searchedQueryRef = useRef("");

  const assessments = useMemo(() => {
    if (!result) return [];
    const normalized = normalizeAddress(searchedQueryRef.current || result.query);
    return assessCandidates(result.candidates, {
      city: normalized.city,
      district: normalized.district,
    });
  }, [result]);

  const selected = useMemo(
    () => result?.candidates.find((candidate) => candidate.candidateId === selectedId) ?? null,
    [result, selectedId],
  );
  const selectedAssessment = useMemo(
    () => assessments.find((assessment) => assessment.candidateId === selectedId) ?? null,
    [assessments, selectedId],
  );
  const needsReview = selectedAssessment ? requiresExplicitReview(selectedAssessment) : false;
  const riskSummary = selectedAssessment ? riskSummaryFor(selectedAssessment) : "";

  const resetSelection = useCallback(() => {
    setSelectedId(null);
    setReviewAcknowledged(false);
    setReviewReason("");
    setLocalError(null);
  }, []);

  const handleSearch = useCallback(async () => {
    if (busy || !canSearch) return;
    const validated = validateQuery(query);
    if (!validated.ok) {
      setLocalError(validated.message);
      return;
    }
    if (!configured) {
      // Surfaced through the same error block as a provider failure, so an
      // unconfigured endpoint reads as "no lookup happened", not "no results".
      setResult(null);
      resetSelection();
      setError(unconfiguredGeocoderError());
      return;
    }

    setBusy(true);
    setLocalError(null);
    resetSelection();
    const outcome = await searchImpl(validated.value);
    searchedQueryRef.current = validated.value;
    if (outcome.ok) {
      setError(null);
      setResult(outcome.value);
    } else {
      // Clearing the previous result is the point: a stale candidate list under
      // a fresh error banner is exactly how a fabricated coordinate gets used.
      setResult(null);
      setError(outcome.error);
    }
    setBusy(false);
  }, [busy, canSearch, configured, query, resetSelection, searchImpl]);

  const handleConfirm = useCallback(() => {
    if (!selected || !selectedAssessment || !canSelect) return;
    const gate = validateSelection(selectedAssessment, { reviewAcknowledged, reviewReason });
    if (!gate.ok) {
      setLocalError(gate.message);
      return;
    }
    setLocalError(null);
    const audit = buildSelectionAuditEvent({
      candidate: selected,
      assessment: selectedAssessment,
      actorRoleId,
      correlationId: result?.correlationId ?? "",
      riskSummary,
      reviewReason,
      reviewAcknowledged,
    });
    onAudit?.(audit);
    onSelect({ candidate: selected, assessment: selectedAssessment, audit });
  }, [
    actorRoleId,
    canSelect,
    onAudit,
    onSelect,
    result,
    reviewAcknowledged,
    reviewReason,
    riskSummary,
    selected,
    selectedAssessment,
  ]);

  const handleRecordRejection = useCallback(() => {
    if (!canSelect) return;
    const reason = rejectReason.trim();
    if (reason.length < MIN_REVIEW_REASON_LENGTH) {
      setLocalError(`無法定位的理由必填且至少 ${MIN_REVIEW_REASON_LENGTH} 個字（寫入 Audit）。`);
      return;
    }
    setLocalError(null);
    onAudit?.(
      buildRejectionAuditEvent({
        addressRaw: searchedQueryRef.current || query,
        actorRoleId,
        correlationId: result?.correlationId ?? error?.correlationId ?? "",
        reason,
        riskSummary:
          "本次地址搜尋未取得可採用的座標，記錄為無法定位；後續流程不會有推估座標。",
      }),
    );
    setRejectReason("");
  }, [actorRoleId, canSelect, error, onAudit, query, rejectReason, result]);

  if (!canSearch) {
    return (
      <section className={styles.panel} data-testid="geocoder-search-panel" role="status">
        <div className={styles.deniedNote} data-testid="geocoder-denied">{NO_PERMISSION_NOTE}</div>
      </section>
    );
  }

  const showEmptyState = result !== null && result.candidates.length === 0;

  return (
    <section
      aria-labelledby={headingId}
      className={styles.panel}
      data-testid="geocoder-search-panel"
    >
      <header className={styles.head}>
        <h3 className={styles.title} id={headingId}>地址定位搜尋 GEOCODER SEARCH</h3>
        <span className={styles.headHint}>
          低信心或精度不足的候選點必須經人工覆核；採用與覆寫皆寫入 Audit。
        </span>
      </header>

      {!configured ? (
        <div className={styles.warnNote} data-testid="geocoder-unconfigured" role="status">
          地址定位服務尚未設定，搜尋不會回傳任何座標。此畫面不顯示模擬或推估位置。
        </div>
      ) : null}
      {!canSelect ? (
        <div className={styles.warnNote} data-testid="geocoder-readonly" role="status">{NO_SELECT_NOTE}</div>
      ) : null}

      <div className={styles.searchRow}>
        <label className={styles.fieldLabel} htmlFor={`${headingId}-input`}>
          地址（含縣市、路名與門牌）
        </label>
        <div className={styles.searchControls}>
          <input
            aria-label="地址搜尋"
            className={styles.input}
            data-testid="geocoder-query-input"
            disabled={busy}
            id={`${headingId}-input`}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void handleSearch();
              }
            }}
            placeholder="例：新北市新莊區興德路 100 號"
            value={query}
          />
          <button
            className={styles.primaryButton}
            data-testid="geocoder-search-button"
            disabled={busy}
            onClick={() => void handleSearch()}
            type="button"
          >
            {busy ? "搜尋中…" : "搜尋地址"}
          </button>
        </div>
      </div>

      {error ? (
        <div className={styles.errorBlock} data-testid="geocoder-error" role="alert">
          <strong className={styles.errorSummary}>{error.summary}</strong>
          <span className={styles.errorNext}>{error.nextAction}</span>
          <span className={styles.errorMeta} data-testid="geocoder-error-meta">
            錯誤代碼 {error.code}
            {error.correlationId ? ` · correlation_id ${error.correlationId}` : ""}
            {` · ${error.occurredAt}`}
          </span>
          <span className={styles.errorMeta}>本次搜尋沒有取得任何座標，畫面不會以推估位置代替。</span>
        </div>
      ) : null}

      {result ? (
        <div className={styles.resultMeta} data-testid="geocoder-result-meta">
          <span>查詢「{result.query}」· 候選 {result.candidates.length} 筆</span>
          <span>正規化 {result.normalizedQuery || "—"}</span>
          <span>correlation_id {result.correlationId}</span>
          {result.rejectedRowCount > 0 ? (
            <span data-testid="geocoder-rejected-rows">
              另有 {result.rejectedRowCount} 筆結果因缺少可用座標而未列出（系統不會為其推估位置）。
            </span>
          ) : null}
        </div>
      ) : null}

      {showEmptyState ? (
        <div className={styles.emptyState} data-testid="geocoder-empty" role="status">
          找不到符合的地址。請調整關鍵字重新搜尋，或記錄為無法定位。
        </div>
      ) : null}

      {result && result.candidates.length > 0 ? (
        <ul className={styles.candidateList} data-testid="geocoder-candidate-list">
          {result.candidates.map((candidate, index) => {
            const assessment = assessments[index];
            const flagged = assessment ? requiresExplicitReview(assessment) : true;
            const active = candidate.candidateId === selectedId;
            return (
              <li
                className={`${styles.candidate} ${flagged ? styles.candidateFlagged : ""} ${active ? styles.candidateActive : ""}`}
                data-flagged={flagged ? "true" : "false"}
                data-testid={`geocoder-candidate-${index}`}
                key={candidate.candidateId}
              >
                <div className={styles.candidateMain}>
                  <span className={styles.candidateAddress}>
                    {candidate.formattedAddress || candidate.addressRaw}
                  </span>
                  <span className={styles.candidateCoords} data-testid={`geocoder-candidate-coords-${index}`}>
                    {candidate.latitude.toFixed(6)}, {candidate.longitude.toFixed(6)}
                  </span>
                </div>
                <div className={styles.candidateFacts}>
                  <span>精度 {candidate.precision || "未提供"}</span>
                  <span data-testid={`geocoder-candidate-confidence-${index}`}>
                    信心 {Number.isFinite(candidate.confidence) ? candidate.confidence.toFixed(2) : "未提供"}
                  </span>
                  <span>來源 {candidate.provider || "未提供"}</span>
                  <span>{candidate.adminCity || "—"} {candidate.adminDistrict || ""}</span>
                </div>
                <div
                  className={flagged ? styles.candidateBadgeFlagged : styles.candidateBadgeClean}
                  data-testid={`geocoder-candidate-requirement-${index}`}
                >
                  {flagged ? "需人工覆核" : "可直接採用"}
                </div>
                {flagged && assessment ? (
                  <ul className={styles.reasonList} data-testid={`geocoder-candidate-reasons-${index}`}>
                    {assessment.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                ) : null}
                <button
                  aria-pressed={active}
                  className={styles.selectButton}
                  data-testid={`geocoder-select-${index}`}
                  disabled={!canSelect}
                  onClick={() => {
                    setSelectedId(candidate.candidateId);
                    setReviewAcknowledged(false);
                    setReviewReason("");
                    setLocalError(null);
                  }}
                  type="button"
                >
                  {active ? "已選取" : "選取此候選點"}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}

      {selected && selectedAssessment ? (
        <div className={styles.confirmBlock} data-testid="geocoder-confirm-block">
          <div className={styles.riskSummary} data-testid="geocoder-risk-summary">{riskSummary}</div>
          {needsReview ? (
            <>
              <label className={styles.checkboxRow}>
                <input
                  checked={reviewAcknowledged}
                  data-testid="geocoder-review-ack"
                  disabled={!canSelect}
                  onChange={(event) => setReviewAcknowledged(event.target.checked)}
                  type="checkbox"
                />
                <span>我已閱讀上述定位品質問題，確認採用此座標並負責後續複查。</span>
              </label>
              <label className={styles.fieldLabel} htmlFor={`${headingId}-reason`}>
                覆核理由（必填 — 寫入 Audit）
              </label>
              <textarea
                className={styles.textarea}
                data-testid="geocoder-review-reason"
                disabled={!canSelect}
                id={`${headingId}-reason`}
                onChange={(event) => setReviewReason(event.target.value)}
                placeholder="說明為何在定位品質不足的情況下仍採用此座標，以及後續如何複查。"
                rows={3}
                value={reviewReason}
              />
            </>
          ) : null}
          {localError ? (
            <div className={styles.errorText} data-testid="geocoder-local-error" role="alert">{localError}</div>
          ) : null}
          <div className={styles.confirmActions}>
            <button
              className={styles.primaryButton}
              data-testid="geocoder-confirm"
              disabled={!canSelect}
              onClick={handleConfirm}
              type="button"
            >
              {needsReview ? "確認覆核並採用座標" : "採用此座標"}
            </button>
            <button
              className={styles.secondaryButton}
              data-testid="geocoder-cancel"
              onClick={resetSelection}
              type="button"
            >
              取消選取
            </button>
          </div>
        </div>
      ) : null}

      {(showEmptyState || error) && canSelect ? (
        <div className={styles.rejectBlock} data-testid="geocoder-reject-block">
          <label className={styles.fieldLabel} htmlFor={`${headingId}-reject`}>
            記錄為無法定位（必填理由 — 寫入 Audit）
          </label>
          <textarea
            className={styles.textarea}
            data-testid="geocoder-reject-reason"
            id={`${headingId}-reject`}
            onChange={(event) => setRejectReason(event.target.value)}
            placeholder="說明為何此地址無法取得可用座標，以及後續處理方式。"
            rows={2}
            value={rejectReason}
          />
          {localError && !selected ? (
            <div className={styles.errorText} data-testid="geocoder-reject-error" role="alert">{localError}</div>
          ) : null}
          <button
            className={styles.secondaryButton}
            data-testid="geocoder-record-rejection"
            onClick={handleRecordRejection}
            type="button"
          >
            記錄為無法定位
          </button>
        </div>
      ) : null}
    </section>
  );
}
