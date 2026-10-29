"use client";

import { useEffect, useRef, useState } from "react";
import type {
  AssistedIntake,
  CanonicalIntakeDetail,
  CanonicalIntakeHistoryPage,
  JobReceipt,
  SlaReceipt,
  TransitionReceipt,
} from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { stageLabel, stageSteps, stageTone } from "./intakeTypes";
import { CANONICAL_INTAKE_STAGE_LABEL, readCanonicalIntakeHistory, type CanonicalIntakeReadContext } from "./canonicalIntakeReadClient";
import { CanonicalIntakeFailure } from "./CanonicalIdentityDecisionPanel";

type AssistedTimelineProps = {
  record: AssistedIntake;
  history?: TransitionReceipt[];
  jobs?: JobReceipt[];
  sla?: SlaReceipt;
  canReplay?: boolean;
  onReplayJob?: (jobId: string) => void;
  onCancel?: () => void;
  testId?: string;
  mode?: "stages" | "audit";
};

type NativeTimelineProps = { canonical: CanonicalIntakeDetail; context: CanonicalIntakeReadContext };
export type IntakeStageTimelineProps = AssistedTimelineProps | NativeTimelineProps;

export function IntakeStageTimeline(props: IntakeStageTimelineProps) {
  return "canonical" in props ? <NativeTimeline {...props} /> : <AssistedTimeline {...props} />;
}

function NativeTimeline({ canonical, context }: NativeTimelineProps) {
  const initial = canonical.processing_history ?? null;
  const [selection, setSelection] = useState<{ context: CanonicalIntakeReadContext; canonical: CanonicalIntakeDetail; cursor: string | null }>(
    { context, canonical, cursor: null });
  const cursor = selection.context === context && selection.canonical === canonical ? selection.cursor : null;
  const [read, setRead] = useState<{ context: CanonicalIntakeReadContext; canonical: CanonicalIntakeDetail;
    cursor: string | null; page: CanonicalIntakeHistoryPage | null; busy: boolean; error: unknown | null }>(
    { context, canonical, cursor: null, page: initial, busy: false, error: null });
  const latest = useRef({ context, canonical, cursor });
  latest.current = { context, canonical, cursor };
  useEffect(() => {
    const controller = new AbortController();
    const current = () => !controller.signal.aborted && latest.current.context === context
      && latest.current.canonical === canonical && latest.current.cursor === cursor;
    if (cursor !== null && initial) {
      setRead({ context, canonical, cursor, page: null, busy: true, error: null });
      void readCanonicalIntakeHistory(context, canonical, cursor, initial.through_sequence, controller.signal).then(
        (page) => { if (current()) setRead({ context, canonical, cursor, page, busy: false, error: null }); },
        (error) => { if (current()) setRead({ context, canonical, cursor, page: null, busy: false, error }); },
      );
    }
    return () => controller.abort();
  }, [context, canonical, cursor, initial]);
  const bound = read.context === context && read.canonical === canonical && read.cursor === cursor;
  const page = cursor === null ? initial : bound ? read.page : null;
  const busy = cursor !== null && (!bound || read.busy);
  return <div className={styles.sectionBox} data-testid="native-intake-stage-timeline">
    <div className={styles.timelineHeader}>
      <h4>階段時序與執行歷程 STAGE TIMELINE</h4>
      <span className={styles.chip}>{CANONICAL_INTAKE_STAGE_LABEL[canonical.processing_state]}</span>
    </div>
    <p>目前版本 {canonical.version} · 歷程只顯示實際記錄的轉移。</p>
    {busy ? <p role="status">正在讀取階段歷程…</p>
      : cursor !== null && bound && read.error ? <CanonicalIntakeFailure error={read.error} />
      : page ? <>
        <p>歷程截點：序號 {page.through_sequence} · 收件版本 {page.intake_version}</p>
        {page.items.length ? <div style={{ overflowX: "auto" }}>
          <table className={styles.compareGrid} aria-label="原生收件實際階段歷程">
            <thead><tr><th>次序／時間</th><th>實際轉移</th><th>執行者</th><th>版本／原因</th><th>記錄參考</th></tr></thead>
            <tbody>{page.items.map((row) => <tr key={row.transition_id}>
              <td>#{row.sequence_no}<p>{row.occurred_at}</p></td>
              <td>{row.from_state === null ? "建立" : CANONICAL_INTAKE_STAGE_LABEL[row.from_state]} → {CANONICAL_INTAKE_STAGE_LABEL[row.to_state]}</td>
              <td className={styles.summaryValue}>{row.actor_subject_id ? "人員 " + row.actor_subject_id
                : row.service_principal ? "服務 " + row.service_principal : "未記錄執行者"}</td>
              <td>{row.expected_version ?? "未記錄"} → {row.resulting_version}<p>{row.reason_code ?? "未記錄原因代碼"}</p><p>{row.permission}</p></td>
              <td className={styles.summaryValue}>
                <p>轉移 {row.transition_id}</p><p>查詢 {row.correlation_id}</p>
                {row.job_id ? <p>工作 {row.job_id}</p> : null}
                {row.source_policy_version !== null ? <p>來源政策版本 {row.source_policy_version}</p> : null}
                {row.source_snapshot_id ? <p>快照 {row.source_snapshot_id}</p> : null}
                {row.parser_release_id ? <p>解析版本 {row.parser_release_id}</p> : null}
                {row.match_case_id ? <p>案件 {row.match_case_id}</p> : null}
              </td>
            </tr>)}</tbody>
          </table>
        </div> : <p>尚無已記錄的階段轉移；目前狀態以詳情讀取為準。</p>}
        <div className={styles.timelineActions}>
          <button className={styles.secondaryButton} disabled={!page.previous_cursor} type="button"
            onClick={() => setSelection({ context, canonical, cursor: page.previous_cursor })}>較早的階段歷程</button>
          <button className={styles.secondaryButton} disabled={!page.next_cursor} type="button"
            onClick={() => setSelection({ context, canonical, cursor: page.next_cursor })}>較後的階段歷程</button>
        </div>
      </> : <p>尚未取得處理歷程，請重新整理詳情。</p>}
  </div>;
}

function AssistedTimeline({
  record,
  history = [],
  jobs = [],
  sla,
  canReplay = false,
  onReplayJob,
  onCancel,
  testId = "intake-stage-timeline",
  mode = "stages",
}: AssistedTimelineProps) {
  const steps = stageSteps(record);
  const activeJob = jobs.find((j) => j.status === "RUNNING" || j.delivery_state === "RETRYING" || j.delivery_state === "DEAD_LETTER") ?? jobs[0];
  const isDlq = activeJob?.delivery_state === "DEAD_LETTER" || record.stage === "FAILED";
  const jobBadge = jobStatusBadgeColors(activeJob?.status, activeJob?.delivery_state);
  const isCancelled = (record.stage as string) === "CANCELLED";

  return (
    <div className={styles.sectionBox} data-testid={testId} style={{ border: "1px solid #eef1f6", borderRadius: "10px", padding: "14px", background: "#ffffff", marginBottom: "16px" }}>
      <div className={styles.timelineHeader}>
        <h4 style={{ margin: 0, fontSize: "13px", fontWeight: 700, color: "#1e293b", display: "flex", alignItems: "center", gap: "8px" }}>
          <span>階段時序與執行歷程 STAGE TIMELINE</span>
          <span className={styles.chip} data-tone={stageTone(record.stage)}>
            {stageLabel(record.stage)}
          </span>
        </h4>
        <div className={styles.timelineActions}>
          {onCancel && !isCancelled && record.stage !== "READY" && (
            <button
              type="button"
              onClick={onCancel}
              className={styles.secondaryButton}
              style={{ color: "#b3261e" }}
              data-testid="timeline-cancel-button"
            >
              取消流程 (Cancel Intake)
            </button>
          )}
        </div>
      </div>

      {/* 1. Stepper without fake percentages */}
      {mode === "stages" ? <div
        data-testid="timeline-stepper"
        role="region"
        aria-label="收件處理階段"
        tabIndex={0}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          overflowX: "auto",
          paddingBottom: "10px",
          marginBottom: "14px",
          borderBottom: "1px solid #f1f5f9",
        }}
      >
        {steps.map((step, idx) => {
          const isCurrent = step.state === "current";
          const isDone = step.state === "done";
          const isFailed = step.state === "failed";

          let bg = "#f1f5f9";
          let fg = "#64748b";
          let borderColor = "#cbd5e1";

          if (isDone) {
            bg = "#e5f3ea";
            fg = "#166534";
            borderColor = "#a7f3d0";
          } else if (isFailed) {
            bg = "#fbe9e7";
            fg = "#b3261e";
            borderColor = "#fca5a5";
          } else if (isCurrent) {
            bg = "#eceffb";
            fg = "#2e3a97";
            borderColor = "#2e3a97";
          }

          return (
            <div
              key={step.code}
              data-testid={`timeline-step-${step.code}`}
              data-state={step.state}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "6px",
                flexShrink: 0,
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  padding: "5px 10px",
                  borderRadius: "6px",
                  background: bg,
                  border: `1px solid ${borderColor}`,
                  fontSize: "11px",
                  fontWeight: isCurrent ? 700 : 500,
                  color: fg,
                }}
              >
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: "18px",
                    height: "18px",
                    borderRadius: "50%",
                    background: fg,
                    color: "#ffffff",
                    fontSize: "10px",
                    fontWeight: 700,
                  }}
                >
                  {step.mark}
                </span>
                <span>{step.label}</span>
              </div>
              {idx < steps.length - 1 && (
                <span style={{ color: "#cbd5e1", fontSize: "12px", padding: "0 2px" }}>→</span>
              )}
            </div>
          );
        })}
      </div> : null}

      {/* 2. SLA & Assignment Metadata */}
      {mode === "audit" && (
        <div
          data-testid="timeline-sla-panel"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: "10px",
            background: "#f8fafc",
            padding: "10px 12px",
            borderRadius: "8px",
            marginBottom: "12px",
            fontSize: "11px",
          }}
        >
          <div>
            <span style={{ color: "#64748b" }}>SLA 狀態: </span>
            <span
              style={{
                fontWeight: 700,
                color: (sla?.state ?? record.slaState) === "BREACHED" || (sla?.state ?? record.slaState) === "OVERDUE" ? "#b3261e" : "#166534",
              }}
            >
              {sla?.state ?? record.slaState ?? "UNAVAILABLE"}
            </span>
          </div>
          <div>
            <span style={{ color: "#64748b" }}>SLA 到期時間: </span>
            <span style={{ fontFamily: "monospace", fontWeight: 600 }}>{sla?.due_at ?? record.dueAt ?? "UNAVAILABLE"}</span>
          </div>
          <div>
            <span style={{ color: "#64748b" }}>暫停時長: </span>
            <span>{sla?.paused_duration_seconds === null || sla?.paused_duration_seconds === undefined ? "UNAVAILABLE" : `${Math.round(sla.paused_duration_seconds / 60)} 分鐘`}</span>
          </div>
          <div data-testid="timeline-sla-receipt-state">
            SLA RECEIPT: {sla ? "AVAILABLE" : "UNAVAILABLE"}
          </div>
        </div>
      )}

      {/* 3. Job Execution & DLQ Status */}
      {mode === "audit" && (activeJob ? (
        <div
          data-testid="timeline-job-panel"
          style={{
            padding: "10px 12px",
            borderRadius: "8px",
            background: isDlq ? "#fff5f5" : "#f8fafc",
            border: `1px solid ${isDlq ? "#fecaca" : "#e2e8f0"}`,
            marginBottom: "12px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
            <span style={{ fontSize: "11px", fontWeight: 700, color: isDlq ? "#991b1b" : "#334155" }}>
              {isDlq ? "⚠️ 任務死信佇列 DEAD LETTER QUEUE" : "⚡ 背景執行 Job Execution"}
            </span>
            <span
              style={{
                fontSize: "10px",
                fontWeight: 700,
                padding: "2px 6px",
                borderRadius: "4px",
                background: jobBadge.background,
                color: jobBadge.foreground,
              }}
            >
              {activeJob.status}{activeJob.delivery_state ? ` · ${activeJob.delivery_state}` : ""}
            </span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: "8px", fontSize: "11px", color: "#475569" }}>
            <div>Job ID: <span style={{ fontFamily: "monospace" }}>{activeJob.job_id}</span></div>
            <div>Checkpoint: <span style={{ fontWeight: 600 }}>{activeJob.checkpoint}</span></div>
            <div>Attempt: <span style={{ fontWeight: 600 }}>#{activeJob.attempt}</span></div>
            <div>Correlation ID: <span style={{ fontFamily: "monospace" }}>{activeJob.correlation_id}</span></div>
          </div>

          {isDlq && canReplay && onReplayJob && (
            <div style={{ marginTop: "8px", paddingTop: "8px", borderTop: "1px solid #fee2e2", display: "flex", justifyContent: "flex-end" }}>
              <button
                type="button"
                onClick={() => onReplayJob(activeJob.job_id)}
                className={styles.primaryButton}
                style={{ padding: "4px 12px", fontSize: "11px", background: "#dc2626", borderColor: "#dc2626" }}
                data-testid="timeline-replay-dlq-button"
              >
                重播 Replay DLQ Job
              </button>
            </div>
          )}
        </div>
      ) : <div className={styles.emptyState} data-testid="timeline-job-unavailable">JOB DATA: UNAVAILABLE</div>)}

      {/* 4. History Transition Audit Nodes */}
      {mode === "audit" && (history.length > 0 ? (
        <div data-testid="timeline-history-nodes" style={{ marginTop: "12px" }}>
          <div style={{ fontSize: "11px", fontWeight: 700, color: "#475569", marginBottom: "8px" }}>
            歷史變更日誌 HISTORY TRANSITIONS ({history.length})
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px", maxHeight: "180px", overflowY: "auto" }}>
            {history.map((tx, idx) => (
              <div
                key={tx.transition_id ?? idx}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "6px 10px",
                  background: "#f8fafc",
                  borderRadius: "6px",
                  fontSize: "11px",
                  borderLeft: "3px solid #2e3a97",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span style={{ color: "#64748b", fontFamily: "monospace", fontSize: "10px" }}>
                    {tx.occurred_at ? new Date(tx.occurred_at).toLocaleTimeString() : "UNAVAILABLE"}
                  </span>
                  <span style={{ fontWeight: 600, color: "#1e293b" }}>
                    {tx.from_state ? `${tx.from_state} → ` : ""}{tx.to_state}
                  </span>
                  {tx.reason_code && (
                    <span style={{ color: "#64748b", fontStyle: "italic" }}>({tx.reason_code})</span>
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "10px", color: "#64748b" }}>
                  <span>Actor: {tx.actor}</span>
                  <span>v{tx.version_after}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : <div className={styles.emptyState} data-testid="timeline-history-unavailable">TRANSITION HISTORY: UNAVAILABLE</div>)}

    </div>
  );
}

export function jobStatusBadgeColors(status: string | undefined, deliveryState?: string | null): {
  background: string;
  foreground: string;
} {
  return deliveryState === "DEAD_LETTER" || status === "FAILED"
    ? { background: "#991b1b", foreground: "#ffffff" }
    : { background: "#334155", foreground: "#ffffff" };
}
