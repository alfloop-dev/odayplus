// ODP-INTAKE-UX-PROMOTION-001 — Candidate Site promotion + SiteScore job UI.
//
// Encodes the four task acceptance criteria plus the Review 003 lesson from
// VDC-001: tests assert control PRESENCE AND ABSENCE per state, not just
// internal defaults.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { JobReceipt, PromotionDecisionReceipt, PromotionStatus } from "@oday-plus/openapi-client";
import {
  PromotionReviewPanel,
  PROMOTION_STATUS_LABEL,
  promotionStagePath,
  committedCandidateId,
  committedScoreJobId,
} from "../PromotionReviewPanel";
import { SiteScoreJobStatus, SITE_SCORE_JOB_LABEL } from "../SiteScoreJobStatus";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLElement | null = null;
let root: Root | null = null;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  if (root) {
    act(() => {
      root!.unmount();
    });
    root = null;
  }
  if (container) {
    container.remove();
    container = null;
  }
});

function render(ui: React.ReactNode) {
  act(() => {
    root!.render(ui);
  });
  return {
    rerender(newUi: React.ReactNode) {
      act(() => {
        root!.render(newUi);
      });
    },
  };
}

const screen = {
  getByTestId(testId: string): HTMLElement {
    const el = document.body.querySelector(`[data-testid="${testId}"]`);
    if (!el) {
      throw new Error(`Element with data-testid="${testId}" not found`);
    }
    return el as HTMLElement;
  },
  queryByTestId(testId: string): HTMLElement | null {
    return document.body.querySelector(`[data-testid="${testId}"]`) as HTMLElement | null;
  },
};

const fireEvent = {
  click(element: HTMLElement) {
    act(() => {
      if (element instanceof HTMLInputElement && element.type === "checkbox") {
        const checkedSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "checked")?.set;
        if (checkedSetter) {
          checkedSetter.call(element, !element.checked);
        } else {
          element.checked = !element.checked;
        }
        element.dispatchEvent(new Event("click", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
      } else {
        element.click();
      }
    });
  },
  change(element: HTMLElement, { target: { value } }: { target: { value: string } }) {
    act(() => {
      if (
        element instanceof HTMLInputElement ||
        element instanceof HTMLTextAreaElement ||
        element instanceof HTMLSelectElement
      ) {
        const valueSetter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), "value")?.set;
        if (valueSetter) {
          valueSetter.call(element, value);
        } else {
          (element as HTMLInputElement).value = value;
        }
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
  },
};

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
}

// ---------------------------------------------------------------- fixtures --

/** READY intake — the only stage from which promotion may be requested. */
const readyRecord: any = {
  id: "IN-4001",
  originalUrl: "https://rent.591.com.tw/44001",
  canonicalUrl: "https://rent.591.com.tw/44001",
  submitter: "OP-100",
  owner: "OP-100",
  heatZoneId: "HZ-TPE-XINYI",
  stage: "READY",
  sourceId: "591_44001",
  policy: "APPROVED_RETRIEVAL",
  policyLabel: "核准單頁讀取",
  policyReason: "核准領域白名單",
  rawSnapshot: null,
  snapshotId: "SNAP-44001",
  capturedAt: "2026-07-22T09:00:00Z",
  parserVersion: "v2.1.0",
  correlationId: "CORR-44001",
  parsedFields: {},
  matchResult: { targetListingId: "", outcome: "NEW", outcomeLabel: "新物件", confidence: 0.95, summary: "", agreeingSignals: [], contradictingSignals: [] },
  auditEvents: [],
  version: 4,
};

const GATE_SHA = "a".repeat(64);

const manager = { id: "OP-200", name: "Second Manager", role: "expansion-manager" };

function promo(status: PromotionStatus, overrides: Partial<PromotionDecisionReceipt> = {}): PromotionDecisionReceipt {
  return {
    promotion_decision_id: "PD-9001",
    intake_id: "IN-4001",
    listing_id: "LST-7001",
    status,
    decision_type: "STANDARD",
    reviewer_subject_id: null,
    candidate_site_id: null,
    site_score_job_id: null,
    version: 7,
    audit_event_id: "AUD-9001",
    correlation_id: "CORR-9001",
    ...overrides,
  };
}

function job(status: JobReceipt["status"], overrides: Partial<JobReceipt> = {}): JobReceipt {
  return {
    job_id: "JOB-5001",
    status,
    checkpoint: "SCORE_QUEUED",
    attempt: 1,
    version: 3,
    correlation_id: "CORR-9001",
    ...overrides,
  };
}

const ALL_PROMOTION_STATUSES: PromotionStatus[] = [
  "REQUESTED",
  "VALIDATING",
  "PENDING_REVIEW",
  "REJECTED",
  "APPROVED",
  "CANDIDATE_CREATING",
  "CANDIDATE_CREATED",
  "SCORE_QUEUED",
  "COMPLETED",
  "FAILED",
  "SCORE_FAILED",
];

function renderPanel(props: Partial<React.ComponentProps<typeof PromotionReviewPanel>> = {}) {
  return render(
    <PromotionReviewPanel
      currentOperator={manager}
      gateSnapshotSha256={GATE_SHA}
      record={readyRecord}
      {...props}
    />,
  );
}

// ------------------------------------------------------------------- tests --

describe("PromotionReviewPanel — saga states are never compressed (acceptance 1)", () => {
  it("renders a distinct badge and stepper node for every canonical promotion state", () => {
    const { rerender } = renderPanel({ promotion: promo("REQUESTED") });
    for (const status of ALL_PROMOTION_STATUSES) {
      rerender(
        <PromotionReviewPanel
          currentOperator={manager}
          gateSnapshotSha256={GATE_SHA}
          promotion={promo(status)}
          record={readyRecord}
        />,
      );
      const badge = screen.getByTestId("promotion-status-badge");
      expect(badge.textContent).toContain(status);
      expect(badge.textContent).toContain(PROMOTION_STATUS_LABEL[status]);
      // The current state is a real stepper node, not a spinner.
      const step = screen.getByTestId(`promo-step-${status}`);
      expect(step.getAttribute("data-state")).not.toBe("upcoming");
    }
  });

  it("shows the full happy path for COMPLETED and the real failure branch otherwise", () => {
    expect(promotionStagePath("COMPLETED")).toEqual([
      "REQUESTED",
      "VALIDATING",
      "PENDING_REVIEW",
      "APPROVED",
      "CANDIDATE_CREATING",
      "CANDIDATE_CREATED",
      "SCORE_QUEUED",
      "COMPLETED",
    ]);
    expect(promotionStagePath("REJECTED")).toContain("REJECTED");
    expect(promotionStagePath("REJECTED")).not.toContain("APPROVED");
    expect(promotionStagePath("FAILED")).toContain("CANDIDATE_CREATING");
    expect(promotionStagePath("FAILED")).not.toContain("CANDIDATE_CREATED");
    expect(promotionStagePath("SCORE_FAILED")).toContain("SCORE_QUEUED");

    renderPanel({ promotion: promo("SCORE_FAILED") });
    expect(screen.getByTestId("promo-step-SCORE_FAILED").getAttribute("data-state")).toBe("failed");
  });

  it("renders no stepper and an explicit 尚未提出 badge before any request", () => {
    renderPanel();
    expect(screen.getByTestId("promotion-status-badge").textContent).toContain("尚未提出晉升申請");
    expect(screen.queryByTestId("promotion-saga-stepper")).toBeNull();
    expect(screen.queryByTestId("promotion-receipt")).toBeNull();
  });
});

describe("PromotionReviewPanel — explicit request contract (acceptance 2)", () => {
  it("keeps submit locked until reason and risk acknowledgement are provided", () => {
    const onRequestPromotion = vi.fn();
    renderPanel({ onRequestPromotion });

    const submit = screen.getByTestId("promotion-request-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.change(screen.getByTestId("promotion-request-reason"), {
      target: { value: "熱區缺口成立，申請晉升。" },
    });
    expect((screen.getByTestId("promotion-request-submit") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByTestId("promotion-request-ack"));
    expect((screen.getByTestId("promotion-request-submit") as HTMLButtonElement).disabled).toBe(false);
  });

  it("submits target format, gate snapshot, If-Match and an idempotency key — without optimistic state", async () => {
    const onRequestPromotion = vi.fn().mockResolvedValue(undefined);
    renderPanel({ onRequestPromotion });

    fireEvent.change(screen.getByTestId("promotion-target-format"), { target: { value: "FMT-MICRO-STORE" } });
    fireEvent.change(screen.getByTestId("promotion-request-reason"), {
      target: { value: "租金與面積皆符合門檻。" },
    });
    fireEvent.click(screen.getByTestId("promotion-request-ack"));
    fireEvent.click(screen.getByTestId("promotion-request-submit"));
    await flush();

    expect(onRequestPromotion).toHaveBeenCalledTimes(1);
    const input = onRequestPromotion.mock.calls[0][0];
    expect(input.targetFormatCode).toBe("FMT-MICRO-STORE");
    expect(input.gateSnapshotSha256).toBe(GATE_SHA);
    expect(input.ifMatch).toBe('W/"4"');
    expect(input.riskAcknowledged).toBe(true);
    expect(input.idempotencyKey).toBeTruthy();

    // Non-optimistic: the saga state is still server-driven — no receipt, no
    // fabricated REQUESTED badge.
    expect(screen.getByTestId("promotion-status-badge").textContent).toContain("尚未提出晉升申請");
  });

  it("locks the submit control while busy instead of double-sending", () => {
    const onRequestPromotion = vi.fn();
    renderPanel({ onRequestPromotion, busy: true });
    const submit = screen.getByTestId("promotion-request-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(submit.textContent).toContain("不做樂觀更新");
  });

  it("reuses the SAME idempotency key when the operator retries a failed submit", async () => {
    const onRequestPromotion = vi
      .fn()
      .mockRejectedValueOnce(Object.assign(new Error("網路中斷"), { status: 0 }))
      .mockResolvedValueOnce(undefined);
    renderPanel({ onRequestPromotion });

    fireEvent.change(screen.getByTestId("promotion-request-reason"), {
      target: { value: "第一次提交因網路失敗。" },
    });
    fireEvent.click(screen.getByTestId("promotion-request-ack"));
    fireEvent.click(screen.getByTestId("promotion-request-submit"));
    await flush();
    fireEvent.click(screen.getByTestId("promotion-request-submit"));
    await flush();

    expect(onRequestPromotion).toHaveBeenCalledTimes(2);
    const first = onRequestPromotion.mock.calls[0][0];
    const second = onRequestPromotion.mock.calls[1][0];
    expect(second.idempotencyKey).toBe(first.idempotencyKey);
  });

  it("removes the request form entirely for unauthorized roles and non-READY intakes", () => {
    renderPanel({ canRequest: false });
    expect(screen.queryByTestId("promotion-request-form")).toBeNull();
    expect(screen.getByTestId("promotion-request-denied")).not.toBeNull();

    const needsReview = { ...readyRecord, stage: "NEEDS_REVIEW" };
    render(
      <PromotionReviewPanel currentOperator={manager} gateSnapshotSha256={GATE_SHA} record={needsReview} />,
    );
    expect(screen.queryByTestId("promotion-request-form")).toBeNull();
    expect(screen.getByTestId("promotion-not-ready-note")).not.toBeNull();
  });

  it("hides the request form once a promotion is in flight, but reopens it after REJECTED with a fresh key", async () => {
    const onRequestPromotion = vi.fn().mockResolvedValue(undefined);
    const { rerender } = renderPanel({ onRequestPromotion });
    const firstKey = screen.getByTestId("promotion-request-key").textContent;

    rerender(
      <PromotionReviewPanel
        currentOperator={manager}
        gateSnapshotSha256={GATE_SHA}
        promotion={promo("PENDING_REVIEW")}
        record={readyRecord}
      />,
    );
    expect(screen.queryByTestId("promotion-request-form")).toBeNull();

    rerender(
      <PromotionReviewPanel
        currentOperator={manager}
        gateSnapshotSha256={GATE_SHA}
        promotion={promo("REJECTED", { reviewer_subject_id: "OP-300" })}
        record={readyRecord}
      />,
    );
    expect(screen.getByTestId("promotion-request-form")).not.toBeNull();
    expect(screen.getByTestId("promotion-rerequest-chip")).not.toBeNull();
    const rerequestKey = screen.getByTestId("promotion-request-key").textContent;
    // A new request after rejection is a NEW mutation — it must not replay
    // the rejected receipt.
    expect(rerequestKey).not.toBe(firstKey);
  });
});

describe("PromotionReviewPanel — independent second-actor review", () => {
  it("lets a different manager approve with reason, risk ack, If-Match and idempotency key", async () => {
    const onReviewPromotion = vi.fn().mockResolvedValue(undefined);
    renderPanel({
      promotion: promo("PENDING_REVIEW"),
      proposerId: "OP-100",
      onReviewPromotion,
    });

    expect(screen.getByTestId("promotion-second-actor-ok")).not.toBeNull();
    expect(screen.queryByTestId("promotion-self-review-denied")).toBeNull();

    const approve = screen.getByTestId("promotion-approve-btn") as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("promotion-review-reason"), {
      target: { value: "已核對 gate snapshot，核准。" },
    });
    fireEvent.click(screen.getByTestId("promotion-review-ack"));
    fireEvent.click(screen.getByTestId("promotion-approve-btn"));
    await flush();

    expect(onReviewPromotion).toHaveBeenCalledTimes(1);
    const input = onReviewPromotion.mock.calls[0][0];
    expect(input.decision).toBe("APPROVE");
    expect(input.ifMatch).toBe('W/"7"');
    expect(input.riskAcknowledged).toBe(true);
    expect(input.idempotencyKey).toBeTruthy();
  });

  it("blocks self-review: proposer sees SELF_REVIEW_DENIED and no approve/reject controls", () => {
    renderPanel({
      promotion: promo("PENDING_REVIEW"),
      proposerId: "OP-200", // same as currentOperator (manager)
    });

    expect(screen.getByTestId("promotion-self-review-denied").textContent).toContain("SELF_REVIEW_DENIED");
    expect(screen.getByTestId("promotion-self-review-notice")).not.toBeNull();
    // Control ABSENCE, not just a disabled default (VDC-001 lesson).
    expect(screen.queryByTestId("promotion-approve-btn")).toBeNull();
    expect(screen.queryByTestId("promotion-reject-btn")).toBeNull();
  });

  it("removes review controls for unauthorized roles and outside PENDING_REVIEW", () => {
    renderPanel({ promotion: promo("PENDING_REVIEW"), proposerId: "OP-100", canReview: false });
    expect(screen.getByTestId("promotion-review-denied")).not.toBeNull();
    expect(screen.queryByTestId("promotion-approve-btn")).toBeNull();

    render(
      <PromotionReviewPanel
        currentOperator={manager}
        gateSnapshotSha256={GATE_SHA}
        promotion={promo("APPROVED", { reviewer_subject_id: "OP-200" })}
        proposerId="OP-100"
        record={readyRecord}
      />,
    );
    expect(screen.queryByTestId("promotion-review-section")).toBeNull();
    expect(screen.queryByTestId("promotion-approve-btn")).toBeNull();
  });
});

describe("PromotionReviewPanel — IDs only after authoritative commit (acceptance 3)", () => {
  it("shows pending placeholders before the creation transaction commits", () => {
    renderPanel({ promotion: promo("PENDING_REVIEW") });
    expect(screen.getByTestId("promotion-candidate-pending")).not.toBeNull();
    expect(screen.getByTestId("promotion-score-job-pending")).not.toBeNull();
    expect(screen.queryByTestId("promotion-candidate-id")).toBeNull();
    expect(screen.queryByTestId("promotion-score-job-id")).toBeNull();
  });

  it("refuses to display IDs the server leaked before their commit point", () => {
    // Defensive gate: even if a receipt carried IDs in a pre-commit state,
    // the UI must not show them (§8.8: display only after transaction commit).
    renderPanel({
      promotion: promo("CANDIDATE_CREATING", {
        candidate_site_id: "CAND-EARLY",
        site_score_job_id: "JOB-EARLY",
      }),
    });
    expect(screen.queryByTestId("promotion-candidate-id")).toBeNull();
    expect(screen.queryByTestId("promotion-score-job-id")).toBeNull();
    expect(committedCandidateId(promo("CANDIDATE_CREATING", { candidate_site_id: "X" }))).toBeNull();
    expect(committedScoreJobId(promo("CANDIDATE_CREATED", { site_score_job_id: "X" }))).toBeNull();
  });

  it("reveals the candidate ID at CANDIDATE_CREATED and the job ID at SCORE_QUEUED", () => {
    const { rerender } = renderPanel({
      promotion: promo("CANDIDATE_CREATED", { candidate_site_id: "CAND-1234" }),
    });
    expect(screen.getByTestId("promotion-candidate-id").textContent).toBe("CAND-1234");
    expect(screen.getByTestId("promotion-score-job-pending")).not.toBeNull();

    rerender(
      <PromotionReviewPanel
        currentOperator={manager}
        gateSnapshotSha256={GATE_SHA}
        promotion={promo("SCORE_QUEUED", { candidate_site_id: "CAND-1234", site_score_job_id: "JOB-5001" })}
        record={readyRecord}
        scoreJob={job("QUEUED")}
      />,
    );
    expect(screen.getByTestId("promotion-candidate-id").textContent).toBe("CAND-1234");
    expect(screen.getByTestId("promotion-score-job-id").textContent).toBe("JOB-5001");
    expect(screen.getByTestId("sitescore-job-id").textContent).toBe("JOB-5001");
  });

  it("keeps the candidate visible on SCORE_FAILED and offers authorized same-key replay", async () => {
    const onReplayScore = vi
      .fn()
      .mockRejectedValueOnce(Object.assign(new Error("replay 逾時"), { status: 0 }))
      .mockResolvedValueOnce(undefined);
    renderPanel({
      promotion: promo("SCORE_FAILED", { candidate_site_id: "CAND-1234", site_score_job_id: "JOB-5001" }),
      scoreJob: job("FAILED"),
      canReplayScore: true,
      onReplayScore,
    });

    // Candidate retained — ID still shown, plus the explicit retention note.
    expect(screen.getByTestId("promotion-candidate-id").textContent).toBe("CAND-1234");
    expect(screen.getByTestId("candidate-retained-note").textContent).toContain("仍然存在");
    expect(screen.getByTestId("candidate-retained-id").textContent).toBe("CAND-1234");

    fireEvent.change(screen.getByTestId("sitescore-replay-reason"), {
      target: { value: "外部評分服務已恢復。" },
    });
    fireEvent.click(screen.getByTestId("sitescore-replay-ack"));
    fireEvent.click(screen.getByTestId("sitescore-replay-btn"));
    await flush();
    fireEvent.click(screen.getByTestId("sitescore-replay-btn"));
    await flush();

    expect(onReplayScore).toHaveBeenCalledTimes(2);
    const [first, second] = [onReplayScore.mock.calls[0][0], onReplayScore.mock.calls[1][0]];
    expect(first.checkpoint).toBe("SCORE_QUEUED");
    expect(first.jobId).toBe("JOB-5001");
    expect(first.ifMatch).toBe('W/"3"');
    // The SAME idempotency key across replay retries — no double-queue.
    expect(second.idempotencyKey).toBe(first.idempotencyKey);
  });

  it("hides the replay control from unauthorized users", () => {
    renderPanel({
      promotion: promo("SCORE_FAILED", { candidate_site_id: "CAND-1234", site_score_job_id: "JOB-5001" }),
      scoreJob: job("FAILED"),
      canReplayScore: false,
    });
    expect(screen.queryByTestId("sitescore-replay-btn")).toBeNull();
    expect(screen.getByTestId("sitescore-replay-denied")).not.toBeNull();
  });
});

describe("PromotionReviewPanel — lost response, receipts and conflicts (acceptance 4)", () => {
  it("offers same-key retry and decision lookup after a lost response", async () => {
    const onRequestPromotion = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error("連線逾時"), { status: 0 }));
    const onLookupDecision = vi.fn();
    const transportError = {
      status: 0,
      code: "ODP-INTAKE-TIMEOUT",
      summary: "連線逾時 — 後端未在時限內回應，本次操作未寫入。",
      nextAction: "請確認網路連線後重試；你輸入的內容已保留。",
      correlationId: null,
      occurredAt: "2026-07-22T10:00:00Z",
      retryable: true,
    };

    const { rerender } = renderPanel({ onRequestPromotion, onLookupDecision });
    fireEvent.change(screen.getByTestId("promotion-request-reason"), {
      target: { value: "提交後回應遺失。" },
    });
    fireEvent.click(screen.getByTestId("promotion-request-ack"));
    fireEvent.click(screen.getByTestId("promotion-request-submit"));
    await flush();

    rerender(
      <PromotionReviewPanel
        currentOperator={manager}
        error={transportError}
        gateSnapshotSha256={GATE_SHA}
        onLookupDecision={onLookupDecision}
        onRequestPromotion={onRequestPromotion}
        record={readyRecord}
      />,
    );

    const lost = screen.getByTestId("promotion-lost-response");
    expect(lost.textContent).toContain("不會建立第二筆 Candidate");

    fireEvent.click(screen.getByTestId("promotion-lost-retry-btn"));
    await flush();
    expect(onRequestPromotion).toHaveBeenCalledTimes(2);
    expect(onRequestPromotion.mock.calls[1][0].idempotencyKey).toBe(
      onRequestPromotion.mock.calls[0][0].idempotencyKey,
    );

    fireEvent.click(screen.getByTestId("promotion-lookup-btn"));
    expect(onLookupDecision).toHaveBeenCalledTimes(1);
  });

  it("labels an idempotent replayed response as recovered, not re-created", () => {
    renderPanel({
      promotion: promo("COMPLETED", { candidate_site_id: "CAND-1234", site_score_job_id: "JOB-5001" }),
      idempotencyReplayed: true,
    });
    const tag = screen.getByTestId("idempotency-replayed-indicator");
    expect(tag.textContent).toContain("未建立第二筆");
  });

  it("renders the durable promotion receipt with audit and correlation evidence", () => {
    renderPanel({
      promotion: promo("COMPLETED", {
        candidate_site_id: "CAND-1234",
        site_score_job_id: "JOB-5001",
        reviewer_subject_id: "OP-300",
      }),
    });
    expect(screen.getByTestId("promotion-decision-id").textContent).toBe("PD-9001");
    expect(screen.getByTestId("promotion-audit-event-id").textContent).toBe("AUD-9001");
    expect(screen.getByTestId("promotion-correlation-id").textContent).toBe("CORR-9001");
    expect(screen.getByTestId("promotion-version").textContent).toContain('W/"7"');
    expect(screen.getByTestId("promotion-receipt-reviewer").textContent).toContain("OP-300");
  });

  it("preserves operator input on 409 conflict and offers refresh", () => {
    const onRefresh = vi.fn();
    const conflictError = {
      status: 409,
      code: "ODP-INTAKE-CONFLICT",
      summary: "版本衝突",
      nextAction: "請重新整理",
      correlationId: "CORR-409",
      occurredAt: "2026-07-22T10:05:00Z",
      retryable: false,
    };
    const { rerender } = renderPanel({ onRefresh });
    fireEvent.change(screen.getByTestId("promotion-request-reason"), {
      target: { value: "衝突前輸入的理由。" },
    });

    rerender(
      <PromotionReviewPanel
        currentOperator={manager}
        error={conflictError}
        gateSnapshotSha256={GATE_SHA}
        onRefresh={onRefresh}
        record={readyRecord}
      />,
    );

    const banner = screen.getByTestId("promotion-conflict-banner");
    expect(banner.textContent).toContain("409");
    expect(banner.textContent).toContain("輸入已完整保留");
    expect((screen.getByTestId("promotion-request-reason") as HTMLTextAreaElement).value).toBe(
      "衝突前輸入的理由。",
    );
    fireEvent.click(screen.getByTestId("promotion-conflict-refresh-btn"));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("surfaces 428 PRECONDITION_REQUIRED explicitly", () => {
    renderPanel({
      error: {
        status: 428,
        code: "ODP-INTAKE-PRECONDITION",
        summary: "缺少 If-Match",
        nextAction: "請重新整理",
        correlationId: null,
        occurredAt: "2026-07-22T10:06:00Z",
        retryable: false,
      },
    });
    expect(screen.getByTestId("promotion-precondition-banner").textContent).toContain("428");
  });
});

describe("SiteScoreJobStatus — every job state distinct, ID only from receipts", () => {
  it("renders all seven canonical job states distinctly", () => {
    const statuses: JobReceipt["status"][] = [
      "QUEUED",
      "RUNNING",
      "RETRYING",
      "SUCCEEDED",
      "FAILED",
      "CANCELLED",
      "DEAD_LETTER",
    ];
    const { rerender } = render(<SiteScoreJobStatus job={job("QUEUED")} />);
    for (const status of statuses) {
      rerender(<SiteScoreJobStatus job={job(status)} />);
      const chip = screen.getByTestId("sitescore-job-state");
      expect(chip.textContent).toContain(status);
      expect(chip.textContent).toContain(SITE_SCORE_JOB_LABEL[status]);
    }
  });

  it("shows a placeholder — never a fabricated job ID — before the server commits one", () => {
    render(<SiteScoreJobStatus job={null} promotionStatus="CANDIDATE_CREATED" />);
    expect(screen.getByTestId("sitescore-job-placeholder")).not.toBeNull();
    expect(screen.queryByTestId("sitescore-job-id")).toBeNull();
  });

  it("exposes attempt, checkpoint, version and correlation from the receipt", () => {
    render(<SiteScoreJobStatus job={job("RETRYING", { attempt: 3, checkpoint: "SCORE_QUEUED" })} />);
    expect(screen.getByTestId("sitescore-job-attempt").textContent).toContain("3");
    expect(screen.getByTestId("sitescore-job-checkpoint").textContent).toBe("SCORE_QUEUED");
    expect(screen.getByTestId("sitescore-job-version").textContent).toContain('W/"3"');
    expect(screen.getByTestId("sitescore-job-correlation").textContent).toBe("CORR-9001");
  });

  it("requires reason and risk ack before replay is enabled", () => {
    const onReplay = vi.fn();
    render(
      <SiteScoreJobStatus canReplay job={job("FAILED")} onReplay={onReplay} promotionStatus="SCORE_FAILED" />,
    );
    const btn = screen.getByTestId("sitescore-replay-btn") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("sitescore-replay-reason"), { target: { value: "已修復根因。" } });
    expect((screen.getByTestId("sitescore-replay-btn") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("sitescore-replay-ack"));
    expect((screen.getByTestId("sitescore-replay-btn") as HTMLButtonElement).disabled).toBe(false);
  });

  it("rotates the replay idempotency key only when the server bumps the attempt", async () => {
    const onReplay = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <SiteScoreJobStatus canReplay job={job("FAILED", { attempt: 1 })} onReplay={onReplay} promotionStatus="SCORE_FAILED" />,
    );
    const keyAttempt1 = screen.getByTestId("sitescore-replay-key").textContent;

    // Same attempt re-render (e.g. refresh) → same key.
    rerender(
      <SiteScoreJobStatus canReplay job={job("FAILED", { attempt: 1 })} onReplay={onReplay} promotionStatus="SCORE_FAILED" />,
    );
    expect(screen.getByTestId("sitescore-replay-key").textContent).toBe(keyAttempt1);

    // Server committed the replay (attempt bumped) → a NEW mutation, new key.
    rerender(
      <SiteScoreJobStatus canReplay job={job("FAILED", { attempt: 2 })} onReplay={onReplay} promotionStatus="SCORE_FAILED" />,
    );
    expect(screen.getByTestId("sitescore-replay-key").textContent).not.toBe(keyAttempt1);
  });
});
