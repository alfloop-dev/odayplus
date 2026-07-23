import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import { AddListingFromUrlDialog } from "../AddListingFromUrlDialog";

const baseProps = {
  busy: false,
  error: null,
  onClose: vi.fn(),
  ownerLabel: "actor-expansion-manager",
  scopeLabel: "HeatZone HZ-01",
  submitterLabel: "展店主管",
  tenantLabel: "tenant-tw",
};

function receipt(overrides: Partial<AssistedIntake> = {}): AssistedIntake {
  return {
    id: "INTAKE-URL-001",
    originalUrl: "https://listings.example.com/property/123?utm_source=test",
    canonicalUrl: "https://listings.example.com/property/123",
    submitter: "actor-expansion-manager",
    owner: "actor-expansion-manager",
    heatZoneId: "HZ-01",
    stage: "CHECKING_IDENTITY",
    sourceId: "source-example",
    policy: "APPROVED_RETRIEVAL",
    policyLabel: "核准單頁擷取",
    policyReason: "Source registry policy v4 is active.",
    rawSnapshot: null,
    snapshotId: null,
    capturedAt: null,
    parserVersion: "pending",
    correlationId: "corr-submit-001",
    parsedFields: {},
    matchResult: null,
    auditEvents: [
      {
        id: "AUD-SUBMIT-001",
        occurredAt: "2026-07-23T08:00:00Z",
        actorRoleId: "expansion-manager",
        actorName: "actor-expansion-manager",
        action: "intake.submitted",
        targetId: "INTAKE-URL-001",
        message: "URL submitted",
        correlationId: "corr-submit-001",
      },
    ],
    version: 1,
    ...overrides,
  };
}

describe("AddListingFromUrlDialog", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows submitter, tenant, scope, owner, source-policy authority and canonical evidence", () => {
    render(<AddListingFromUrlDialog {...baseProps} onSubmit={vi.fn()} />);

    fireEvent.change(screen.getByTestId("intake-url-input"), {
      target: {
        value:
          "https://listings.example.com/property/123?utm_source=campaign&ref=operator&locale=zh-TW",
      },
    });

    expect(screen.getByTestId("intake-submitter")).toHaveTextContent(
      "送件人 展店主管",
    );
    expect(screen.getByTestId("intake-submitter")).toHaveTextContent(
      "Tenant tenant-tw · Scope HeatZone HZ-01",
    );
    expect(screen.getByTestId("intake-submitter")).toHaveTextContent(
      "初始 owner actor-expansion-manager",
    );
    expect(screen.getByTestId("intake-source-preview")).toHaveTextContent(
      "送出後由伺服器判定",
    );
    expect(screen.getByTestId("intake-url-evidence-preview")).toHaveTextContent(
      "utm_source=campaign",
    );
    expect(screen.getByTestId("intake-canonical-preview")).not.toHaveTextContent(
      "utm_source",
    );
    expect(screen.getByTestId("intake-canonical-preview")).toHaveTextContent(
      "locale=zh-TW",
    );
    expect(screen.getByTestId("intake-canonical-preview")).not.toHaveTextContent(
      "ref=operator",
    );
  });

  it("rejects invalid URLs before calling the server", () => {
    const onSubmit = vi.fn();
    render(<AddListingFromUrlDialog {...baseProps} onSubmit={onSubmit} />);

    fireEvent.change(screen.getByTestId("intake-url-input"), {
      target: { value: "invalid-url-string" },
    });
    fireEvent.click(screen.getByTestId("intake-submit-button"));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("intake-add-error")).toHaveTextContent(
      "請確認網址格式",
    );
  });

  it("submits the original URL and selected HeatZone exactly once", async () => {
    let resolveSubmit:
      | ((value: AssistedIntake | PromiseLike<AssistedIntake>) => void)
      | undefined;
    const onSubmit = vi.fn(
      () =>
        new Promise<AssistedIntake>((resolve) => {
          resolveSubmit = resolve;
        }),
    );
    render(
      <AddListingFromUrlDialog
        {...baseProps}
        defaultHeatZoneId="HZ-01"
        onSubmit={onSubmit}
      />,
    );

    fireEvent.change(screen.getByTestId("intake-url-input"), {
      target: {
        value:
          "https://listings.example.com/property/123?utm_source=test",
      },
    });
    fireEvent.click(screen.getByTestId("intake-submit-button"));
    fireEvent.click(screen.getByTestId("intake-submit-button"));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith({
      url: "https://listings.example.com/property/123?utm_source=test",
      heatZoneId: "HZ-01",
    });
    expect(screen.getByTestId("intake-submit-button")).toBeDisabled();
    expect(screen.getByLabelText("關閉")).toBeDisabled();

    resolveSubmit?.(receipt());
    await waitFor(() =>
      expect(screen.getByTestId("intake-submission-receipt")).toBeVisible(),
    );
  });

  it("renders only the authoritative server receipt and durable intake link", async () => {
    const onSubmit = vi.fn().mockResolvedValue(receipt());
    render(<AddListingFromUrlDialog {...baseProps} onSubmit={onSubmit} />);

    fireEvent.change(screen.getByTestId("intake-url-input"), {
      target: {
        value:
          "https://listings.example.com/property/123?utm_source=test",
      },
    });
    fireEvent.click(screen.getByTestId("intake-submit-button"));

    const serverReceipt = await screen.findByTestId("intake-submission-receipt");
    expect(serverReceipt).toHaveTextContent(
      "Intake INTAKE-URL-001 · version 1 · CHECKING_IDENTITY",
    );
    expect(serverReceipt).toHaveTextContent(
      "Source source-example · Policy APPROVED_RETRIEVAL",
    );
    expect(serverReceipt).toHaveTextContent("Correlation corr-submit-001");
    expect(serverReceipt).toHaveTextContent(
      "Canonical https://listings.example.com/property/123",
    );
    expect(screen.getByTestId("intake-open-created")).toHaveAttribute(
      "href",
      "/w/expansion/listings/intake/INTAKE-URL-001",
    );
  });

  it("navigates an exact duplicate to the existing Listing, never the intake", async () => {
    const openExisting = vi.fn();
    const duplicate = receipt({
      id: "INTAKE-DUP-001",
      stage: "READY",
      matchResult: {
        outcome: "EXACT_DUPLICATE",
        outcomeLabel: "完全重複",
        targetListingId: "LISTING-7788",
        confidence: 1,
        agreeingSignals: [
          {
            key: "canonicalUrl",
            label: "Canonical URL",
            agrees: true,
            detail: "Canonical URL is identical.",
          },
        ],
        contradictingSignals: [],
        summary: "Canonical URL already belongs to an existing Listing.",
      },
    });
    render(
      <AddListingFromUrlDialog
        {...baseProps}
        onOpenExisting={openExisting}
        onSubmit={vi.fn().mockResolvedValue(duplicate)}
      />,
    );

    fireEvent.change(screen.getByTestId("intake-url-input"), {
      target: { value: duplicate.originalUrl },
    });
    fireEvent.click(screen.getByTestId("intake-submit-button"));

    await screen.findByTestId("intake-exact-duplicate-intercept");
    expect(screen.queryByTestId("intake-open-created")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("intake-open-existing"));
    expect(openExisting).toHaveBeenCalledWith("LISTING-7788");
    expect(openExisting).not.toHaveBeenCalledWith("INTAKE-DUP-001");
  });
});
