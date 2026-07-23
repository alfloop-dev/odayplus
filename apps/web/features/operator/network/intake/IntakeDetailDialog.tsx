"use client";

import { useMemo, useState, type ReactNode } from "react";
import type { AssignmentReceipt, AssistedIntake, IntakeFieldCell, SlaReceipt } from "@oday-plus/openapi-client";
import { ASSISTED_ENTRY_REQUIRED_FIELDS } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import type { IntakeApiError } from "./intakeClient";
import { ACTION_DENIED_NOTE } from "./intakePermissions";
import { DurableReceiptPanel } from "./DurableReceiptPanel";
import {
  decisionOptions,
  isIdentityField,
  matchLabel,
  matchTone,
  policyTone,
  stageLabel,
  stageSteps,
  stageTone,
  type IntakeDecisionKind,
} from "./intakeTypes";

// Inbox quick-preview dialog. The production caller sets `previewOnly`; full
// correction, comparison, decision, and promotion work belongs to the durable
// `/w/expansion/listings/intake/:intakeId` page.
//
// Owned layer  : submission summary, real-stage progress, source evidence,
//                parsed-field review, match evidence, decision actions, audit.
// Not changing : decision effects and field persistence — server-owned.

export function IntakeDetailDialog({
  busy,
  canCorrect,
  canDecide,
  canRetry,
  permissionDenials,
  error,
  onAssistedEntrySave,
  onClose,
  onDecide,
  onOpenFix,
  onRetry,
  record,
  assignmentReceipt,
  slaReceipt,
  onClaimAssignment,
  onOpenTransfer,
  onOpenPause,
  onResumeSla,
  promotionSection,
  previewOnly = false,
  onOpenFullPage,
}: {
  busy: boolean;
  canCorrect: boolean;
  canDecide: boolean;
  canRetry: boolean;
  permissionDenials?: {
    correct?: string | null;
    decide?: string | null;
    retry?: string | null;
  };
  error: IntakeApiError | null;
  onAssistedEntrySave: (input: {
    fields: Record<string, string>;
    riskSummary: string;
    riskAcknowledged: boolean;
  }) => void;
  onClose: () => void;
  onDecide: (kind: IntakeDecisionKind) => void;
  onOpenFix: (fieldKey: string) => void;
  onRetry: () => void;
  record: AssistedIntake;
  assignmentReceipt?: AssignmentReceipt;
  slaReceipt?: SlaReceipt;
  onClaimAssignment?: () => void;
  onOpenTransfer?: () => void;
  onOpenPause?: () => void;
  onResumeSla?: () => void;
  /**
   * Candidate promotion saga slice (ODP-INTAKE-UX-PROMOTION-001). The
   * container owns the API wiring and passes the fully-wired
   * PromotionReviewPanel; the dialog only decides WHERE it renders (after the
   * human decision, before the durable receipts).
   */
  promotionSection?: ReactNode;
  previewOnly?: boolean;
  onOpenFullPage?: () => void;
}) {
  const steps = stageSteps(record);
  const fields = useMemo(() => Object.values(record.parsedFields ?? {}), [record.parsedFields]);
  const options = decisionOptions(record);
  const outcome = record.matchResult?.outcome;
  const canonicalDiffers = record.originalUrl !== record.canonicalUrl;
  const isStale = isSnapshotStale(record.capturedAt);
  const decided = record.stage === "READY" && Boolean(record.matchResult) && isDecided(record);

  const showClaim = canDecide && (!record.assignmentStatus || record.assignmentStatus === "ASSIGNED") && record.stage !== "READY";
  const showMg = canDecide && record.assignmentStatus !== "COMPLETED" && record.stage !== "READY";

  if (previewOnly) {
    return (
      <IntakeDialogShell
        ariaLabel={`收件快速預覽 ${record.id}`}
        className={styles.panelWide}
        dismissible={!busy}
        onClose={onClose}
        screenLabel="Drawer 收件快速預覽"
        testId="intake-detail-preview"
      >
        <div className={styles.dialogHead}>
          <span className={styles.dialogTitle}>收件快速預覽</span>
          <span className={styles.screenBadge}>PREVIEW ONLY</span>
          <span className={styles.rowId} data-testid="intake-preview-id">
            {record.id}
          </span>
          <span className={styles.chip} data-tone={stageTone(record.stage)}>
            {record.stage} · {stageLabel(record.stage)}
          </span>
          <button
            aria-label="關閉預覽"
            className={styles.dialogClose}
            disabled={busy}
            onClick={onClose}
            type="button"
          >
            ×
          </button>
        </div>
        <div className={styles.dialogBody}>
          <div className={styles.metaGrid}>
            <Meta caption="來源" sub={record.policyLabel} value={record.sourceId} />
            <Meta caption="送件人" value={record.submitter} />
            <Meta caption="Owner" value={record.owner} />
            <Meta caption="SLA" value={translateSlaState(record.slaState)} />
            <Meta caption="更新版本" value={`v${record.version}`} />
          </div>
          <div className={styles.sectionBox}>
            <div className={styles.sectionHead}>下一步 NEXT ACTION</div>
            <div className={styles.evidenceRow}>
              此 Drawer 僅供掃描狀態與來源摘要。欄位修正、完整比對、身分決策、隔離釋放與
              Candidate Site 晉升都必須在可重載的收件頁面執行。
            </div>
          </div>
        </div>
        <div className={styles.dialogFooter}>
          <button
            className={styles.secondaryButton}
            disabled={busy}
            onClick={onClose}
            type="button"
          >
            關閉預覽
          </button>
          <button
            className={styles.primaryButton}
            data-testid="intake-preview-open-full-page"
            disabled={busy}
            onClick={onOpenFullPage}
            type="button"
          >
            開啟完整收件頁面
          </button>
        </div>
      </IntakeDialogShell>
    );
  }

  return (
    <IntakeDialogShell
      ariaLabel={`收件處理詳情 ${record.id}`}
      className={styles.panelWide}
      dismissible={!busy}
      onClose={onClose}
      screenLabel="Dialog 收件處理詳情"
      testId="intake-detail-dialog"
    >
      <div className={styles.dialogHead}>
        <span className={styles.dialogTitle}>收件處理詳情</span>
        <span className={styles.rowId} data-testid="intake-detail-id">
          {record.id}
        </span>
        <span className={styles.chip} data-testid="intake-detail-stage" data-tone={stageTone(record.stage)}>
          {stageLabel(record.stage)}
        </span>
        {outcome ? (
          <span className={styles.chip} data-testid="intake-detail-match" data-tone={matchTone(outcome)}>
            {matchLabel(outcome)}
          </span>
        ) : null}
        <span className={styles.deepLink} data-testid="intake-detail-deeplink">
          #intake/{record.id} · 狀態已保存，可離開後回來
        </span>
        <button aria-label="關閉" className={styles.dialogClose} onClick={onClose} type="button">
          ×
        </button>
      </div>

      <div className={styles.dialogBody}>
        {/* 1. Submission summary */}
        <div className={styles.metaGrid}>
          <Meta caption="來源" sub={record.policyLabel} value={record.sourceId} />
          <Meta caption="送件人" value={record.submitter} />
          <Meta caption="送出時間" value={record.capturedAt ?? "—"} />
          <Meta caption="Owner" value={record.owner} />
          <Meta caption="HeatZone" value={record.heatZoneId ?? "未指定"} />
        </div>

        {/* 指派與 SLA ASSIGNMENT */}
        <div className={styles.sectionBox} data-testid="intake-assignment-section" style={{ border: "1px solid #eef1f6", borderRadius: "10px", overflow: "hidden" }}>
          <div style={{ background: "#f8fafd", padding: "7px 12px", display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
            <span style={{ fontSize: "10px", fontWeight: 700, color: "#5a6478", letterSpacing: ".06em" }}>
              指派與 SLA ASSIGNMENT
            </span>
            <span style={{ padding: "1px 8px", borderRadius: "999px", fontSize: "9.5px", fontWeight: 700, background: "#eceffb", color: "#2e3a97" }} data-testid="intake-asg-status-badge">
              {record.assignmentStatus ? translateAsgStatus(record.assignmentStatus) : "未指派"}
            </span>
            <span style={{ padding: "1px 8px", borderRadius: "999px", fontSize: "9.5px", fontWeight: 700, background: getSlaBg(record.slaState), color: getSlaFg(record.slaState) }} data-testid="intake-sla-status-badge">
              {translateSlaState(record.slaState)}
            </span>

            <span style={{ marginLeft: "auto", display: "flex", gap: "6px" }}>
              {showClaim ? (
                <button
                  onClick={onClaimAssignment}
                  className={styles.primaryButton}
                  style={{ padding: "4px 12px", fontSize: "10.5px" }}
                  data-testid="intake-claim-button"
                  type="button"
                >
                  認領此收件
                </button>
              ) : null}

              {showMg ? (
                <>
                  <button
                    onClick={onOpenTransfer}
                    className={styles.secondaryButton}
                    style={{ padding: "4px 11px", fontSize: "10px" }}
                    data-testid="intake-transfer-button"
                    type="button"
                  >
                    轉交
                  </button>
                  {record.slaState === "PAUSED" ? (
                    <button
                      onClick={onResumeSla}
                      className={styles.secondaryButton}
                      style={{ padding: "4px 11px", fontSize: "10px" }}
                      data-testid="intake-resume-button"
                      type="button"
                    >
                      恢復 SLA
                    </button>
                  ) : (
                    <button
                      onClick={onOpenPause}
                      className={styles.secondaryButton}
                      style={{ padding: "4px 11px", fontSize: "10px" }}
                      data-testid="intake-pause-button"
                      type="button"
                    >
                      暫停 SLA
                    </button>
                  )}
                </>
              ) : null}
            </span>
          </div>

          <div style={{ padding: "7px 12px", display: "flex", gap: "16px", flexWrap: "wrap", fontSize: "10.5px", color: "#3a4362" }}>
            <span>佇列：<b>Triage Queue</b></span>
            <span>認領人：<b>{record.owner || "—"}</b></span>
            <span>到期時間：<b>{record.dueAt ? new Date(record.dueAt).toLocaleString() : "—"}</b></span>
          </div>

          {record.slaReceipt ? (
            <div style={{ padding: "0 12px 8px", fontSize: "10px", color: "#1e7f4f", fontFamily: "monospace" }} data-testid="intake-sla-receipt">
              receipt {record.slaReceipt}
            </div>
          ) : null}
        </div>

        {/* 2. Processing status — real stages, never a fabricated percentage */}
        <div>
          <div className={styles.sectionLabel}>處理狀態（實際階段，非百分比）</div>
          <div className={styles.stepper} data-testid="intake-stage-stepper" role="list">
            {steps.map((step, index) => (
              <div className={styles.step} data-state={step.state} key={step.code} role="listitem">
                <span aria-hidden="true" className={styles.stepMark}>
                  {step.mark}
                </span>
                <span className={styles.stepText}>
                  <span className={styles.stepName}>{step.label}</span>
                  <span className={styles.stepCode}>{step.code}</span>
                </span>
                {index < steps.length - 1 ? (
                  <span aria-hidden="true" className={styles.stepArrow}>
                    →
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        </div>

        {/* Policy state — explains WHY retrieval did or did not happen */}
        <div className={styles.sectionBox}>
          <div className={styles.sectionHead}>
            來源政策 SOURCE POLICY
            <span className={styles.chip} data-testid="intake-policy-chip" data-tone={policyTone(record.policy)}>
              {record.policyLabel}
            </span>
          </div>
          <div className={styles.evidenceRow}>
            <span className={styles.evidenceKey}>政策原因</span>
            <span className={styles.evidenceValue} data-testid="intake-policy-reason">
              {record.policyReason}
            </span>
          </div>
        </div>

        {/* 3. Source evidence */}
        <div className={styles.sectionBox}>
          <div className={styles.sectionHead}>來源證據 SOURCE EVIDENCE</div>
          <Evidence label="原始 URL">
            <a
              href={record.originalUrl}
              rel="noreferrer noopener"
              target="_blank"
              title={`在新分頁開啟來源頁（${hostOf(record.originalUrl)}）— 不影響本收件狀態`}
            >
              {record.originalUrl}
            </a>
          </Evidence>
          {canonicalDiffers ? (
            <Evidence label="Canonical URL">
              <span data-testid="intake-canonical-url">
                {record.canonicalUrl}（追蹤參數已正規化）
              </span>
            </Evidence>
          ) : null}
          <Evidence label="擷取時間">
            <span data-testid="intake-captured-at">
              {record.capturedAt ?? "—（未擷取）"}
              {isStale ? (
                <span className={styles.chip} data-testid="intake-stale-chip" data-tone="watch">
                  ⚠ 快照可能過期
                </span>
              ) : null}
            </span>
          </Evidence>
          <Evidence label="Parser／快照">
            <span>
              {record.parserVersion} · {record.snapshotId ?? "—"}
            </span>
          </Evidence>
          <Evidence label="Correlation ID">
            <span data-testid="intake-correlation-id">{record.correlationId ?? "—"}</span>
          </Evidence>
        </div>

        {/* 5. Failure — retryable and non-retryable variants */}
        {record.failure ? (
          <div className={styles.errorPanel} data-testid="intake-failure-panel" role="alert">
            <span className={styles.errorSummary}>{record.failure.summary}</span>
            <span className={styles.errorMeta}>
              錯誤碼 {record.failure.code} · correlation {record.correlationId ?? "—"} ·{" "}
              {record.failure.retryable ? "可重試" : "不可重試"}
            </span>
            <span className={styles.errorNext}>下一步：{record.failure.nextAction}</span>
          </div>
        ) : null}

        {/* Action-level API error */}
        {error ? (
          <div className={styles.errorPanel} data-testid="intake-detail-error" role="alert">
            <span className={styles.errorSummary}>{error.summary}</span>
            <span className={styles.errorMeta}>
              錯誤碼 {error.code}
              {error.correlationId ? ` · correlation ${error.correlationId}` : ""} · 發生於{" "}
              {error.occurredAt}
            </span>
            <span className={styles.errorNext}>下一步：{error.nextAction}</span>
          </div>
        ) : null}

        {/* 6. Assisted entry — the fallback when the source may not be fetched */}
        {record.stage === "AWAITING_ASSISTED_ENTRY" ? (
          <AssistedEntryForm busy={busy} canEdit={canCorrect} onSave={onAssistedEntrySave} />
        ) : null}

        {/* 4. Parsed listing preview: source vs normalized vs corrected */}
        {fields.length > 0 ? (
          <div className={styles.sectionBox}>
            <div className={styles.sectionHead}>
              解析資料覆核 PARSED DATA REVIEW
              <span className={styles.sectionHeadHint}>
                來源值 → 正規化值 → 人工修正；識別欄位修正需填原因
              </span>
            </div>
            <div className={styles.fieldsGrid} data-testid="intake-fields-grid">
              <div className={styles.fieldsHeadCell}>欄位</div>
              <div className={styles.fieldsHeadCell}>來源值</div>
              <div className={styles.fieldsHeadCell}>正規化值</div>
              <div className={styles.fieldsHeadCell}>人工修正</div>
              <div className={styles.fieldsHeadCell} />
              {fields.map((field) => (
                <FieldRow
                  canCorrect={canCorrect}
                  field={field}
                  key={field.key}
                  onOpenFix={onOpenFix}
                  stage={record.stage}
                />
              ))}
            </div>
          </div>
        ) : null}

        {/* 5. Match result + evidence */}
        {record.matchResult ? (
          <MatchReview record={record} />
        ) : null}

        {/* 7. Human decision */}
        {options.length > 0 && !decided ? (
          <div>
            <div className={styles.sectionLabel}>人工決策 HUMAN DECISION</div>
            {outcome === "POSSIBLE_MATCH" ? (
              <div className={styles.warnNote} data-testid="intake-no-auto-note">
                系統不會自動合併疑似重複 — 必須由人工決策，且決策原因必填。
              </div>
            ) : null}
            {!canDecide ? (
              <div className={styles.warnNote} data-testid="intake-decide-denied">
                {ACTION_DENIED_NOTE.decide}
                {permissionDenials?.decide ? (
                  <> 後端拒絕代碼：<code>{permissionDenials.decide}</code></>
                ) : null}
              </div>
            ) : null}
            <div className={styles.actionRow} data-testid="intake-decision-actions">
              {options.map((option) => (
                <button
                  className={option.primary ? styles.primaryButton : styles.secondaryButton}
                  data-testid={`intake-decide-${option.kind}`}
                  disabled={!canDecide || busy}
                  key={option.kind}
                  onClick={() => onDecide(option.kind)}
                  type="button"
                >
                  {option.label}
                </button>
              ))}
              {record.failure?.retryable ? (
                <button
                  className={styles.secondaryButton}
                  data-testid="intake-retry-button"
                  disabled={!canRetry || busy}
                  onClick={onRetry}
                  type="button"
                >
                  {busy ? "重試中…" : "重試擷取"}
                </button>
              ) : null}
            </div>
          </div>
        ) : null}

        {/* 8. Candidate promotion saga (UX-SCR-EXP-003F) */}
        {promotionSection}

        {/* Durable Receipts Panel */}
        <DurableReceiptPanel
          record={record}
          assignmentReceipt={assignmentReceipt}
          slaReceipt={slaReceipt}
        />

        {/* 7. Timeline and audit history */}
        <div className={styles.timeline} data-testid="intake-timeline">
          <div className={styles.sectionLabel}>時間軸與稽核 TIMELINE</div>
          {(record.auditEvents ?? []).length === 0 ? (
            <div className={styles.emptyState}>尚無稽核事件。</div>
          ) : (
            [...(record.auditEvents ?? [])].reverse().map((event) => (
              <div className={styles.timelineRow} key={event.id}>
                <span className={styles.timelineTime}>{event.occurredAt}</span>
                <span className={styles.timelineText}>
                  {event.actorName}（{event.actorRoleId}）· {event.message}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </IntakeDialogShell>
  );
}

function MatchReview({ record }: { record: AssistedIntake }) {
  const match = record.matchResult;
  if (!match) return null;
  const rows = buildCompareRows(match.agreeingSignals, match.contradictingSignals);

  return (
    <div className={styles.sectionBox}>
      <div className={styles.sectionHead}>
        比對結果 MATCH REVIEW
        {match.targetListingId ? <span>對象：{match.targetListingId}</span> : null}
        <span className={styles.sectionHeadHint}>信心 {match.confidence.toFixed(2)}</span>
      </div>

      {/* Screen-reader-readable change summary (§9) */}
      <div className={styles.srSummary} data-testid="intake-change-summary">
        變更摘要（供螢幕閱讀器）：{match.summary}
      </div>

      <div className={styles.desktopOnlyNote} data-testid="intake-desktop-required">
        並列比對需要較寬的畫面 — 請改用桌機完成疑似重複的比對與決策。以下僅列出比對訊號摘要。
      </div>

      <div className={`${styles.compareGrid} ${styles.compareHide}`} data-testid="intake-compare-grid">
        <div className={styles.fieldsHeadCell}>訊號</div>
        <div className={styles.fieldsHeadCell}>判定</div>
        <div className={styles.fieldsHeadCell}>說明</div>
        {rows.map((row) => (
          <div
            className={row.changed ? styles.compareRowChanged : undefined}
            key={row.key}
            style={{ display: "contents" }}
          >
            <div className={styles.compareCell} data-label="訊號">
              {row.label}
            </div>
            <div className={styles.compareCell} data-label="判定">
              {row.changed ? (
                <span className={styles.changeChip}>▲ 矛盾</span>
              ) : (
                <span className={styles.chip} data-tone="good">
                  ✓ 一致
                </span>
              )}
            </div>
            <div className={styles.compareCell} data-label="說明">
              {row.detail}
            </div>
          </div>
        ))}
      </div>

      <div className={styles.signals}>
        <div className={styles.signalCol}>
          <div className={styles.signalHeadAgree}>一致訊號</div>
          {match.agreeingSignals.length === 0 ? (
            <div className={styles.signalItem}>—</div>
          ) : (
            match.agreeingSignals.map((signal) => (
              <div className={styles.signalItem} key={signal.key}>
                ✓ {signal.label}：{signal.detail}
              </div>
            ))
          )}
        </div>
        {match.contradictingSignals.length > 0 ? (
          <div className={styles.signalCol}>
            <div className={styles.signalHeadCon}>矛盾訊號</div>
            {match.contradictingSignals.map((signal) => (
              <div className={styles.signalItem} key={signal.key}>
                ✕ {signal.label}：{signal.detail}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function buildCompareRows(
  agreeing: AssistedIntake["matchResult"] extends null ? never : NonNullable<AssistedIntake["matchResult"]>["agreeingSignals"],
  contradicting: NonNullable<AssistedIntake["matchResult"]>["contradictingSignals"],
) {
  return [
    ...agreeing.map((signal) => ({
      key: `agree-${signal.key}`,
      label: signal.label,
      detail: signal.detail,
      changed: false,
    })),
    ...contradicting.map((signal) => ({
      key: `con-${signal.key}`,
      label: signal.label,
      detail: signal.detail,
      changed: true,
    })),
  ];
}

function FieldRow({
  canCorrect,
  field,
  onOpenFix,
  stage,
}: {
  canCorrect: boolean;
  field: IntakeFieldCell;
  onOpenFix: (fieldKey: string) => void;
  stage: AssistedIntake["stage"];
}) {
  const fixable = stage === "READY" || stage === "NEEDS_REVIEW" || stage === "AWAITING_ASSISTED_ENTRY";
  const corrected = field.correctedValue !== null && field.correctedValue !== undefined && field.correctedValue !== "";
  const masked = field.masked === true;
  const maskedValue = (
    <span className={styles.maskedValue} data-testid={`intake-masked-${field.key}`}>
      已遮罩 · {field.mask_reason_code ?? "FIELD_MASKED"}
    </span>
  );

  return (
    <>
      <div className={styles.fieldCell} data-label="欄位">
        <span>{field.label}</span>
        {isIdentityField(field.key) ? <span className={styles.identityMark}>識別欄位</span> : null}
      </div>
      <div className={`${styles.fieldCell} ${styles.sourceValue}`} data-label="來源值">
        {masked ? maskedValue : display(field.sourceValue)}
      </div>
      <div className={styles.fieldCell} data-label="正規化值">
        <span className={styles.normalizedValue}>{masked ? maskedValue : display(field.normalizedValue)}</span>
        {field.lowConfidence && !corrected ? (
          <span className={styles.lowChip} data-testid={`intake-low-${field.key}`}>
            ⚠ 低信心
          </span>
        ) : null}
      </div>
      <div className={styles.fieldCell} data-label="人工修正">
        <span className={corrected ? styles.correctedValue : styles.correctedEmpty}>
          {masked ? maskedValue : corrected ? display(field.correctedValue) : "—"}
        </span>
        {field.correctionReason ? (
          <span className={styles.metaSub}>原因：{field.correctionReason}</span>
        ) : null}
      </div>
      <div>
        {fixable ? (
          <button
            className={styles.fixButton}
            data-testid={`intake-fix-${field.key}`}
            disabled={!canCorrect || masked}
            onClick={() => onOpenFix(field.key)}
                  title={masked ? "此欄位已依資料分級遮罩，無法在目前權限下修正。" : canCorrect ? undefined : ACTION_DENIED_NOTE.correct}
            type="button"
          >
            修正
          </button>
        ) : null}
      </div>
    </>
  );
}

function AssistedEntryForm({
  busy,
  canEdit,
  onSave,
}: {
  busy: boolean;
  canEdit: boolean;
  onSave: (input: {
    fields: Record<string, string>;
    riskSummary: string;
    riskAcknowledged: boolean;
  }) => void;
}) {
  const [address, setAddress] = useState("");
  const [rent, setRent] = useState("");
  const [areaPing, setAreaPing] = useState("");
  const [floor, setFloor] = useState("");
  const [listingType, setListingType] = useState("");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  // Assisted entry is hand-keyed from a source this system is not approved to
  // fetch, so the values carry no retrieved evidence — that is the risk being
  // disclosed, and it is why the operator must acknowledge it.
  const riskSummary =
    `人工補錄地址／租金／坪數等欄位，內容由操作者依來源頁自行輸入，` +
    `不具本系統擷取的來源證據，且將作為後續比對與網絡評估的依據。前後值會寫入 Audit。`;

  function handleSave() {
    if (busy) return;
    if (!address.trim() || !rent.trim() || !areaPing.trim()) {
      setLocalError("人工補錄需至少填寫地址、租金、坪數。");
      return;
    }
    if (!riskAcknowledged) {
      setLocalError("請先確認你已了解人工補錄的風險。");
      return;
    }
    setLocalError(null);
    const fields: Record<string, string> = {
      address: address.trim(),
      rent: rent.trim(),
      areaPing: areaPing.trim(),
    };
    if (floor.trim()) fields.floor = floor.trim();
    if (listingType.trim()) fields.listingType = listingType.trim();
    onSave({ fields, riskSummary, riskAcknowledged });
  }

  return (
    <div className={styles.sectionBox} data-testid="intake-assisted-entry">
      <div className={styles.sectionHead}>
        人工補錄 ASSISTED ENTRY — 此來源不擷取，請依來源頁內容輸入必要欄位（URL 已保留為佐證）
      </div>
      <div className={styles.dialogBody}>
        <div>
          <label className={styles.fieldLabel} htmlFor="assisted-address">
            地址（必填）
          </label>
          <input
            className={styles.input}
            data-testid="assisted-address"
            disabled={!canEdit}
            id="assisted-address"
            onChange={(event) => setAddress(event.target.value)}
            placeholder="例：新北市新莊區興德路 XX 號 1F"
            value={address}
          />
        </div>
        <div className={styles.grid2}>
          <div>
            <label className={styles.fieldLabel} htmlFor="assisted-rent">
              租金（必填）
            </label>
            <input
              className={`${styles.input} ${styles.mono}`}
              data-testid="assisted-rent"
              disabled={!canEdit}
              id="assisted-rent"
              onChange={(event) => setRent(event.target.value)}
              placeholder="45000"
              value={rent}
            />
          </div>
          <div>
            <label className={styles.fieldLabel} htmlFor="assisted-area">
              坪數（必填）
            </label>
            <input
              className={`${styles.input} ${styles.mono}`}
              data-testid="assisted-area"
              disabled={!canEdit}
              id="assisted-area"
              onChange={(event) => setAreaPing(event.target.value)}
              placeholder="18"
              value={areaPing}
            />
          </div>
        </div>
        <div className={styles.grid2}>
          <div>
            <label className={styles.fieldLabel} htmlFor="assisted-floor">
              樓層
            </label>
            <input
              className={styles.input}
              data-testid="assisted-floor"
              disabled={!canEdit}
              id="assisted-floor"
              onChange={(event) => setFloor(event.target.value)}
              placeholder="1F"
              value={floor}
            />
          </div>
          <div>
            <label className={styles.fieldLabel} htmlFor="assisted-type">
              型態／用途
            </label>
            <input
              className={styles.input}
              data-testid="assisted-type"
              disabled={!canEdit}
              id="assisted-type"
              onChange={(event) => setListingType(event.target.value)}
              placeholder="店面"
              value={listingType}
            />
          </div>
        </div>

        <div className={styles.sectionBox}>
          <div className={styles.sectionHead}>風險摘要 RISK SUMMARY</div>
          <div className={styles.riskSummaryText} data-testid="intake-assisted-risk-summary">
            {riskSummary}
          </div>
          <label className={styles.checkboxRow} htmlFor="assisted-risk-ack">
            <input
              checked={riskAcknowledged}
              data-testid="assisted-risk-ack"
              disabled={!canEdit}
              id="assisted-risk-ack"
              onChange={(event) => setRiskAcknowledged(event.target.checked)}
              type="checkbox"
            />
            <span>我已閱讀並了解上述風險，確認補錄內容正確（將連同此摘要寫入 Audit）</span>
          </label>
        </div>

        {localError ? (
          <div className={styles.errorText} data-testid="intake-assisted-error" role="alert">
            {localError}
          </div>
        ) : null}
        {!canEdit ? (
          <div className={styles.warnNote}>{ACTION_DENIED_NOTE.correct}</div>
        ) : null}

        <div>
          <button
            className={styles.primaryButton}
            data-testid="assisted-save"
            disabled={!canEdit || busy}
            onClick={handleSave}
            type="button"
          >
            {busy ? "儲存中…" : "儲存並進入比對"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Meta({ caption, sub, value }: { caption: string; sub?: string; value: string }) {
  return (
    <div>
      <div className={styles.metaCaption}>{caption}</div>
      <div className={styles.metaValue}>{value}</div>
      {sub ? <div className={styles.metaSub}>{sub}</div> : null}
    </div>
  );
}

function Evidence({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <div className={styles.evidenceRow}>
      <span className={styles.evidenceKey}>{label}</span>
      <span className={styles.evidenceValue}>{children}</span>
    </div>
  );
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** A snapshot older than 24h is stale for expansion review purposes. */
export function isSnapshotStale(capturedAt: string | null): boolean {
  if (!capturedAt) return false;
  const captured = Date.parse(capturedAt);
  if (Number.isNaN(captured)) return false;
  return Date.now() - captured > 24 * 60 * 60 * 1000;
}

/** A record whose audit trail already carries a decide event is closed out. */
function isDecided(record: AssistedIntake): boolean {
  return (record.auditEvents ?? []).some((event) => event.action.startsWith("intake.decide"));
}

function translateAsgStatus(status?: string | null): string {
  if (!status) return "未指派";
  const mapping: Record<string, string> = {
    ASSIGNED: "已指派",
    CLAIMED: "已認領",
    TRANSFERRED: "已轉交",
    ESCALATED: "已升級",
    COMPLETED: "已完成",
  };
  return mapping[status] || status;
}

function translateSlaState(state?: string | null): string {
  if (!state) return "● ON_TRACK 進度內";
  const mapping: Record<string, string> = {
    ON_TRACK: "● ON_TRACK 進度內",
    DUE_SOON: "▲ DUE_SOON 將到期",
    OVERDUE: "▲ OVERDUE 已逾期",
    BREACHED: "✕ BREACHED 已違約",
    PAUSED: "∥ PAUSED 暫停",
    COMPLETED: "✓ COMPLETED",
  };
  return mapping[state] || state;
}

function getSlaBg(state?: string | null): string {
  if (!state) return "#E5F3EA";
  const mapping: Record<string, string> = {
    ON_TRACK: "#E5F3EA",
    DUE_SOON: "#FBF1DD",
    OVERDUE: "#FBE9E7",
    BREACHED: "#FBE9E7",
    PAUSED: "#EEF1F5",
    COMPLETED: "#E5F3EA",
  };
  return mapping[state] || "#EEF1F5";
}

function getSlaFg(state?: string | null): string {
  if (!state) return "#166534";
  const mapping: Record<string, string> = {
    ON_TRACK: "#166534",
    DUE_SOON: "#854D0E",
    OVERDUE: "#B3261E",
    BREACHED: "#B3261E",
    PAUSED: "#5A6472",
    COMPLETED: "#166534",
  };
  return mapping[state] || "#5A6472";
}
