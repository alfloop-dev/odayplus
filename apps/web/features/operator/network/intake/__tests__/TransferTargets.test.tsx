import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OdpApiClient, type AssistedIntake } from "@oday-plus/openapi-client";
import { TransferIntakeDialog, usableTransferTargets, useAssignmentTransferTargets } from "../TransferIntakeDialog";

// Explicit mounted UI fixtures. These do not authorize identities or prove API writes.
const targets = [
  { id: "00000000-0000-0000-0000-000000000102", name: "Fixture reviewer", role: "site-reviewer" },
  { id: "00000000-0000-0000-0000-000000000105", name: "Fixture steward", role: "data-steward" },
];
const props = {
  busy: false, error: null, resourceVersion: 14, onClose: vi.fn(), onSubmit: vi.fn(),
  record: { id: "IN-TARGET-FIXTURE", version: 71, assignmentId: "00000000-0000-0000-0000-000000000301", owner: "Fixture owner" } as AssistedIntake,
};
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

const assignmentId = props.record.assignmentId!;
function directoryResponse(id = assignmentId, version = 14, items: unknown = targets) {
  return new Response(JSON.stringify({ assignment_id: id, assignment_version: version, items }),
    { status: 200, headers: { "content-type": "application/json" } });
}

describe("Typed resource-bound directory reads", () => {
  it("loads only for an enabled transfer and binds a minimal typed response to the resource", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL) => directoryResponse());
    vi.stubGlobal("fetch", fetcher);
    const client = new OdpApiClient({ baseUrl: "http://localhost" });
    const { result, rerender } = renderHook(({ enabled }) =>
      useAssignmentTransferTargets(client, assignmentId, 14, enabled), { initialProps: { enabled: false } });
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.options).toEqual([]);
    rerender({ enabled: true });
    expect(result.current.options).toEqual([]);
    await waitFor(() => expect(result.current.state).toBe("ready"));
    expect(result.current.options).toEqual(targets);
    expect(String(fetcher.mock.calls[0]?.[0])).toContain(`/api/v1/assignments/${assignmentId}/transfer-targets`);
    rerender({ enabled: false });
    expect(result.current.options).toEqual([]);
  });

  it.each([
    ["foreign resource", () => directoryResponse("00000000-0000-0000-0000-000000000999")],
    ["stale version", () => directoryResponse(assignmentId, 15)],
    ["display actor", () => directoryResponse(assignmentId, 14, [{ ...targets[0], id: "actor-mgr" }])],
    ["duplicate actor", () => directoryResponse(assignmentId, 14, [targets[0], targets[0]])],
    ["queue role", () => directoryResponse(assignmentId, 14, [{ ...targets[0], role: "gov-queue" }])],
    ["malformed list", () => directoryResponse(assignmentId, 14, {})],
    ["denied", () => new Response(JSON.stringify({ code: "SCOPE_DENIED" }), { status: 403 })],
    ["unavailable", () => new Response("unavailable", { status: 503 })],
  ])("fails closed on %s rather than retaining recipients", async (_, response) => {
    vi.stubGlobal("fetch", vi.fn(async () => response()));
    const client = new OdpApiClient({ baseUrl: "http://localhost" });
    const { result } = renderHook(() => useAssignmentTransferTargets(client, assignmentId, 14, true));
    await waitFor(() => expect(result.current.state).toBe("error"));
    expect(result.current.options).toEqual([]);
    expect(result.current.error).not.toBeNull();
  });

  it("drops old results on refetch and ignores late reads after a resource or client change", async () => {
    let resolveOld!: (value: Response) => void;
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveOld = resolve; }))
      .mockImplementation(async () => directoryResponse(assignmentId, 15));
    vi.stubGlobal("fetch", fetcher);
    const client = new OdpApiClient({ baseUrl: "http://localhost" });
    const { result, rerender } = renderHook(({ version, activeClient }) =>
      useAssignmentTransferTargets(activeClient, assignmentId, version, true),
    { initialProps: { version: 14, activeClient: client } });
    rerender({ version: 15, activeClient: client });
    expect(result.current.options).toEqual([]);
    await waitFor(() => expect(result.current.state).toBe("ready"));
    await act(async () => { resolveOld(directoryResponse()); });
    expect(result.current.options).toEqual(targets);
    expect(result.current.state).toBe("ready");
    act(() => result.current.refreshTargets());
    expect(result.current.options).toEqual([]);
    await waitFor(() => expect(result.current.state).toBe("ready"));
    rerender({ version: 15, activeClient: new OdpApiClient({ baseUrl: "http://localhost" }) });
    expect(result.current.options).toEqual([]);
    await waitFor(() => expect(result.current.state).toBe("ready"));
  });
});

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
