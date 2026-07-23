import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssistedIntake, PromotionDecisionReceipt } from "@oday-plus/openapi-client";
import { AssignmentSlaSummary } from "../AssignmentSlaSummary";
import { IntakeStageTimeline } from "../IntakeStageTimeline";
import { PauseSlaDialog } from "../PauseSlaDialog";
import { PromotionReviewPanel } from "../PromotionReviewPanel";
import { SiteScoreJobStatus } from "../SiteScoreJobStatus";
import { TransferIntakeDialog } from "../TransferIntakeDialog";
import type {
  AssignmentLifecycleReceipt,
  JobLifecycleReceipt,
  PersistedLifecycleTransition,
  SlaLifecycleReceipt,
} from "../useIntakeLifecycle";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const record: AssistedIntake = {
  id: "INT-LIFE-1",
  originalUrl: "https://example.com/listing/1",
  canonicalUrl: "https://example.com/listing/1",
  submitter: "staff-1",
  owner: "manager-1",
  heatZoneId: "HZ-1",
  stage: "FAILED",
  sourceId: "synthetic",
  policy: "APPROVED_RETRIEVAL",
  policyLabel: "核准",
  policyReason: "policy",
  rawSnapshot: null,
  snapshotId: "SNAP-1",
  capturedAt: "2026-07-23T12:00:00Z",
  parserVersion: "parser-1",
  correlationId: "CORR-1",
  parsedFields: {},
  matchResult: null,
  auditEvents: [],
  version: 7,
};

function transition(
  id: string,
  stream: PersistedLifecycleTransition["stream"],
  from: string | null,
  to: string,
  overrides: Partial<PersistedLifecycleTransition> = {},
): PersistedLifecycleTransition {
  return {
    transition_id: id,
    stream,
    from_state: from,
    to_state: to,
    occurred_at: "2026-07-23T12:01:00Z",
    actor: "worker-1",
    actor_role: "service",
    reason_code: "WORKER_TRANSITION",
    version_after: 2,
    correlation_id: "CORR-1",
    ...overrides,
  };
}

const failedJob: JobLifecycleReceipt = {
  job_id: "JOB-1",
  status: "DEAD_LETTER",
  checkpoint: "PARSING",
  attempt: 4,
  max_attempts: 4,
  version: 5,
  correlation_id: "CORR-1",
  queue_name: "intake-parse",
  timeout_at: "2026-07-23T12:02:00Z",
  next_retry_at: null,
  dead_lettered_at: "2026-07-23T12:03:00Z",
  retryable: true,
};

let container: HTMLDivElement;
let root: Root;

function render(ui: React.ReactNode) {
  act(() => root.render(ui));
}

function get(testId: string): HTMLElement {
  const element = container.querySelector(`[data-testid="${testId}"]`);
  if (!element) throw new Error(`Missing ${testId}`);
  return element as HTMLElement;
}

function query(testId: string): HTMLElement | null {
  return container.querySelector(`[data-testid="${testId}"]`);
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("lifecycle controls", () => {
  it("renders persisted intake/job history, retry, controlled reopen, and DLQ replay", () => {
    const retry = vi.fn();
    const reopen = vi.fn();
    const replay = vi.fn();
    render(
      <IntakeStageTimeline
        canReopen
        canReplay
        canRetry
        history={[
          transition("TX-1", "INTAKE", null, "SUBMITTED"),
          transition("TX-2", "INTAKE", "PARSING", "FAILED", {
            attempt: 4,
            checkpoint: "PARSING",
          }),
        ]}
        jobs={[failedJob]}
        onReopen={reopen}
        onReplayJob={replay}
        onRetry={retry}
        record={record}
      />,
    );

    expect(get("timeline-transition-TX-2").textContent).toContain("Attempt 4");
    expect(get("timeline-job-JOB-1").textContent).toContain("intake-parse");
    expect(get("timeline-job-JOB-1").textContent).toContain("DEAD_LETTER");

    act(() => get("timeline-retry-button").click());
    act(() => get("timeline-reopen-button").click());
    act(() => get("timeline-replay-job-JOB-1").click());
    expect(retry).toHaveBeenCalledWith("PARSING");
    expect(reopen).toHaveBeenCalledTimes(1);
    expect(replay).toHaveBeenCalledWith("JOB-1");
  });

  it("never fabricates intermediate stages when persisted history is absent", () => {
    render(<IntakeStageTimeline record={record} />);
    expect(get("timeline-history-unavailable").textContent).toContain("不推算中間階段");
    expect(query("timeline-transition-TX-1")).toBeNull();
  });

  it("treats CANCELLED intake as terminal and removes retry/reopen/cancel actions", () => {
    render(
      <IntakeStageTimeline
        canCancel
        canReopen
        canRetry
        onCancel={vi.fn()}
        onReopen={vi.fn()}
        onRetry={vi.fn()}
        record={{ ...record, stage: "CANCELLED" } as unknown as AssistedIntake}
      />,
    );
    expect(get("timeline-cancelled-terminal").textContent).toContain("terminal");
    expect(query("timeline-cancel-button")).toBeNull();
    expect(query("timeline-retry-button")).toBeNull();
    expect(query("timeline-reopen-button")).toBeNull();
  });

  it("renders authoritative assignment/SLA facts and all direct actions", () => {
    const assignment: AssignmentLifecycleReceipt = {
      assignment_id: "ASG-1",
      status: "ESCALATED",
      owner_subject_id: "manager-2",
      owner_display_name: "王主管",
      owner_role: "expansion-manager",
      queue_name: "overdue-expansion",
      assigned_at: "2026-07-23T10:00:00Z",
      claimed_at: "2026-07-23T10:02:00Z",
      escalated_at: "2026-07-23T12:00:00Z",
      due_at: "2026-07-23T11:00:00Z",
      version: 4,
      audit_event_id: "AUD-ASG-1",
    };
    const sla: SlaLifecycleReceipt = {
      sla_instance_id: "SLA-1",
      state: "BREACHED",
      due_at: "2026-07-23T11:00:00Z",
      paused_duration_seconds: 600,
      version: 3,
      audit_event_id: "AUD-SLA-1",
      correlation_id: "CORR-1",
      escalation_level: 2,
    };
    const handlers = {
      claim: vi.fn(),
      transfer: vi.fn(),
      pause: vi.fn(),
      escalate: vi.fn(),
      complete: vi.fn(),
    };
    render(
      <AssignmentSlaSummary
        allowedActions={[
          "CLAIM_ASSIGNMENT",
          "TRANSFER_ASSIGNMENT",
          "PAUSE_SLA",
          "ESCALATE_ASSIGNMENT",
          "COMPLETE_ASSIGNMENT",
        ]}
        assignment={assignment}
        history={[
          transition("ASG-TX-1", "ASSIGNMENT", "CLAIMED", "ESCALATED", {
            owner_subject_id: "manager-2",
            queue_name: "overdue-expansion",
          }),
          transition("SLA-TX-1", "SLA", "OVERDUE", "BREACHED"),
        ]}
        onClaim={handlers.claim}
        onComplete={handlers.complete}
        onEscalate={handlers.escalate}
        onOpenPause={handlers.pause}
        onOpenTransfer={handlers.transfer}
        record={record}
        sla={sla}
      />,
    );

    expect(get("asg-owner").textContent).toContain("王主管");
    expect(get("asg-queue").textContent).toContain("overdue-expansion");
    expect(get("asg-history").textContent).toContain("CLAIMED → ESCALATED");
    for (const [testId, handler] of [
      ["asg-btn-claim", handlers.claim],
      ["asg-btn-transfer", handlers.transfer],
      ["asg-btn-pause", handlers.pause],
      ["asg-btn-escalate", handlers.escalate],
      ["asg-btn-complete", handlers.complete],
    ] as const) {
      act(() => get(testId).click());
      expect(handler).toHaveBeenCalledTimes(1);
    }
  });

  it("keeps Candidate visible after SCORE_FAILED and exposes timeout/next retry/replay", () => {
    const replay = vi.fn();
    render(
      <SiteScoreJobStatus
        canReplay
        candidateSiteId="CAND-1"
        history={[transition("JOB-TX-1", "JOB", "RUNNING", "DEAD_LETTER", { attempt: 4 })]}
        job={failedJob}
        onReplay={replay}
        promotionStatus="SCORE_FAILED"
      />,
    );
    expect(get("candidate-retained-id").textContent).toBe("CAND-1");
    expect(get("sitescore-job-timeout").textContent).not.toContain("未提供");
    expect(get("sitescore-job-next-retry").textContent).toBe("無");
    expect(get("sitescore-job-history").textContent).toContain("RUNNING → DEAD_LETTER");
    expect(get("sitescore-replay-controls")).toBeTruthy();
  });

  it("renders persisted promotion and decision histories without inferring a happy path", () => {
    const promotion: PromotionDecisionReceipt = {
      promotion_decision_id: "PD-1",
      intake_id: record.id,
      listing_id: "LST-1",
      status: "SCORE_FAILED",
      decision_type: "STANDARD",
      proposer_subject_id: "staff-1",
      reviewer_subject_id: "manager-2",
      candidate_site_id: "CAND-1",
      site_score_job_id: "JOB-1",
      version: 8,
      audit_event_id: "AUD-PD-1",
      correlation_id: "CORR-1",
    };
    render(
      <PromotionReviewPanel
        currentOperator={{ id: "manager-2", name: "王主管", role: "expansion-manager" }}
        decisionHistory={[
          transition("DEC-TX-1", "DECISION", "PENDING_REVIEW", "APPROVED"),
        ]}
        gateSnapshotSha256={"a".repeat(64)}
        promotion={promotion}
        promotionHistory={[
          transition("PRO-TX-1", "PROMOTION", "SCORE_QUEUED", "SCORE_FAILED"),
        ]}
        record={{ ...record, stage: "READY" }}
        scoreJob={failedJob}
        scoreJobHistory={[transition("JOB-TX-2", "JOB", "RUNNING", "DEAD_LETTER")]}
      />,
    );
    expect(get("promotion-history").textContent).toContain("SCORE_QUEUED → SCORE_FAILED");
    expect(get("promotion-decision-history").textContent).toContain(
      "PENDING_REVIEW → APPROVED",
    );
    expect(get("candidate-retained-id").textContent).toBe("CAND-1");
  });

  it("locks transfer and pause dialog dismissal while a write is in flight", () => {
    const closeTransfer = vi.fn();
    render(
      <TransferIntakeDialog
        busy
        error={null}
        onClose={closeTransfer}
        onSubmit={vi.fn()}
        record={record}
      />,
    );
    act(() => (container.querySelector('[aria-label="關閉"]') as HTMLButtonElement).click());
    expect(closeTransfer).not.toHaveBeenCalled();

    act(() => root.unmount());
    root = createRoot(container);
    const closePause = vi.fn();
    render(
      <PauseSlaDialog
        busy
        error={null}
        onClose={closePause}
        onSubmit={vi.fn()}
        record={record}
      />,
    );
    act(() => (container.querySelector('[aria-label="關閉"]') as HTMLButtonElement).click());
    expect(closePause).not.toHaveBeenCalled();
  });
});
