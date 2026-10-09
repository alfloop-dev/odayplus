import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import { TransferIntakeDialog, usableTransferTargets } from "../TransferIntakeDialog";

// Explicit mounted UI fixtures. These do not authorize identities or prove API writes.
const targets = [
  { id: "00000000-0000-0000-0000-000000000102", name: "Fixture reviewer", role: "site-reviewer" },
  { id: "00000000-0000-0000-0000-000000000105", name: "Fixture steward", role: "data-steward" },
];
const props = {
  busy: false, error: null, resourceVersion: 14, onClose: vi.fn(), onSubmit: vi.fn(),
  record: { id: "IN-TARGET-FIXTURE", version: 71, assignmentId: "00000000-0000-0000-0000-000000000301", owner: "Fixture owner" } as AssistedIntake,
};
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function fillDraft() {
  fireEvent.change(screen.getByTestId("transfer-handoff-note"), { target: { value: "keep this handoff" } });
  fireEvent.click(screen.getByTestId("transfer-risk-ack"));
}

describe("Transfer target authority fail-closed boundary", () => {
  it("does not invent a target when no directory results exist, despite a valid resource token", () => {
    render(<TransferIntakeDialog {...props} />);
    expect(screen.getByTestId("transfer-record-version")).toHaveTextContent("v14");
    expect(screen.getByTestId("transfer-targets-unavailable")).toHaveTextContent("TRANSFER_TARGETS_UNAVAILABLE");
    expect(screen.getByTestId("transfer-target-select")).toBeDisabled();
    expect(screen.getByTestId("transfer-risk-ack")).toBeDisabled();
    expect(screen.getByTestId("transfer-submit-btn")).toBeDisabled();
    expect(screen.queryByText(/吳孟哲|治理覆核佇列/)).toBeNull();
    fireEvent.click(screen.getByTestId("transfer-submit-btn"));
    expect(props.onSubmit).not.toHaveBeenCalled();
  });

  it("rejects display IDs, queues, ambiguous UUIDs and incomplete target metadata", () => {
    expect(usableTransferTargets([
      { ...targets[0], id: "actor-mgr" }, { ...targets[0], id: "gov-queue" },
      { ...targets[0], name: " " }, { ...targets[0], role: " " },
    ])).toEqual([]);
    expect(usableTransferTargets([targets[0], { ...targets[0], name: "another identity" }])).toEqual([]);
    expect(usableTransferTargets(targets)).toEqual(targets);
    render(<TransferIntakeDialog {...props} targetOptions={[{ ...targets[0], id: "actor-mgr" }]} />);
    expect(screen.getByTestId("transfer-submit-btn")).toBeDisabled();
  });

  it("keeps a disappearing selection unavailable instead of silently transferring to the first remaining person", () => {
    const { rerender } = render(<TransferIntakeDialog {...props} targetOptions={targets} />);
    fireEvent.change(screen.getByTestId("transfer-target-select"), { target: { value: targets[1].id } });
    fillDraft();
    expect(screen.getByTestId("transfer-risk-ack")).toBeChecked();
    rerender(<TransferIntakeDialog {...props} targetOptions={[targets[0]]} />);
    expect(screen.getByTestId("transfer-target-select")).toHaveValue("");
    expect(screen.getByTestId("transfer-targets-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("transfer-risk-ack")).not.toBeChecked();
    expect(screen.getByTestId("transfer-handoff-note")).toHaveValue("keep this handoff");
    expect(screen.getByTestId("transfer-submit-btn")).toBeDisabled();
    fireEvent.click(screen.getByTestId("transfer-submit-btn"));
    expect(props.onSubmit).not.toHaveBeenCalled();
    // Even a restored target requires renewed acknowledgement.
    rerender(<TransferIntakeDialog {...props} targetOptions={targets} />);
    expect(screen.getByTestId("transfer-risk-ack")).not.toBeChecked();
    fireEvent.click(screen.getByTestId("transfer-submit-btn"));
    expect(props.onSubmit).not.toHaveBeenCalled();
  });

  it.each(["name", "role"] as const)("requires new consent when the selected target %s changes", (field) => {
    const { rerender } = render(<TransferIntakeDialog {...props} targetOptions={targets} />);
    fillDraft();
    rerender(<TransferIntakeDialog {...props} targetOptions={[{ ...targets[0], [field]: `changed ${field}` }, targets[1]]} />);
    expect(screen.getByTestId("transfer-risk-ack")).not.toBeChecked();
    fireEvent.click(screen.getByTestId("transfer-submit-btn"));
    expect(props.onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("transfer-error-panel")).toHaveTextContent("風險");
  });

  it("binds consent and payload to the exact selected target, retaining draft through same-target resource refresh", () => {
    const { rerender } = render(<TransferIntakeDialog {...props} targetOptions={targets} />);
    fillDraft();
    fireEvent.change(screen.getByTestId("transfer-target-select"), { target: { value: targets[1].id } });
    expect(screen.getByTestId("transfer-risk-ack")).not.toBeChecked();
    fireEvent.click(screen.getByTestId("transfer-submit-btn"));
    expect(props.onSubmit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("transfer-risk-ack"));
    rerender(<TransferIntakeDialog {...props} resourceVersion={15} targetOptions={[...targets]} />);
    expect(screen.getByTestId("transfer-risk-ack")).toBeChecked();
    fireEvent.click(screen.getByTestId("transfer-submit-btn"));
    expect(props.onSubmit).toHaveBeenCalledWith({
      target_owner_subject_id: targets[1].id, target_owner_role: targets[1].role,
      handoff_note: "keep this handoff", riskSummary: expect.stringContaining(targets[1].name), riskAcknowledged: true,
    });
  });
});
