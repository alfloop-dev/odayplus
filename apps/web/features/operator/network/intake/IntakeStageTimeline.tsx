"use client";

import type {
  AssistedIntake,
  IntakeStage,
  JobReceipt,
  SlaReceipt,
  TransitionReceipt,
} from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { stageLabel, stageSteps, stageTone } from "./intakeTypes";

export type IntakeStageTimelineProps = {
  record: AssistedIntake;
  history?: TransitionReceipt[];
  jobs?: JobReceipt[];
  sla?: SlaReceipt;
  canReplay?: boolean;
  onReplayJob?: (jobId: string) => void;
  onCancel?: () => void;
  testId?: string;
};

export function IntakeStageTimeline({
  record,
  history = [],
  jobs = [],
  sla,
  canReplay = false,
  onReplayJob,
  onCancel,
  testId = "intake-stage-timeline",
}: IntakeStageTimelineProps) {
  const steps = stageSteps(record);
  const activeJob = jobs.find((j) => j.status === "RUNNING" || j.status === "RETRYING" || j.status === "DEAD_LETTER") ?? jobs[0];
  const isDlq = activeJob?.status === "DEAD_LETTER" || record.stage === "FAILED";
  const isCancelled = record.stage === "CANCELLED";

  return (
    <div className={styles.sectionBox} data-testid={testId} style={{ border: "1px solid #eef1f6", borderRadius: "10px", padding: "14px", background: "#ffffff", marginBottom: "16px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px" }}>
        <h4 style={{ margin: 0, fontSize: "13px", fontWeight: 700, color: "#1e293b", display: "flex", alignItems: "center", gap: "8px" }}>
          <span>階段時序與執行歷程 STAGE TIMELINE</span>
          <span className={styles.chip} data-tone={stageTone(record.stage)}>
            {stageLabel(record.stage)}
          </span>
        </h4>
        {onCancel && !isCancelled && record.stage !== "READY" && (
          <button
            type="button"
            onClick={onCancel}
            className={styles.secondaryButton}
            style={{ padding: "3px 8px", fontSize: "10.5px", color: "#b3261e" }}
            data-testid="timeline-cancel-button"
          >
            取消流程 (Cancel Intake)
          </button>
        )}
      </div>

      {/* 1. Stepper without fake percentages */}
      <div
        data-testid="timeline-stepper"
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
            fg = "#1e7f4f";
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
      </div>

      {/* 2. SLA & Assignment Metadata */}
      {sla && (
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
                color: sla.state === "BREACHED" || sla.state === "OVERDUE" ? "#b3261e" : "#1e7f4f",
              }}
            >
              {sla.state}
            </span>
          </div>
          <div>
            <span style={{ color: "#64748b" }}>SLA 到期時間: </span>
            <span style={{ fontFamily: "monospace", fontWeight: 600 }}>{sla.due_at ?? "—"}</span>
          </div>
          <div>
            <span style={{ color: "#64748b" }}>暫停時長: </span>
            <span>{sla.paused_duration_seconds ? `${Math.round(sla.paused_duration_seconds / 60)} 分鐘` : "無"}</span>
          </div>
        </div>
      )}

      {/* 3. Job Execution & DLQ Status */}
      {activeJob && (
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
                background: isDlq ? "#f87171" : "#cbd5e1",
                color: "#ffffff",
              }}
            >
              {activeJob.status}
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
      )}

      {/* 4. History Transition Audit Nodes */}
      {history.length > 0 && (
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
                    {tx.occurred_at ? new Date(tx.occurred_at).toLocaleTimeString() : "—"}
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
      )}
    </div>
  );
}
