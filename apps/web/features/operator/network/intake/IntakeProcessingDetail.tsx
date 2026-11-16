"use client";

import { useEffect, useMemo, useRef, useState, type SyntheticEvent, type ReactNode } from "react";
import type {
  AssignmentReceipt, AssistedIntake, AuditReference, CanonicalIntakeDetail, CorrectionReceipt, DecisionReceipt,
  FieldValue, IntakeSubmissionReceipt, JobReceipt, MatchCaseSummary, PromotionDecisionReceipt, SlaReceipt, TransitionReceipt,
  CanonicalIntakeParserRunMetadata, CanonicalIntakeParserFields,
} from "@oday-plus/openapi-client";
import { AssistedEntryForm } from "./AssistedEntryForm";
import {
  CANONICAL_INTAKE_STAGE_LABEL, readCanonicalAssignmentTransferTargets, readCanonicalIntakeParserFields, submitCanonicalWorkflow,
  type CanonicalIntakeReadContext, type CanonicalWorkflowAction, type CanonicalWorkflowCommand, type CanonicalWorkflowReceipt,
} from "./canonicalIntakeReadClient";
import { useCanonicalIntakeCommand } from "./useCanonicalIntakeCommand";
import { CanonicalInputFields, CanonicalIntakeFailure } from "./CanonicalIdentityDecisionPanel";
import { AssignmentSlaSummary } from "./AssignmentSlaSummary";
import { DurableReceiptPanel } from "./DurableReceiptPanel";
import { EvidencePanel } from "./EvidencePanel";
import { IntakeErrorRecovery } from "./IntakeErrorRecovery";
import type { IntakeApiError } from "./intakeClient";
import { IntakeStageTimeline } from "./IntakeStageTimeline";
import { ListingCompareTable, type TargetListingData } from "./ListingCompareTable";
import { MatchEvidencePanel } from "./MatchEvidencePanel";
import {
  PromotionReviewPanel, type PromotionActor, type PromotionRequestInput, type PromotionReviewInput,
} from "./PromotionReviewPanel";
import type { ScoreReplayInput } from "./SiteScoreJobStatus";
import {
  decisionOptions, matchLabel, matchTone, policyLabel, policyTone, stageLabel, stageTone, type IntakeDecisionKind,
} from "./intakeTypes";
import styles from "./intake.module.css";

type AssistedIntakeProcessingDetailProps = {
  activeSection?: string | null;
  onSectionChange?: (section: string) => void;
  record: AssistedIntake;
  targetListing?: TargetListingData | null;
  detailHref?: string;
  busy?: boolean; canCorrect?: boolean; canDecide?: boolean; canRetry?: boolean; canReplay?: boolean;
  error?: IntakeApiError | null; history?: TransitionReceipt[]; jobs?: JobReceipt[]; sla?: SlaReceipt;
  fields?: FieldValue[]; auditReferences?: AuditReference[]; submissionReceipt?: IntakeSubmissionReceipt;
  assignmentReceipt?: AssignmentReceipt; decisionReceipt?: DecisionReceipt | PromotionDecisionReceipt;
  slaReceipt?: SlaReceipt; correctionReceipts?: CorrectionReceipt[];
  assignmentResourceVersion?: number | null; slaResourceVersion?: number | null;
  onClose: () => void;
  onAssistedEntrySave?: (input: { fields: Record<string, string>; riskSummary: string; riskAcknowledged: boolean }) => void;
  onDecide?: (kind: IntakeDecisionKind) => void; onOpenFix?: (fieldKey: string) => void;
  onRetry?: (overrides?: { overrideRetryBudget?: boolean; riskAcknowledged?: boolean }) => void;
  onReplayDlq?: (jobId?: string) => void; onCancel?: () => void; onOverride?: (reason: string) => void;
  onClaimAssignment?: () => void; onOpenTransfer?: () => void; onOpenPause?: () => void;
  onResumeSla?: () => void; onRefresh?: () => void; testId?: string;
  promotion?: PromotionDecisionReceipt | null; scoreJob?: JobReceipt | null; currentOperator?: PromotionActor;
  gateSnapshotSha256?: string; promotionBusy?: boolean; promotionError?: IntakeApiError | null;
  promotionIdempotencyReplayed?: boolean; canRequestPromotion?: boolean; canReviewPromotion?: boolean;
  canReplayScore?: boolean;
  onRequestPromotion?: (input: PromotionRequestInput) => Promise<PromotionDecisionReceipt | void> | void;
  onReviewPromotion?: (input: PromotionReviewInput) => Promise<PromotionDecisionReceipt | void> | void;
  onReplayScore?: (input: ScoreReplayInput) => Promise<JobReceipt | void> | void;
  onLookupPromotionDecision?: () => void;
};

export type IntakeProcessingDetailProps = AssistedIntakeProcessingDetailProps | {
  /** Same continuous detail composition, without inventing a legacy DTO. */
  canonical: CanonicalIntakeDetail | null;
  readContext: CanonicalIntakeReadContext;
  matchCase: MatchCaseSummary | null;
  intakeId: string;
  children: ReactNode;
  recovery?: ReactNode;
  detailHref: string;
  onClose: () => void;
  onRefresh: () => void;
  busy?: boolean;
  activeSection?: string | null;
  onSectionChange?: (section: string) => void;
  testId?: string;
};

export function IntakeProcessingDetail(props: IntakeProcessingDetailProps) {
  const native = "canonical" in props;
  const resourceId = native ? props.intakeId : props.record.id;
  const record = native ? null : props.record;
  const canonical = native ? props.canonical : null;
  const version = record?.version ?? canonical?.version;
  const outcome = record?.matchResult?.outcome;
  const { onClose, onRefresh, detailHref, testId = "intake-detail-dialog", busy = false } = props;
  const page = useRef<HTMLElement>(null);
  useEffect(() => {
    const aliases: Record<string, string> = { property: "lineage", commercial: "lineage" };
    const section = props.activeSection ? aliases[props.activeSection] ?? props.activeSection : null;
    const allowed = ["submission", "processing", "source", "error", "lineage", "identity", "comparison", "promotion", "receipts", "audit"];
    if (!section || !allowed.includes(section)) return;
    const target = page.current?.querySelector<HTMLElement>(`[data-intake-section="${section}"]`);
    target?.scrollIntoView?.({ block: "start", behavior: "instant" });
  }, [props.activeSection, resourceId]);
  const rememberSection = (event: SyntheticEvent<HTMLElement>) => {
    const target = (event.target as Element).closest?.<HTMLElement>("[data-intake-section]");
    if (target && page.current?.contains(target) && target.dataset.intakeSection !== props.activeSection) {
      props.onSectionChange?.(target.dataset.intakeSection!);
    }
  };
  return (
    <section aria-label="收件處理詳情" ref={page} onFocusCapture={rememberSection} onClickCapture={rememberSection} className={styles.intakeFullPage} data-screen-label="Intake 收件處理詳情頁" data-testid={testId}>
      <header className={styles.intakeFullPageHeader} data-testid="intake-detail-header">
        <button className={styles.secondaryButton} disabled={busy} onClick={onClose} type="button" data-testid="intake-return-button">← 返回收件匣</button>
        <div>
          <div className={styles.dialogTitle}>收件處理詳情 Intake Processing Detail</div>
          <div className={styles.intakeHeaderChips}>
            <span className={styles.rowId} data-testid="intake-detail-id">{resourceId}</span>
            {record ? <>
              <span className={styles.chip} data-testid="intake-detail-stage" data-tone={stageTone(record.stage)}>{stageLabel(record.stage)}</span>
              {outcome ? <span className={styles.chip} data-testid="intake-detail-match" data-tone={matchTone(outcome)}>{matchLabel(outcome)}</span> : null}
              <span className={styles.chip} data-tone={policyTone(record.policy)}>{policyLabel(record.policy)}</span>
            </> : canonical ? <>
              <span className={styles.chip} data-testid="intake-detail-stage">{CANONICAL_INTAKE_STAGE_LABEL[canonical.processing_state]}</span>
              {native && props.matchCase ? <span className={styles.chip} data-testid="intake-detail-match">{props.matchCase.outcome}</span> : null}
            </> : null}
            <span>{version === undefined ? "版本未取得" : "版本 v" + version}</span>
          </div>
          <a className={styles.deepLink} data-testid="intake-detail-deeplink" href={detailHref ?? "/intake/" + resourceId}>
            {detailHref ?? "/intake/" + resourceId} · 可直接開啟、重新載入與返回
          </a>
        </div>
        {onRefresh ? <button className={styles.secondaryButton} disabled={busy} onClick={onRefresh} type="button" data-testid="intake-refresh-button">重新整理</button> : null}
      </header>
      {native ? <>
        {canonical ? <NativeIntakeSections canonical={canonical} context={props.readContext} busy={busy}
          recovery={props.recovery} onLineageSelect={() => props.onSectionChange?.("lineage")}
          onRefresh={props.onRefresh} /> : props.recovery}
        {props.children}
      </> : <AssistedIntakeSections {...props} />}
    </section>
  );
}

function NativeIntakeSections({ canonical, context, recovery, busy, onLineageSelect, onRefresh }: {
  canonical: CanonicalIntakeDetail; context: CanonicalIntakeReadContext; recovery?: ReactNode;
  busy: boolean; onLineageSelect: () => void; onRefresh: () => void;
}) {
  const epoch = useRef(0);
  const frame = useMemo(() => ({ canonical, context }), [canonical, context]);
  const [selected, setSelected] = useState<{ frame: typeof frame;
    run: CanonicalIntakeParserRunMetadata; epoch: number } | null>(null);
  const current = selected?.frame === frame ? selected : null;
  const select = (run: CanonicalIntakeParserRunMetadata) => {
    setSelected({ frame, run, epoch: ++epoch.current });
    onLineageSelect();
  };
  return <>
    <section data-intake-section="submission" className={styles.sectionBox} data-testid="intake-submission-summary">
      <div className={styles.sectionHead}>送件摘要 SUBMISSION SUMMARY</div>
      <p>收件 {canonical.intake_id} · 版本 {canonical.version} · {CANONICAL_INTAKE_STAGE_LABEL[canonical.processing_state]}</p>
      <div className={styles.metaGrid}>
        <Meta label="送件方式" value={canonical.intake_method} />
        <Meta label="送件時間 Submitted At" value={canonical.submitted_at} />
        <Meta label="提交者識別碼" value={canonical.submitter_subject_id} />
        <Meta label="查詢編號 Correlation ID" value={canonical.correlation_id} />
        <Meta label="品牌識別碼" value={canonical.scope.brand_id ?? null} />
        <Meta label="區域識別碼" value={canonical.scope.region_id ?? null} />
        <Meta label="指派區域識別碼" value={canonical.scope.assigned_area_id ?? null} />
        <Meta label="HeatZone 識別碼" value={canonical.scope.heat_zone_id ?? null} />
      </div>
    </section>
    {canonical.workflow ? <NativeWorkflowControls key={`${canonical.workflow.assignment_id}:${canonical.workflow.assignment_version}:${canonical.workflow.sla_version}`}
      workflow={canonical.workflow} context={context} busy={busy} onRefresh={onRefresh} />
      : <p>尚無可讀取的指派與 SLA 資料。</p>}
    <section data-intake-section="processing" data-testid="intake-processing-stages">
      <IntakeStageTimeline canonical={canonical} context={context} />
    </section>
    <section data-intake-section="source" data-testid="intake-source-policy-evidence">
      <EvidencePanel canonical={canonical} context={context} onSelectParserRun={select}
        selectedParserRunId={current?.run.parser_run_id} busy={busy} />
    </section>
    {recovery}
    <section data-intake-section="lineage" className={styles.sectionBox} aria-label="解析與已保存欄位沿革">
      <div className={styles.sectionHead}>解析、正規化與人工修正 PARSED DATA LINEAGE</div>
      {current ? <>
        <NativeParserFields key={current.epoch} canonical={canonical} context={context} selected={current.run} />
        <button className={styles.secondaryButton} type="button" disabled={busy} onClick={() => setSelected(null)}>關閉解析欄位</button>
      </> : <p>請從上方解析紀錄選取要檢視的欄位版本。</p>}
              {canonical.resolution ? <section aria-label="已落庫物件" className={styles.noteBox}>
                <p>物件 {canonical.resolution.listing_id} · 版本 {canonical.resolution.listing_version} · {canonical.resolution.lifecycle_state}</p>
                <p>目前主體 {canonical.resolution.effective_property_id} · 版本 {canonical.resolution.property_version}</p>
                <p>建立時主體 {canonical.resolution.property_id_at_creation}</p>
                <p>目前修訂 {canonical.resolution.current_revision_id ?? "未提供"}</p>
                {canonical.resolution.fields?.length ? <>
                  <table className={styles.compareGrid} aria-label="已落庫欄位">
                    <thead><tr><th>欄位</th><th>已保存值</th><th>資料分類</th></tr></thead>
                    <tbody>{canonical.resolution.fields.map((field) => <tr key={field.field_path}>
                      <th scope="row">{field.field_path}</th>
                      <td className={styles.summaryValue}>{field.masked ? "無法檢視" : field.effective === null ? "未提供" : String(field.effective)}</td>
                      <td>{field.classification ?? "分類未提供"}</td>
                    </tr>)}</tbody>
                  </table>
                  {canonical.resolution.fields.some((field) => field.masked)
                    ? <p>部分欄位因權限或分類資料不足而遮罩。</p> : null}
                </> : <p>目前沒有可顯示的已保存欄位資料。</p>}
              </section> : null}
    </section>
  </>;
}

type NativeWorkflow = NonNullable<CanonicalIntakeDetail["workflow"]>;
type WorkflowForm = { action: "transfer" | "pause" } | null;

/** Native detail wires the shared summary to the original V1 actions.
 * One account-bound owner captures each command; retries reuse its key/body/version. */
function NativeWorkflowControls({ workflow, context, busy, onRefresh }: {
  workflow: NativeWorkflow; context: CanonicalIntakeReadContext; busy: boolean; onRefresh: () => void;
}) {
  const command = useCanonicalIntakeCommand<CanonicalWorkflowCommand, CanonicalWorkflowReceipt>(context,
    (packet, signal) => submitCanonicalWorkflow(context, packet, signal),
    (packet) => `${WORKFLOW_ACTION_LABEL[packet.action]}：${packet.resourceId}`,
    (packet) => ({ kind: "workflow", key: packet.key, action: packet.action, resourceId: packet.resourceId,
      expectedVersion: packet.expectedVersion }));
  const [form, setForm] = useState<WorkflowForm>(null);
  const [reason, setReason] = useState("");
  const [handoff, setHandoff] = useState("");
  const [resumeAt, setResumeAt] = useState("");
  const [target, setTarget] = useState("");
  const [targets, setTargets] = useState<{ status: "idle" | "loading" } | { status: "error"; error: unknown }
    | { status: "ready"; items: { subject_id: string; display_name: string; owner_role: string }[] }>({ status: "idle" });
  const locked = busy || command.busy || command.blocked || command.hasPending || Boolean(command.receipt);
  const status = workflow.assignment_status, sla = workflow.sla_state;
  const send = (action: CanonicalWorkflowAction, body: CanonicalWorkflowCommand["body"]) => {
    const sla = action === "pause" || action === "resume";
    void command.submit(() => ({ key: `canonical-workflow:${crypto.randomUUID()}`, action,
      resourceId: sla ? workflow.sla_instance_id : workflow.assignment_id,
      expectedVersion: sla ? workflow.sla_version : workflow.assignment_version, body }));
  };
  const explicit = (value: string) => value.trim().length >= 3;
  const openTransfer = () => {
    setForm({ action: "transfer" }); setTargets({ status: "loading" });
    const controller = new AbortController();
    void readCanonicalAssignmentTransferTargets(context, workflow.assignment_id, workflow.assignment_version, controller.signal).then(
      (page) => setTargets({ status: "ready", items: page.items }), (error) => setTargets({ status: "error", error }));
  };
  const selected = targets.status === "ready" ? targets.items.find((item) => item.subject_id === target) : undefined;
  return <div data-testid="native-workflow-controls">
    <AssignmentSlaSummary record={{
      owner: null, ownerSubjectId: workflow.owner_subject_id,
      assignedQueue: workflow.queue_key,
      assignmentId: workflow.assignment_id,
      assignmentStatus: workflow.assignment_status,
      slaInstanceId: workflow.sla_instance_id,
      slaState: workflow.sla_state, dueAt: workflow.due_at,
    }} assignmentResourceVersion={workflow.assignment_version}
      slaResourceVersion={workflow.sla_version} busy={locked}
      onClaim={["ASSIGNED", "TRANSFERRED", "ESCALATED"].includes(status) ? () => { setForm(null); send("claim", { reason: "認領指派的收件處理工作" }); } : undefined}
      onOpenTransfer={["ASSIGNED", "CLAIMED"].includes(status) ? openTransfer : undefined}
      onOpenPause={["ON_TRACK", "DUE_SOON", "OVERDUE"].includes(sla) ? () => setForm({ action: "pause" }) : undefined}
      onResume={sla === "PAUSED" ? () => { setForm(null); send("resume", { reason: "恢復 SLA 計時" }); } : undefined}
      onComplete={["CLAIMED", "ESCALATED"].includes(status) ? () => { setForm(null); send("complete", { reason: "完成收件處理工作" }); } : undefined} />
    {form?.action === "pause" ? <form aria-label="暫停 SLA" className={styles.sectionBox} onSubmit={(event) => {
      event.preventDefault();
      if (!explicit(reason) || !resumeAt) return;
      send("pause", { reason: reason.trim(), expected_resume_at: new Date(resumeAt).toISOString() });
    }}>
      <label>暫停原因<input data-testid="native-pause-reason" value={reason} disabled={locked} onChange={(event) => setReason(event.target.value)} /></label>
      <label>預計恢復時間<input data-testid="native-pause-resume-at" type="datetime-local" value={resumeAt} disabled={locked}
        onChange={(event) => setResumeAt(event.target.value)} /></label>
      <button className={styles.primaryButton} data-testid="native-pause-submit" disabled={locked || !explicit(reason) || !resumeAt} type="submit">送出暫停</button>
      <button className={styles.secondaryButton} disabled={locked} type="button" onClick={() => setForm(null)}>取消</button>
    </form> : null}
    {form?.action === "transfer" ? <form aria-label="轉交指派" className={styles.sectionBox} onSubmit={(event) => {
      event.preventDefault();
      if (!selected || !explicit(reason) || !explicit(handoff)) return;
      send("transfer", { target_owner_subject_id: selected.subject_id, target_owner_role: selected.owner_role,
        reason: reason.trim(), handoff_note: handoff.trim() });
    }}>
      {targets.status === "loading" ? <p role="status">正在讀取可轉交對象…</p> : targets.status === "error"
        ? <CanonicalIntakeFailure error={targets.error} /> : targets.status === "ready" ? (targets.items.length === 0
          ? <p>目前沒有可轉交的對象。</p> : <label>轉交對象<select data-testid="native-transfer-target" value={target} disabled={locked}
            onChange={(event) => setTarget(event.target.value)}>
            <option value="">請選擇</option>
            {targets.items.map((item) => <option key={item.subject_id} value={item.subject_id}>{item.display_name} · {item.owner_role}</option>)}
          </select></label>) : null}
      <label>轉交原因<input data-testid="native-transfer-reason" value={reason} disabled={locked} onChange={(event) => setReason(event.target.value)} /></label>
      <label>交接說明<input data-testid="native-transfer-handoff" value={handoff} disabled={locked} onChange={(event) => setHandoff(event.target.value)} /></label>
      <button className={styles.primaryButton} data-testid="native-transfer-submit" disabled={locked || !selected || !explicit(reason) || !explicit(handoff)} type="submit">送出轉交</button>
      <button className={styles.secondaryButton} disabled={locked} type="button" onClick={() => setForm(null)}>取消</button>
    </form> : null}
    {command.receipt ? <div role="status" className={styles.noteBox} data-testid="native-workflow-receipt">
      <p>{"assignment_id" in command.receipt ? `指派 ${command.receipt.assignment_id} · ${command.receipt.status}`
        : `SLA ${command.receipt.sla_instance_id} · ${command.receipt.state}`} · 版本 {command.receipt.version}</p>
      <p>稽核紀錄 {command.receipt.audit_event_id}；請重新讀取收件以取得最新的指派與 SLA 狀態。</p>
      <button className={styles.secondaryButton} type="button" onClick={onRefresh}>重新讀取指派與 SLA</button>
    </div> : null}
    {command.error ? <><CanonicalIntakeFailure error={command.error} />
      {command.hasPending ? <><p>結果尚未確認；重試會沿用同一請求識別、內容與版本。</p>
        <button className={styles.secondaryButton} type="button" disabled={command.busy}
          onClick={() => void command.submit(() => { throw new Error("CAPTURED_COMMAND_REQUIRED"); })}>重試同一指派／SLA 操作</button></> : null}</> : null}
    {command.blocked && !command.hasPending ? <p role="status">先前收件操作尚待確認；請先核對頁面上的原操作狀態。</p> : null}
  </div>;
}

const WORKFLOW_ACTION_LABEL: Record<CanonicalWorkflowAction, string> = {
  claim: "認領指派", transfer: "轉交指派", complete: "完成指派", pause: "暫停 SLA", resume: "恢復 SLA",
};

function NativeParserFields({ canonical, context, selected }: {
  canonical: CanonicalIntakeDetail; context: CanonicalIntakeReadContext; selected: CanonicalIntakeParserRunMetadata;
}) {
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useState<{ reload: number; status: "loading" } | { reload: number; status: "error"; error: unknown }
    | { reload: number; status: "ready"; value: CanonicalIntakeParserFields }>({ reload, status: "loading" });
  const shown = loaded.reload === reload ? loaded : { status: "loading" as const };
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setLoaded({ reload, status: "loading" });
    void readCanonicalIntakeParserFields(context, canonical, selected, controller.signal).then(
      (value) => { if (current) setLoaded({ reload, status: "ready", value }); },
      (error) => { if (current) setLoaded({ reload, status: "error", error }); },
    );
    return () => { current = false; controller.abort(); };
  }, [canonical, context, selected, reload]);
  return <div data-testid="native-parser-fields">
    <p className={styles.summaryValue}>解析紀錄 {selected.parser_run_id} · 版本 {selected.version}</p>
    {shown.status === "loading" ? <p role="status">正在讀取此解析紀錄的欄位…</p>
      : shown.status === "error" ? <CanonicalIntakeFailure error={shown.error} /> : <>
        <p>解析狀態 {shown.value.status} · 工作狀態 {shown.value.job_status} · 讀取於 {shown.value.observed_at}</p>
        <p>人工值為補錄提案；修正狀態與覆核紀錄不表示已更新物件，已保存值另列於下方。</p>
        <CanonicalInputFields fields={shown.value.fields} observations label="解析與人工修正欄位" />
      </>}
    <button className={styles.secondaryButton} disabled={shown.status === "loading"} type="button"
      onClick={() => setReload((value) => value + 1)}>重新讀取欄位狀態</button>
  </div>;
}

function AssistedIntakeSections(props: AssistedIntakeProcessingDetailProps) {
  const {
    record, targetListing, detailHref, busy = false, canCorrect = true, canDecide = true, canRetry = true, canReplay = true,
    error = null, history = [], jobs = [], sla, fields, auditReferences = [], submissionReceipt,
    assignmentReceipt, decisionReceipt, slaReceipt, correctionReceipts = [],
    assignmentResourceVersion = null, slaResourceVersion = null, onAssistedEntrySave,
    onDecide, onOpenFix, onRetry, onReplayDlq, onCancel, onOverride, onClaimAssignment, onOpenTransfer,
    onOpenPause, onResumeSla, onRefresh, promotion = null,
    scoreJob = null, currentOperator, gateSnapshotSha256, promotionBusy = false, promotionError = null,
    promotionIdempotencyReplayed = false, canRequestPromotion = false, canReviewPromotion = false,
    canReplayScore = false, onRequestPromotion, onReviewPromotion, onReplayScore, onLookupPromotionDecision,
  } = props;
  const [maskedView, setMaskedView] = useState(false);
  const options = decisionOptions(record);
  const outcome = record.matchResult?.outcome;
  const targetDecisionReady = hasAuthoritativeTargetEvidence(record, targetListing);
  const targetDependentOutcome =
    outcome === "POSSIBLE_MATCH" || outcome === "REVISION" || outcome === "EXACT_DUPLICATE";
  const decisionLocked = targetDependentOutcome && !targetDecisionReady;
  const evidenceFields = useMemo(() => fields, [fields]);
  const promotionMounted = Boolean(currentOperator && gateSnapshotSha256 !== undefined);
  const promotionAvailable = promotionMounted && (record.stage === "READY" || Boolean(promotion));
  const recordFailure =
    record.stage === "FAILED" || record.stage === "QUARANTINED"
      ? record.failure ?? null
      : null;
  const recoveryError = error ?? recordFailure;
  const hasRecoveryState =
    Boolean(recoveryError) ||
    record.stage === "FAILED" ||
    record.stage === "QUARANTINED" ||
    jobs.some((job) => job.delivery_state === "DEAD_LETTER" || job.status === "FAILED");
  const preservedInput = useMemo(() => buildPreservedInput(record), [record]);
  const providerListingId = authoritativeParsedValue(
    record,
    "providerListingId",
    "provider_listing_id",
    "sourceListingId",
    "source_listing_id",
  );
  const rawRecord = record as AssistedIntake & {
    submittedAt?: string | null;
    submitted_at?: string | null;
  };
  const submittedAt = rawRecord.submittedAt ?? rawRecord.submitted_at ?? "UNAVAILABLE";

  return (
    <>
      <section data-intake-section="submission" className={styles.sectionBox} data-testid="intake-submission-summary">
        <div className={styles.sectionHead}>送件摘要 SUBMISSION SUMMARY</div>
        <div className={styles.metaGrid}>
          <Meta label="原始 URL" value={record.originalUrl} />
          <Meta label="Canonical URL" value={record.canonicalUrl} />
          <Meta label="來源提供者 ID／政策" value={`${record.sourceId || "UNAVAILABLE"} · ${record.policyLabel || "UNAVAILABLE"}`} />
          <Meta label="提供者物件 ID" value={providerListingId} />
          <Meta label="送件時間 Submitted At" value={submittedAt} />
          <Meta label="擷取時間 Captured At" value={record.capturedAt ?? "UNAVAILABLE"} testId="intake-captured-at" />
          <Meta label="提交者" value={record.submitter || "UNAVAILABLE"} />
          <Meta label="Owner" value={record.owner || "UNAVAILABLE"} />
          <Meta label="HeatZone／區域" value={record.heatZoneId ?? "UNAVAILABLE"} />
        </div>
      </section>

      <AssignmentSlaSummary assignmentResourceVersion={assignmentResourceVersion} busy={busy}
        onClaim={onClaimAssignment} onOpenPause={onOpenPause} onOpenTransfer={onOpenTransfer}
        onResume={onResumeSla} record={record} slaResourceVersion={slaResourceVersion} />

      <section data-intake-section="processing" data-testid="intake-processing-stages">
        <IntakeStageTimeline record={record} mode="stages" onCancel={onCancel} />
      </section>

      <section data-intake-section="source" data-testid="intake-source-policy-evidence">
        <div className={styles.intakeSectionToolbar}>
          <strong>來源政策與 WORM 證據</strong>
          <label><input checked={maskedView} onChange={(event) => setMaskedView(event.target.checked)} type="checkbox" data-testid="intake-masking-checkbox" /> 資安遮蔽</label>
        </div>
        <EvidencePanel record={record} fields={evidenceFields} auditReferences={auditReferences}
          maskedView={maskedView} section="source" />
      </section>

      {hasRecoveryState ? <section data-intake-section="error" data-testid="intake-error-recovery-section">
        <IntakeErrorRecovery error={recoveryError} stage={record.stage} correlationId={record.correlationId}
          preservedInput={preservedInput} onRetry={canRetry ? onRetry : undefined} onReplayDlq={onReplayDlq}
          onCancel={onCancel} onOverride={onOverride}
          onCorrectInput={onOpenFix ? () => onOpenFix(Object.keys(record.parsedFields ?? {})[0] ?? "address_raw") : undefined} />
      </section> : null}

      {record.stage === "AWAITING_ASSISTED_ENTRY" && onAssistedEntrySave
        ? <AssistedEntryForm busy={busy} canEdit={canCorrect} onSave={onAssistedEntrySave} /> : null}

      <section data-intake-section="lineage" data-testid="intake-parsed-lineage">
        <EvidencePanel record={record} fields={evidenceFields} onOpenFix={canCorrect ? onOpenFix : undefined}
          maskedView={maskedView} section="lineage" />
      </section>

      <section data-intake-section="identity" data-testid="intake-match-evidence-section"><MatchEvidencePanel record={record} /></section>

      {record.matchResult ? (
        <section
          data-intake-section="comparison"
          className={outcome === "POSSIBLE_MATCH" ? styles.possibleMatchOutcome : styles.unambiguousMatchOutcome}
          data-match-outcome={outcome}
          data-testid="intake-comparison-decision-section"
        >
          <ListingCompareTable detailHref={detailHref} record={record} targetListing={targetListing} />
          {decisionLocked ? (
            <div className={styles.errorPanel} data-testid="intake-target-decision-lock" role="alert">
              <strong>AUTHORITATIVE_TARGET_UNAVAILABLE</strong>
              <span>精確目標物件、可比對值或命名證據尚未由 API 提供；目標相依決策已關閉。</span>
              {onRefresh ? <button className={styles.secondaryButton} onClick={onRefresh} type="button">重新整理目標資料</button> : null}
            </div>
          ) : null}
          {options.length > 0 && onDecide ? (
            <div
              className={`${styles.intakeDecisionSection} ${outcome === "POSSIBLE_MATCH" ? styles.possibleMatchDecision : ""}`}
              data-testid="intake-detail-actions"
            >
              <div>
                <strong>人工覆核決策 HUMAN DECISION</strong>
                <p>識別決策須記錄原因、前後值、操作者、時間與版本；高風險動作由後端執行第二操作者與衝突檢查。POSSIBLE_MATCH 絕不自動合併。</p>
              </div>
              <div className={styles.intakeDecisionActions}>
                {options.map((option) => (
                  <button className={option.kind === "create" ? styles.primaryButton : styles.secondaryButton}
                    data-testid={`decide-action-${option.kind}`} disabled={busy || !canDecide || decisionLocked}
                    key={option.kind} onClick={() => onDecide(option.kind)} type="button">{option.label}</button>
                ))}
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      {promotionAvailable && currentOperator ? (
        <section data-intake-section="promotion" data-testid="intake-promotion-section">
          <PromotionReviewPanel busy={promotionBusy} canReplayScore={canReplayScore} canRequest={canRequestPromotion}
            canReview={canReviewPromotion} currentOperator={currentOperator} error={promotionError}
            gateSnapshotSha256={gateSnapshotSha256 ?? ""} idempotencyReplayed={promotionIdempotencyReplayed}
            onLookupDecision={onLookupPromotionDecision} onRefresh={onRefresh} onReplayScore={onReplayScore}
            onRequestPromotion={onRequestPromotion} onReviewPromotion={onReviewPromotion} promotion={promotion}
            record={record} scoreJob={scoreJob} />
        </section>
      ) : null}

      <section data-intake-section="receipts" data-testid="intake-receipts-section">
        <DurableReceiptPanel record={record} submissionReceipt={submissionReceipt} assignmentReceipt={assignmentReceipt}
          decisionReceipt={decisionReceipt} slaReceipt={slaReceipt} correctionReceipts={correctionReceipts} />
      </section>

      <section data-intake-section="audit" data-testid="intake-timeline-audit-section">
        <IntakeStageTimeline record={record} history={history} jobs={jobs} sla={sla} canReplay={canReplay}
          mode="audit" onReplayJob={onReplayDlq} onCancel={onCancel} />
        <div className={styles.sectionBox} data-testid="intake-audit-references">
          <div className={styles.sectionHead}>稽核參照 AUDIT REFERENCES</div>
          {record.auditEvents?.length ? (
            <ul>
              {record.auditEvents.map((event) => (
                <li key={event.id}>
                  Time {event.occurredAt || "UNAVAILABLE"} · ID {event.id || "UNAVAILABLE"} · Action {event.action || "UNAVAILABLE"} · Actor {event.actorName || "UNAVAILABLE"} · Actor Role {event.actorRoleId || "UNAVAILABLE"} · Target {event.targetId || "UNAVAILABLE"} · Message {event.message || "UNAVAILABLE"} · Correlation {event.correlationId || "UNAVAILABLE"} · Event Version UNAVAILABLE · Before/After UNAVAILABLE — classified/masked audit detail contract required · Metadata UNAVAILABLE — classified/masked audit detail contract required
                </li>
              ))}
            </ul>
          ) : <div className={styles.emptyState} data-testid="audit-events-unavailable">AUDIT EVENTS: UNAVAILABLE</div>}
        </div>
        <div className={styles.sectionBox} data-testid="intake-authoritative-audit-references">
          {auditReferences.length ? auditReferences.map((reference) => (
            <div key={reference.audit_event_id}>{reference.audit_event_id} · {reference.action} · {reference.result}</div>
          )) : <div className={styles.emptyState} data-testid="audit-references-unavailable">AUDIT REFERENCES: UNAVAILABLE</div>}
        </div>
      </section>
    </>
  );
}

function Meta({ label, value, testId }: { label: string; value: string | null; testId?: string }) {
  return <div><div className={styles.metaCaption}>{label}</div><div className={styles.metaValue} data-testid={testId}>{value || "UNAVAILABLE"}</div></div>;
}

/** Same withheld-value marker the evidence panel renders for masked cells. */
const MASKED_MARKER = "•••• [MASKED]";

/**
 * Preserved input is built from the durable API-backed intake record, not from
 * parsed output alone. Retrieval can fail before parsing ever starts, so a
 * `parsedFields`-only view renders `{}` and loses the URL the operator actually
 * submitted (ADD ODP-P10-CAN-001-R3D). Submission context is therefore read
 * first, then parsed/normalized/corrected field values layered on top so an
 * operator correction still wins.
 *
 * Only values the record actually supplies are emitted: absent values stay
 * absent rather than being filled with a placeholder, and server-masked cells
 * keep the same `•••• [MASKED]` marker the evidence panel uses. No browser-only
 * copy of the submission is kept anywhere.
 */
export function buildPreservedInput(record: AssistedIntake): Record<string, unknown> | null {
  const preserved: Record<string, unknown> = {};
  const put = (key: string, value: unknown) => {
    if (value === null || value === undefined || value === "") return;
    preserved[key] = value;
  };

  // A below-CONFIDENTIAL clearance gets `originalUrl: null` plus the masking
  // flag from the API. Report it as withheld rather than silently absent.
  const masking = record as AssistedIntake & { originalUrl_masked?: boolean };
  if (masking.originalUrl_masked === true) {
    preserved.originalUrl = MASKED_MARKER;
  } else {
    put("originalUrl", record.originalUrl);
  }
  put("canonicalUrl", record.canonicalUrl);
  put("heatZoneId", record.heatZoneId);
  put("sourceId", record.sourceId);
  put("intakeMethod", record.intakeMethod);

  for (const field of Object.values(record.parsedFields ?? {})) {
    if (!field?.key) continue;
    if (field.masked === true) {
      preserved[field.key] = MASKED_MARKER;
      continue;
    }
    put(field.key, field.correctedValue ?? field.normalizedValue ?? field.sourceValue);
  }

  return Object.keys(preserved).length > 0 ? preserved : null;
}

function authoritativeParsedValue(record: AssistedIntake, ...keys: string[]): string {
  for (const key of keys) {
    const field = record.parsedFields?.[key];
    const value = field?.correctedValue ?? field?.normalizedValue ?? field?.sourceValue;
    if (value !== null && value !== undefined && value !== "") return String(value);
  }
  return "UNAVAILABLE";
}

export function hasAuthoritativeTargetEvidence(
  record: AssistedIntake,
  targetListing?: TargetListingData | null,
): boolean {
  const match = record.matchResult;
  if (!match?.targetListingId || !["POSSIBLE_MATCH", "REVISION", "EXACT_DUPLICATE"].includes(match.outcome ?? "")
    || !targetListing || targetListing.id !== match.targetListingId) {
    return false;
  }
  const hasValue = [
    targetListing.sourceId,
    targetListing.sourceListingId,
    targetListing.sourceUrl,
    targetListing.canonicalUrl,
    targetListing.address,
    targetListing.area,
    targetListing.floor,
    targetListing.listingType,
    targetListing.rent,
    targetListing.status,
  ].some((value) => value !== null && value !== undefined && value !== "");
  const hasNamedEvidence =
    (match.agreeingSignals?.length ?? 0) + (match.contradictingSignals?.length ?? 0) > 0;
  return hasValue && hasNamedEvidence;
}
