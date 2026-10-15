import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import { TransferIntakeDialog, transferTargetKey } from "../TransferIntakeDialog";
import { PauseSlaDialog } from "../PauseSlaDialog";
import { toIntakeApiError } from "../intakeClient";

const record = { id: "10000000-0000-4000-8000-000000000001", version: 91, owner: "Original owner" } as AssistedIntake;
const target = { id: "10000000-0000-4000-8000-000000000002", name: "Synthetic target", role: "data-steward" };
afterEach(cleanup);

describe("authoritative transfer target selection", () => {
  it.each([null, 0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])("requires a usable resource version (%s)", (resourceVersion) => {
    const onSubmit = vi.fn();
    const { unmount } = render(<TransferIntakeDialog busy={false} error={null} onClose={vi.fn()} onSubmit={onSubmit} record={record} targetOptions={[target]} resourceVersion={resourceVersion} />);
    fireEvent.change(screen.getByTestId("transfer-target-select"), { target: { value: transferTargetKey(target) } });
    expect(screen.getByTestId("transfer-record-version")).toHaveTextContent("尚未取得");
    expect(screen.getByTestId("transfer-submit-btn")).toBeDisabled();
    unmount();
    render(<PauseSlaDialog busy={false} error={null} onClose={vi.fn()} onSubmit={onSubmit} record={record} resourceVersion={resourceVersion} />);
    expect(screen.getByTestId("pause-record-version")).toHaveTextContent("尚未取得");
    expect(screen.getByTestId("pause-submit-btn")).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("has no fabricated production targets and refuses submission without a directory result", () => {
    const onSubmit = vi.fn();
    render(<TransferIntakeDialog busy={false} error={null} onClose={vi.fn()} onSubmit={onSubmit} record={record} resourceVersion={7} />);
    expect(screen.getByTestId("transfer-targets-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("transfer-target-select")).toBeDisabled();
    expect(screen.getByTestId("transfer-submit-btn")).toBeDisabled();
    expect(screen.getByTestId("transfer-record-version")).toHaveTextContent("v7");
    expect(screen.queryByText("吳孟哲（展店主管）")).toBeNull();
    fireEvent.click(screen.getByTestId("transfer-submit-btn"));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("requires an explicit subject/role choice and never substitutes a revoked target", () => {
    const onSubmit = vi.fn();
    const props = { busy: false, error: null, onClose: vi.fn(), onSubmit, record, resourceVersion: 7 };
    const { rerender } = render(<TransferIntakeDialog {...props} targetOptions={[target]} />);
    expect(screen.getByTestId("transfer-submit-btn")).toBeDisabled();
    fireEvent.change(screen.getByTestId("transfer-target-select"), { target: { value: transferTargetKey(target) } });
    fireEvent.change(screen.getByTestId("transfer-handoff-note"), { target: { value: "Preserved operator handoff" } });
    fireEvent.click(screen.getByTestId("transfer-risk-ack"));
    expect(screen.getByTestId("transfer-submit-btn")).not.toBeDisabled();
    const otherRole = { ...target, role: "expansion-manager" };
    rerender(<TransferIntakeDialog {...props} targetOptions={[otherRole]} />);
    expect(screen.getByRole("alert")).toHaveTextContent("原選取對象已無法轉交");
    expect(screen.getByTestId("transfer-submit-btn")).toBeDisabled();
    expect(screen.getByTestId("transfer-handoff-note")).toHaveValue("Preserved operator handoff");
    expect(screen.getByTestId("transfer-risk-ack")).toBeChecked();
    fireEvent.click(screen.getByTestId("transfer-submit-btn"));
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId("transfer-target-select"), { target: { value: transferTargetKey(otherRole) } });
    fireEvent.click(screen.getByTestId("transfer-submit-btn"));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ target_owner_subject_id: target.id, target_owner_role: otherRole.role, handoff_note: "Preserved operator handoff" }));
  });

  it("disables a previously loaded target while the server directory is refreshing or unavailable", () => {
    const props = { busy: false, error: null, onClose: vi.fn(), onSubmit: vi.fn(), record, resourceVersion: 7, targetOptions: [target] };
    const { rerender } = render(<TransferIntakeDialog {...props} />);
    fireEvent.change(screen.getByTestId("transfer-target-select"), { target: { value: transferTargetKey(target) } });
    rerender(<TransferIntakeDialog {...props} targetsLoading />);
    expect(screen.getByTestId("transfer-submit-btn")).toBeDisabled();
    rerender(<TransferIntakeDialog {...props} targetsError="Directory unavailable" />);
    expect(screen.getByTestId("transfer-submit-btn")).toBeDisabled();
    expect(screen.getByTestId("transfer-targets-unavailable")).toHaveTextContent("Directory unavailable");
  });

  it("does not claim that an unacknowledged network operation was never written", () => {
    for (const error of [new TypeError("Failed to fetch"), new DOMException("Timed out", "TimeoutError")]) {
      const result = toIntakeApiError(error);
      expect(result.summary).toContain("操作結果尚未確認");
      expect(result.summary).not.toContain("未寫入");
      expect(result.nextAction).toContain("同一 Idempotency-Key");
    }
  });
});
