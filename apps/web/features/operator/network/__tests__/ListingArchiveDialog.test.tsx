import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ListingArchiveDialog } from "../ListingArchiveDialog";
afterEach(cleanup);
const request = { listingId: "real-shaped-source", label: "Recorded location", idempotencyKey: "stable-key", ifMatch: 'W/"8"' };

it("starts blank, rejects blank reason, cancels without writes, and submits only entered reason", () => {
  const onClose = vi.fn(); const onSubmit = vi.fn();
  const view = render(<ListingArchiveDialog request={request} onClose={onClose} onSubmit={onSubmit} />);
  expect(screen.getByTestId("listing-archive-reason")).toHaveValue("");
  fireEvent.click(screen.getByTestId("listing-archive-submit")); expect(onSubmit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByTestId("listing-archive-cancel")); expect(onClose).toHaveBeenCalledOnce();
  view.rerender(<ListingArchiveDialog request={request} onClose={onClose} onSubmit={onSubmit} />);
  fireEvent.change(screen.getByTestId("listing-archive-reason"), { target: { value: "  Recorded retention-compatible reason  " } });
  fireEvent.click(screen.getByTestId("listing-archive-submit"));
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith("Recorded retention-compatible reason");
});

it("preserves reason and blocks dismissal or edits while actual write outcome is pending", () => {
  const onClose = vi.fn(); const onSubmit = vi.fn();
  const view = render(<ListingArchiveDialog request={request} onClose={onClose} onSubmit={onSubmit} />);
  fireEvent.change(screen.getByTestId("listing-archive-reason"), { target: { value: "Real entered reason" } });
  view.rerender(<ListingArchiveDialog request={request} busy onClose={onClose} onSubmit={onSubmit} />);
  for (const id of ["listing-archive-reason", "listing-archive-close", "listing-archive-cancel", "listing-archive-submit"]) {
    expect(screen.getByTestId(id)).toBeDisabled();
  }
  fireEvent.keyDown(document, { key: "Escape" }); fireEvent.mouseDown(screen.getByTestId("listing-archive-dialog"));
  expect(onClose).not.toHaveBeenCalled(); expect(onSubmit).not.toHaveBeenCalled();
  view.rerender(<ListingArchiveDialog request={request} onClose={onClose} onSubmit={onSubmit}
    error={{ status: 0, code: "unknown-outcome", summary: "尚無法確認", occurredAt: "recorded-time", correlationId: "recorded-correlation",
      nextAction: "Retain same request", retryable: true }} />);
  expect(screen.getByTestId("listing-archive-reason")).toHaveValue("Real entered reason");
  expect(screen.getByRole("alert")).toHaveTextContent("recorded-correlation");
});
