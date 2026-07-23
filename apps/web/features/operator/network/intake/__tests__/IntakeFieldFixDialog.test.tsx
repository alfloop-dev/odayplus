import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IntakeFieldCell } from "@oday-plus/openapi-client";
import { IntakeFieldFixDialog } from "../IntakeFieldFixDialog";
import type { IntakeApiError } from "../intakeClient";

const field: IntakeFieldCell = {
  key: "address",
  label: "正規化地址",
  sourceValue: "台北市信義區松仁路10號",
  normalizedValue: "台北市信義區松仁路 10 號",
  correctedValue: null,
  correctionReason: null,
  identity: true,
  lowConfidence: true,
};

const draftIdentity = {
  tenantId: "tenant-01",
  intakeId: "intake-001",
  actorSubjectId: "staff-001",
};

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("IntakeFieldFixDialog durable correction proposal", () => {
  it("requires reason, risk acknowledgement, and independent review for material changes", () => {
    const onSubmit = vi.fn();
    renderDialog({ onSubmit });

    fireEvent.change(screen.getByTestId("intake-fix-value"), {
      target: { value: "台北市信義區松仁路 100 號" },
    });
    fireEvent.click(screen.getByTestId("intake-fix-submit"));
    expect(screen.getByTestId("intake-fix-error")).toHaveTextContent("必須填寫原因");

    fireEvent.change(screen.getByTestId("intake-fix-reason"), {
      target: { value: "依謄本校正門牌" },
    });
    fireEvent.click(screen.getByTestId("intake-fix-submit"));
    expect(screen.getByTestId("intake-fix-error")).toHaveTextContent("請先確認");
    expect(screen.getByTestId("intake-fix-independent-review")).toHaveTextContent(
      "提案者不得自行核准",
    );
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("intake-fix-risk-ack"));
    fireEvent.click(screen.getByTestId("intake-fix-submit"));

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        value: "台北市信義區松仁路 100 號",
        reason: "依謄本校正門牌",
        riskAcknowledged: true,
        requiresIndependentReview: true,
        ifMatchVersion: 3,
        operationId: expect.any(String),
      }),
    );
  });

  it("locks every control while submitting and blocks duplicate submissions", () => {
    const onSubmit = vi.fn();
    renderDialog({ onSubmit });
    fillMaterialCorrection();

    fireEvent.click(screen.getByTestId("intake-fix-submit"));
    fireEvent.click(screen.getByTestId("intake-fix-submit"));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("intake-fix-value")).toBeDisabled();
    expect(screen.getByTestId("intake-fix-reason")).toBeDisabled();
    expect(screen.getByTestId("intake-fix-risk-ack")).toBeDisabled();
    expect(screen.getByTestId("intake-fix-submit")).toBeDisabled();
    expect(screen.getByRole("button", { name: "關閉" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
  });

  it("keeps correction value, reason, acknowledgement and operation across close/reload", () => {
    const onSubmit = vi.fn();
    const first = renderDialog({ onSubmit });

    fireEvent.change(screen.getByTestId("intake-fix-value"), {
      target: { value: "台北市信義區松仁路 100 號" },
    });
    fireEvent.change(screen.getByTestId("intake-fix-reason"), {
      target: { value: "依謄本校正門牌" },
    });
    fireEvent.click(screen.getByTestId("intake-fix-risk-ack"));
    const firstOperation = operationFromContext();
    fireEvent.click(screen.getByRole("button", { name: "關閉" }));
    first.unmount();

    renderDialog({ onSubmit });

    expect(screen.getByTestId("intake-fix-value")).toHaveValue(
      "台北市信義區松仁路 100 號",
    );
    expect(screen.getByTestId("intake-fix-reason")).toHaveValue("依謄本校正門牌");
    expect(screen.getByTestId("intake-fix-risk-ack")).toBeChecked();
    expect(operationFromContext()).toBe(firstOperation);
  });

  it("retains the submitted draft and operation after failure", async () => {
    const onSubmit = vi.fn();
    const view = renderDialog({ onSubmit });
    fillMaterialCorrection();
    const submittedOperation = operationFromContext();
    fireEvent.click(screen.getByTestId("intake-fix-submit"));

    const conflict: IntakeApiError = {
      status: 409,
      code: "VERSION_CONFLICT",
      summary: "Record version changed",
      nextAction: "重新整理後使用相同 operation 重試",
      correlationId: "corr-conflict",
      occurredAt: "2026-07-23T07:00:00Z",
      retryable: true,
    };
    view.rerender(
      <IntakeFieldFixDialog
        baseVersion={3}
        busy={false}
        draftIdentity={draftIdentity}
        error={conflict}
        field={field}
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("intake-fix-error")).toHaveTextContent("Record version changed"),
    );
    expect(screen.getByTestId("intake-fix-value")).toHaveValue(
      "台北市信義區松仁路 100 號",
    );
    expect(screen.getByTestId("intake-fix-reason")).toHaveValue("依謄本校正門牌");
    expect(screen.getByTestId("intake-fix-risk-ack")).toBeChecked();
    expect(operationFromContext()).toBe(submittedOperation);
    expect(screen.getByTestId("intake-fix-submit")).toHaveTextContent("相同 operation");
    expect(screen.getByTestId("intake-fix-value")).not.toBeDisabled();
  });

  it("clears only the matching submitted operation and applies authoritative readback", async () => {
    const onSubmit = vi.fn();
    const view = renderDialog({ onSubmit });
    fillMaterialCorrection();
    fireEvent.click(screen.getByTestId("intake-fix-submit"));

    const submission = onSubmit.mock.calls[0]?.[0] as {
      operationId: string;
      ifMatchVersion: number | null;
    };

    view.rerender(
      <IntakeFieldFixDialog
        baseVersion={4}
        busy={false}
        draftIdentity={draftIdentity}
        error={null}
        field={{ ...field, correctedValue: "不應套用的舊回讀" }}
        onClose={vi.fn()}
        onSubmit={onSubmit}
        submissionState={{
          status: "COMMITTED",
          operationId: "different-operation",
          submittedBaseVersion: submission.ifMatchVersion,
        }}
      />,
    );
    expect(screen.getByTestId("intake-fix-value")).toHaveValue(
      "台北市信義區松仁路 100 號",
    );
    expect(screen.getByTestId("intake-fix-value")).toBeDisabled();

    view.rerender(
      <IntakeFieldFixDialog
        baseVersion={4}
        busy={false}
        draftIdentity={draftIdentity}
        error={null}
        field={{
          ...field,
          correctedValue: "台北市信義區松仁路 100 號",
          correctionReason: "依謄本校正門牌",
        }}
        onClose={vi.fn()}
        onSubmit={onSubmit}
        submissionState={{
          status: "COMMITTED",
          operationId: submission.operationId,
          submittedBaseVersion: submission.ifMatchVersion,
        }}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("intake-fix-value")).toHaveValue(
        "台北市信義區松仁路 100 號",
      ),
    );
    expect(screen.getByTestId("intake-fix-reason")).toHaveValue("");
    expect(screen.getByTestId("intake-fix-risk-ack")).not.toBeChecked();
    expect(screen.getByTestId("intake-fix-value")).not.toBeDisabled();
    expect(operationFromContext()).not.toBe(submission.operationId);
  });
});

function renderDialog({
  onSubmit,
}: {
  onSubmit: React.ComponentProps<typeof IntakeFieldFixDialog>["onSubmit"];
}) {
  return render(
    <IntakeFieldFixDialog
      baseVersion={3}
      busy={false}
      draftIdentity={draftIdentity}
      error={null}
      field={field}
      onClose={vi.fn()}
      onSubmit={onSubmit}
    />,
  );
}

function operationFromContext(): string {
  const text = screen.getByTestId("intake-fix-context").textContent ?? "";
  return text.match(/Operation ([^ ]+)/)?.[1] ?? "";
}

function fillMaterialCorrection() {
  fireEvent.change(screen.getByTestId("intake-fix-value"), {
    target: { value: "台北市信義區松仁路 100 號" },
  });
  fireEvent.change(screen.getByTestId("intake-fix-reason"), {
    target: { value: "依謄本校正門牌" },
  });
  fireEvent.click(screen.getByTestId("intake-fix-risk-ack"));
}
