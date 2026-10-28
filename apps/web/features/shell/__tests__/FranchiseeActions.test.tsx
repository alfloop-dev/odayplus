import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  OdpApiClient,
  OdpApiError,
  type ShellFranchiseeReportResponse,
  type ShellWriteResponse,
} from "@oday-plus/openapi-client";
import { FranchiseeActions } from "../FranchiseeActions.tsx";

const refreshMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    refresh: refreshMock,
  }),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function reportReceipt(
  overrides: Partial<ShellFranchiseeReportResponse["report"]> = {},
): ShellFranchiseeReportResponse {
  return {
    report: {
      reportId: "FR-TEST0001",
      category: "equipment",
      message: "reported",
      status: "received",
      createdAt: "2026-10-25T20:00:00Z",
      storeId: "store-1",
      ...overrides,
    },
    auditEvent: {
      id: "AUD-SHELL-1",
      auditEventId: "audit-1",
      occurredAt: "2026-10-25T20:00:00Z",
      actorRoleId: "franchisee",
      action: "report",
      message: "加盟主回報",
      metadata: {},
    },
    correlationId: "corr-1",
    idempotentReplay: false,
  };
}

function ackReceipt(): ShellWriteResponse {
  return {
    auditEvent: {
      id: "AUD-SHELL-ACK-1",
      auditEventId: "audit-ack-1",
      occurredAt: "2026-10-25T20:00:00Z",
      actorRoleId: "franchisee",
      action: "acknowledge",
      message: "加盟主確認通知",
      metadata: {},
    },
    correlationId: "corr-ack-1",
    idempotentReplay: false,
  };
}

function maintenanceError(url = "/api/v1/operator/shell/franchisee/reports"): OdpApiError {
  return new OdpApiError("Service Unavailable", {
    status: 503,
    url,
    body: { detail: "後端暫時無法寫入。" },
  });
}

beforeEach(() => {
  refreshMock.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("FranchiseeActions report draft lifecycle and idempotency discipline", () => {
  it("preserves a newly typed draft when an older in-flight submission resolves and submits the new draft with a fresh key", async () => {
    const firstSubmit = deferred<ShellFranchiseeReportResponse>();
    const secondSubmit = deferred<ShellFranchiseeReportResponse>();
    const submitSpy = vi
      .spyOn(OdpApiClient.prototype, "submitShellFranchiseeReport")
      .mockImplementationOnce(() => firstSubmit.promise)
      .mockImplementationOnce(() => secondSubmit.promise);

    render(
      <FranchiseeActions
        mode="report"
        storeId="store-1"
        categories={["equipment", "staffing", "supply"]}
      />,
    );

    const categorySelect = screen.getByTestId("franchisee-report-category");
    const messageInput = screen.getByTestId("franchisee-report-message");
    const submitButton = screen.getByTestId("franchisee-report-submit");

    fireEvent.change(messageInput, { target: { value: "Report A: ice machine leak" } });
    fireEvent.click(submitButton);

    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(1));
    expect(submitSpy.mock.calls[0]?.[0]).toEqual({
      category: "equipment",
      message: "Report A: ice machine leak",
      storeId: "store-1",
    });
    const firstKey = submitSpy.mock.calls[0]?.[1]?.idempotencyKey;
    expect(firstKey).toMatch(/^shell-fr-report-store-1-/);
    expect(submitButton).toBeDisabled();
    expect(submitButton).toHaveTextContent("送出中…");

    // User starts a new draft B while submission A is still pending.
    fireEvent.change(categorySelect, { target: { value: "staffing" } });
    fireEvent.change(messageInput, { target: { value: "Report B: evening shift shortage" } });

    await act(async () => {
      firstSubmit.resolve(
        reportReceipt({
          category: "equipment",
          message: "Report A: ice machine leak",
          storeId: "store-1",
        }),
      );
    });

    // Old completion A must not clear or overwrite the unsubmitted draft B.
    expect(categorySelect).toHaveValue("staffing");
    expect(messageInput).toHaveValue("Report B: evening shift shortage");
    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(submitButton).not.toBeDisabled();
    expect(submitButton).toHaveTextContent("送出回報");

    fireEvent.click(submitButton);

    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(2));
    expect(submitSpy.mock.calls[1]?.[0]).toEqual({
      category: "staffing",
      message: "Report B: evening shift shortage",
      storeId: "store-1",
    });
    const secondKey = submitSpy.mock.calls[1]?.[1]?.idempotencyKey;
    expect(secondKey).toMatch(/^shell-fr-report-store-1-/);
    expect(secondKey).not.toBe(firstKey);

    await act(async () => {
      secondSubmit.resolve(
        reportReceipt({
          reportId: "FR-TEST0002",
          category: "staffing",
          message: "Report B: evening shift shortage",
          storeId: "store-1",
        }),
      );
    });

    expect(messageInput).toHaveValue("");
    expect(refreshMock).toHaveBeenCalledTimes(2);
  });

  it("preserves an A -> B -> A edited draft when the original A submission resolves so equal text is not treated as unedited", async () => {
    const firstSubmit = deferred<ShellFranchiseeReportResponse>();
    const secondSubmit = deferred<ShellFranchiseeReportResponse>();
    const submitSpy = vi
      .spyOn(OdpApiClient.prototype, "submitShellFranchiseeReport")
      .mockImplementationOnce(() => firstSubmit.promise)
      .mockImplementationOnce(() => secondSubmit.promise);

    render(
      <FranchiseeActions
        mode="report"
        storeId="store-1"
        categories={["equipment", "staffing"]}
      />,
    );

    const messageInput = screen.getByTestId("franchisee-report-message");
    const submitButton = screen.getByTestId("franchisee-report-submit");

    fireEvent.change(messageInput, { target: { value: "Same text A" } });
    fireEvent.click(submitButton);

    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(1));
    const firstKey = submitSpy.mock.calls[0]?.[1]?.idempotencyKey;

    // Edit A -> B -> A while the first request is still in flight.
    fireEvent.change(messageInput, { target: { value: "Intermediate draft B" } });
    fireEvent.change(messageInput, { target: { value: "Same text A" } });

    await act(async () => {
      firstSubmit.resolve(reportReceipt({ message: "Same text A" }));
    });

    expect(messageInput).toHaveValue("Same text A");
    expect(submitButton).not.toBeDisabled();

    fireEvent.click(submitButton);
    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(2));
    const secondKey = submitSpy.mock.calls[1]?.[1]?.idempotencyKey;
    expect(secondKey).toMatch(/^shell-fr-report-store-1-/);
    expect(secondKey).not.toBe(firstKey);

    await act(async () => {
      secondSubmit.resolve(reportReceipt({ reportId: "FR-TEST0002", message: "Same text A" }));
    });
    expect(messageInput).toHaveValue("");
  });

  it("clears an unchanged draft on success, refreshes the router, and mints a new key for the next submission", async () => {
    const submitSpy = vi
      .spyOn(OdpApiClient.prototype, "submitShellFranchiseeReport")
      .mockResolvedValueOnce(reportReceipt({ reportId: "FR-TEST0001", message: "First report" }))
      .mockResolvedValueOnce(reportReceipt({ reportId: "FR-TEST0002", message: "Second report" }));

    render(
      <FranchiseeActions
        mode="report"
        storeId="store-1"
        categories={["equipment", "other"]}
      />,
    );

    const messageInput = screen.getByTestId("franchisee-report-message");
    const submitButton = screen.getByTestId("franchisee-report-submit");

    expect(submitButton).toBeDisabled();
    fireEvent.change(messageInput, { target: { value: "First report" } });
    expect(submitButton).not.toBeDisabled();

    fireEvent.click(submitButton);

    await waitFor(() => expect(screen.getByTestId("franchisee-report-sent")).toBeVisible());
    expect(messageInput).toHaveValue("");
    expect(submitButton).toBeDisabled();
    expect(refreshMock).toHaveBeenCalledTimes(1);

    const firstKey = submitSpy.mock.calls[0]?.[1]?.idempotencyKey;

    fireEvent.change(messageInput, { target: { value: "Second report" } });
    fireEvent.click(submitButton);

    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(2));
    const secondKey = submitSpy.mock.calls[1]?.[1]?.idempotencyKey;
    expect(secondKey).toMatch(/^shell-fr-report-store-1-/);
    expect(secondKey).not.toBe(firstKey);
    expect(refreshMock).toHaveBeenCalledTimes(2);
  });

  it("reuses the exact idempotency key when retrying an unchanged failed or uncertain report, but mints a new key when message or category changes", async () => {
    const submitSpy = vi
      .spyOn(OdpApiClient.prototype, "submitShellFranchiseeReport")
      .mockRejectedValueOnce(maintenanceError())
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(maintenanceError())
      .mockResolvedValueOnce(
        reportReceipt({
          category: "staffing",
          message: "Revised report after failure",
        }),
      );

    render(
      <FranchiseeActions
        mode="report"
        storeId="store-1"
        categories={["equipment", "staffing"]}
      />,
    );

    const categorySelect = screen.getByTestId("franchisee-report-category");
    const messageInput = screen.getByTestId("franchisee-report-message");
    const submitButton = screen.getByTestId("franchisee-report-submit");

    fireEvent.change(messageInput, { target: { value: "Original failed report" } });
    fireEvent.click(submitButton);

    expect(await screen.findByTestId("franchisee-report-error")).toHaveTextContent(
      "後端暫時無法寫入。",
    );
    expect(messageInput).toHaveValue("Original failed report");
    expect(refreshMock).not.toHaveBeenCalled();

    const initialKey = submitSpy.mock.calls[0]?.[1]?.idempotencyKey;
    expect(initialKey).toMatch(/^shell-fr-report-store-1-/);

    // Retry unchanged submission: must reuse exact same key.
    fireEvent.click(submitButton);
    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(2));
    expect(submitSpy.mock.calls[1]?.[0]).toEqual({
      category: "equipment",
      message: "Original failed report",
      storeId: "store-1",
    });
    expect(submitSpy.mock.calls[1]?.[1]?.idempotencyKey).toBe(initialKey);

    // Edit message after uncertain transport error: must mint a new operation key.
    fireEvent.change(messageInput, { target: { value: "Revised report after failure" } });
    fireEvent.click(submitButton);
    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(3));
    const messageEditedKey = submitSpy.mock.calls[2]?.[1]?.idempotencyKey;
    expect(submitSpy.mock.calls[2]?.[0]).toEqual({
      category: "equipment",
      message: "Revised report after failure",
      storeId: "store-1",
    });
    expect(messageEditedKey).toMatch(/^shell-fr-report-store-1-/);
    expect(messageEditedKey).not.toBe(initialKey);

    // Edit category only after failure: category edits are also content edits and must mint a new key.
    fireEvent.change(categorySelect, { target: { value: "staffing" } });
    fireEvent.click(submitButton);
    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(4));
    const categoryEditedKey = submitSpy.mock.calls[3]?.[1]?.idempotencyKey;
    expect(submitSpy.mock.calls[3]?.[0]).toEqual({
      category: "staffing",
      message: "Revised report after failure",
      storeId: "store-1",
    });
    expect(categoryEditedKey).toMatch(/^shell-fr-report-store-1-/);
    expect(categoryEditedKey).not.toBe(messageEditedKey);
    expect(categoryEditedKey).not.toBe(initialKey);

    await waitFor(() => expect(screen.getByTestId("franchisee-report-sent")).toBeVisible());
    expect(messageInput).toHaveValue("");
    expect(refreshMock).toHaveBeenCalledTimes(1);
  });

  it("isolates draft state, in-flight outcomes, and operation keys across storeId prop transitions", async () => {
    const store1Submit = deferred<ShellFranchiseeReportResponse>();
    const store2Submit = deferred<ShellFranchiseeReportResponse>();
    const submitSpy = vi
      .spyOn(OdpApiClient.prototype, "submitShellFranchiseeReport")
      .mockImplementationOnce(() => store1Submit.promise)
      .mockImplementationOnce(() => store2Submit.promise);

    const view = render(
      <FranchiseeActions
        mode="report"
        storeId="store-1"
        categories={["equipment", "staffing"]}
      />,
    );

    fireEvent.change(screen.getByTestId("franchisee-report-message"), {
      target: { value: "Store 1 pending report" },
    });
    fireEvent.click(screen.getByTestId("franchisee-report-submit"));

    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(1));
    expect(submitSpy.mock.calls[0]?.[0]).toEqual({
      category: "equipment",
      message: "Store 1 pending report",
      storeId: "store-1",
    });
    const store1Key = submitSpy.mock.calls[0]?.[1]?.idempotencyKey;
    expect(store1Key).toMatch(/^shell-fr-report-store-1-/);

    // Transition component to store-2 while store-1 submission is still in flight.
    view.rerender(
      <FranchiseeActions
        mode="report"
        storeId="store-2"
        categories={["equipment", "staffing"]}
      />,
    );

    const store2Message = screen.getByTestId("franchisee-report-message");
    const store2Button = screen.getByTestId("franchisee-report-submit");
    expect(store2Message).toHaveValue("");
    expect(store2Button).toHaveTextContent("送出回報");
    expect(screen.queryByTestId("franchisee-report-sent")).not.toBeInTheDocument();

    fireEvent.change(store2Message, { target: { value: "Store 2 active draft" } });
    expect(store2Button).not.toBeDisabled();

    // Resolve store-1's old request; it must not clear store-2's draft or show store-1's outcome on store-2.
    await act(async () => {
      store1Submit.resolve(
        reportReceipt({
          storeId: "store-1",
          message: "Store 1 pending report",
        }),
      );
    });

    expect(store2Message).toHaveValue("Store 2 active draft");
    expect(screen.queryByTestId("franchisee-report-sent")).not.toBeInTheDocument();
    expect(refreshMock).not.toHaveBeenCalled();

    fireEvent.click(store2Button);
    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(2));
    expect(submitSpy.mock.calls[1]?.[0]).toEqual({
      category: "equipment",
      message: "Store 2 active draft",
      storeId: "store-2",
    });
    const store2Key = submitSpy.mock.calls[1]?.[1]?.idempotencyKey;
    expect(store2Key).toMatch(/^shell-fr-report-store-2-/);
    expect(store2Key).not.toBe(store1Key);

    await act(async () => {
      store2Submit.resolve(
        reportReceipt({
          reportId: "FR-STORE2",
          storeId: "store-2",
          message: "Store 2 active draft",
        }),
      );
    });

    expect(store2Message).toHaveValue("");
    expect(screen.getByTestId("franchisee-report-sent")).toBeVisible();
    expect(refreshMock).toHaveBeenCalledTimes(1);
  });

  it("keeps notification acknowledgement working with retry key reuse and router refresh on success", async () => {
    const ackSpy = vi
      .spyOn(OdpApiClient.prototype, "acknowledgeShellFranchiseeNotification")
      .mockRejectedValueOnce(
        maintenanceError("/api/v1/operator/shell/franchisee/acknowledgement"),
      )
      .mockResolvedValueOnce(ackReceipt());

    const view = render(
      <FranchiseeActions
        mode="acknowledge"
        notificationId="notif-1"
        acknowledged={false}
        storeId="store-1"
        categories={["equipment"]}
      />,
    );

    const ackButton = screen.getByTestId("franchisee-ack-notif-1");
    fireEvent.click(ackButton);

    expect(await screen.findByTestId("franchisee-ack-error-notif-1")).toHaveTextContent(
      "後端暫時無法寫入。",
    );
    expect(refreshMock).not.toHaveBeenCalled();

    const firstAckKey = ackSpy.mock.calls[0]?.[1]?.idempotencyKey;
    expect(ackSpy.mock.calls[0]?.[0]).toEqual({
      notificationId: "notif-1",
      storeId: "store-1",
    });
    expect(firstAckKey).toMatch(/^shell-fr-ack-notif-1-/);

    fireEvent.click(ackButton);
    await waitFor(() => expect(ackSpy).toHaveBeenCalledTimes(2));
    expect(ackSpy.mock.calls[1]?.[1]?.idempotencyKey).toBe(firstAckKey);
    expect(refreshMock).toHaveBeenCalledTimes(1);

    view.rerender(
      <FranchiseeActions
        mode="acknowledge"
        notificationId="notif-1"
        acknowledged={true}
        storeId="store-1"
        categories={["equipment"]}
      />,
    );
    expect(screen.getByTestId("franchisee-acked-notif-1")).toHaveTextContent("✓ 已確認");
  });
});
