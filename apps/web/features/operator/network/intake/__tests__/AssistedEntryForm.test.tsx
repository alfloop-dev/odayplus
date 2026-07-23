import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AssistedEntryForm,
  type AssistedEntryCommitResult,
  type AssistedEntrySubmission,
} from "../AssistedEntryForm";

const identity = {
  tenantId: "tenant-tw-01",
  intakeId: "intake-001",
  actorSubjectId: "expansion-user-001",
};

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("AssistedEntryForm durable drafts", () => {
  it("never retrieves or renders credential inputs for ASSISTED_ENTRY_ONLY", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const onCommit = vi.fn();

    renderForm({ onCommit });

    expect(screen.getByTestId("assisted-entry-form")).toHaveTextContent(
      "系統不會讀取此來源頁面",
    );
    expect(screen.queryByLabelText(/密碼|Cookie|Token|API/i)).not.toBeInTheDocument();
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("fails closed for every policy other than ASSISTED_ENTRY_ONLY", () => {
    const onCommit = vi.fn();
    render(
      <AssistedEntryForm
        baseVersion={3}
        draftIdentity={identity}
        onCommit={onCommit}
        originalUrl="https://example.com/listing/1"
        policy="APPROVED_RETRIEVAL"
        sourceId="example"
      />,
    );

    expect(screen.getByTestId("assisted-entry-policy-guard")).toHaveTextContent(
      "人工補錄未開放",
    );
    expect(screen.queryByTestId("assisted-entry-submit")).not.toBeInTheDocument();
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("preserves every field, reason and risk acknowledgement across close and reload", async () => {
    const onCommit = vi.fn();
    const onCancel = vi.fn();
    const first = renderForm({ onCommit, onCancel });

    fillRequiredDraft();
    fireEvent.change(screen.getByTestId("assisted-entry-listingType"), {
      target: { value: "街邊店面" },
    });
    fireEvent.change(screen.getByTestId("assisted-entry-reason"), {
      target: { value: "依現場招租文件人工補錄" },
    });
    fireEvent.click(screen.getByTestId("assisted-entry-risk-ack"));
    const operationId = operationFromState();

    fireEvent.click(screen.getByRole("button", { name: "關閉（保留草稿）" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    first.unmount();

    renderForm({ onCommit, onCancel });

    expect(screen.getByTestId("assisted-entry-address")).toHaveValue(
      "台北市信義區松仁路 100 號",
    );
    expect(screen.getByTestId("assisted-entry-rent")).toHaveValue(180000);
    expect(screen.getByTestId("assisted-entry-areaPing")).toHaveValue(36.5);
    expect(screen.getByTestId("assisted-entry-listingType")).toHaveValue("街邊店面");
    expect(screen.getByTestId("assisted-entry-reason")).toHaveValue("依現場招租文件人工補錄");
    expect(screen.getByTestId("assisted-entry-risk-ack")).toBeChecked();
    expect(operationFromState()).toBe(operationId);
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("preserves the draft and stable operation ID through network failure and retry", async () => {
    const submissions: AssistedEntrySubmission[] = [];
    const onCommit = vi
      .fn<(submission: AssistedEntrySubmission) => Promise<AssistedEntryCommitResult>>()
      .mockImplementationOnce(async (submission) => {
        submissions.push(submission);
        throw new Error("network offline");
      })
      .mockImplementationOnce(async (submission) => {
        submissions.push(submission);
        return {
          status: "COMMITTED",
          authoritativeVersion: 4,
          correctionIds: ["correction-001"],
        };
      });

    const view = renderForm({ onCommit });
    fillCompleteReviewDraft();
    fireEvent.submit(screen.getByTestId("assisted-entry-form"));

    await waitFor(() =>
      expect(screen.getByTestId("assisted-entry-submit-error")).toHaveTextContent(
        "network offline",
      ),
    );
    expect(screen.getByTestId("assisted-entry-address")).toHaveValue(
      "台北市信義區松仁路 100 號",
    );
    expect(screen.getByTestId("assisted-entry-submit")).toHaveTextContent("相同 operation");

    view.unmount();
    renderForm({ onCommit });
    fireEvent.submit(screen.getByTestId("assisted-entry-form"));

    await waitFor(() => expect(onCommit).toHaveBeenCalledTimes(2));
    expect(submissions[1].operationId).toBe(submissions[0].operationId);
    expect(submissions[1].retrievalAllowed).toBe(false);
    expect(submissions[1].sourcePolicy).toBe("ASSISTED_ENTRY_ONLY");
    expect(submissions[1].requiresIndependentReview).toBe(true);
    expect(submissions[1].fields.rent).toBe(180000);
    expect(submissions[1].fields.areaPing).toBe(36.5);

    cleanup();
    renderForm({ onCommit });
    expect(screen.getByTestId("assisted-entry-address")).toHaveValue("");
    expect(screen.getByTestId("assisted-entry-risk-ack")).not.toBeChecked();
  });

  it("preserves inputs on conflict, rebases without data loss, and retries the same operation", async () => {
    const submissions: AssistedEntrySubmission[] = [];
    const onCommit = vi
      .fn<(submission: AssistedEntrySubmission) => Promise<AssistedEntryCommitResult>>()
      .mockImplementationOnce(async (submission) => {
        submissions.push(submission);
        return {
          status: "CONFLICT",
          failure: {
            code: "VERSION_CONFLICT",
            summary: "Server record changed",
            occurredAt: "2026-07-23T06:00:00Z",
            retryable: true,
            currentVersion: 7,
            currentState: "AWAITING_ASSISTED_ENTRY",
            correlationId: "corr-conflict-1",
          },
        };
      })
      .mockImplementationOnce(async (submission) => {
        submissions.push(submission);
        return {
          status: "COMMITTED",
          authoritativeVersion: 8,
          correctionIds: ["correction-008"],
        };
      });

    renderForm({ onCommit });
    fillCompleteReviewDraft();
    fireEvent.submit(screen.getByTestId("assisted-entry-form"));

    await waitFor(() =>
      expect(screen.getByTestId("assisted-entry-submit-error")).toHaveTextContent(
        "VERSION_CONFLICT",
      ),
    );
    expect(screen.getByTestId("assisted-entry-address")).toHaveValue(
      "台北市信義區松仁路 100 號",
    );

    fireEvent.click(screen.getByRole("button", { name: "套用最新版本並保留草稿" }));
    fireEvent.submit(screen.getByTestId("assisted-entry-form"));

    await waitFor(() => expect(onCommit).toHaveBeenCalledTimes(2));
    expect(submissions[0].ifMatchVersion).toBe(3);
    expect(submissions[1].ifMatchVersion).toBe(7);
    expect(submissions[1].operationId).toBe(submissions[0].operationId);
    expect(submissions[1].fields.address).toBe("台北市信義區松仁路 100 號");
  });
});

function renderForm({
  onCommit,
  onCancel,
}: {
  onCommit: (submission: AssistedEntrySubmission) => Promise<AssistedEntryCommitResult>;
  onCancel?: () => void;
}) {
  return render(
    <AssistedEntryForm
      baseVersion={3}
      draftIdentity={identity}
      onCancel={onCancel}
      onCommit={onCommit}
      originalUrl="https://www.591.com.tw/rent-detail-16244102.html"
      policy="ASSISTED_ENTRY_ONLY"
      sourceId="591"
    />,
  );
}

function fillRequiredDraft() {
  fireEvent.change(screen.getByTestId("assisted-entry-address"), {
    target: { value: "台北市信義區松仁路 100 號" },
  });
  fireEvent.change(screen.getByTestId("assisted-entry-rent"), {
    target: { value: "180000" },
  });
  fireEvent.change(screen.getByTestId("assisted-entry-areaPing"), {
    target: { value: "36.5" },
  });
}

function fillCompleteReviewDraft() {
  fillRequiredDraft();
  fireEvent.change(screen.getByTestId("assisted-entry-reason"), {
    target: { value: "依現場招租文件人工補錄" },
  });
  fireEvent.click(screen.getByTestId("assisted-entry-risk-ack"));
}

function operationFromState(): string {
  const text = screen.getByTestId("assisted-entry-draft-state").textContent ?? "";
  return text.match(/operation ([^ ]+)/)?.[1] ?? "";
}
