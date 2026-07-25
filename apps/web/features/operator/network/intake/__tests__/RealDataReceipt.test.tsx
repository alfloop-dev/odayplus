import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { AssistedIntake, AssignmentReceipt } from "@oday-plus/openapi-client";
import { DurableReceiptPanel } from "../DurableReceiptPanel";

const intake: AssistedIntake = {
  id: "intake-backend-101",
  originalUrl: "https://example.com/listing/101",
  canonicalUrl: "https://example.com/listing/101",
  submitter: "subject-backend-1",
  owner: "",
  heatZoneId: null,
  stage: "NEEDS_REVIEW",
  sourceId: "source-backend-1",
  policy: "APPROVED_RETRIEVAL",
  policyLabel: "APPROVED_RETRIEVAL",
  policyReason: "policy-backend-1",
  rawSnapshot: null,
  snapshotId: null,
  capturedAt: null,
  parserVersion: "parser-backend-1",
  correlationId: null,
  parsedFields: {},
  matchResult: null,
  auditEvents: [],
  version: 4,
};

const assignmentReceipt: AssignmentReceipt = {
  assignment_id: "assignment-from-backend-1",
  audit_event_id: "audit-from-backend-1",
  due_at: "2026-07-25T12:00:00Z",
  owner_subject_id: "owner-from-backend-1",
  status: "ASSIGNED",
  version: 2,
};

afterEach(cleanup);

describe("DurableReceiptPanel real-data contract", () => {
  it("never fabricates receipt identifiers, verification, checksum, or WORM evidence", () => {
    render(<DurableReceiptPanel record={intake} />);

    expect(screen.getByTestId("receipt-verification-status")).toHaveTextContent("UNVERIFIED");
    expect(screen.getByTestId("receipt-unavailable-state")).toHaveTextContent("UNAVAILABLE");
    expect(screen.getByTestId("receipt-copy-button")).toBeDisabled();
    expect(screen.getByTestId("receipt-export-button")).toBeDisabled();
    expect(screen.getByTestId("receipt-checksum")).toHaveTextContent("UNAVAILABLE");
    expect(screen.getByTestId("receipt-worm-state")).toHaveTextContent("UNAVAILABLE");

    const rendered = document.body.textContent ?? "";
    expect(rendered).not.toContain(`CORR-${intake.id}`);
    expect(rendered).not.toContain(`AUD-${intake.id}`);
    expect(rendered).not.toContain(`LISTING-${intake.id}`);
    expect(rendered).not.toContain("SITE-NONE");
    expect(rendered).not.toContain("e3b0c44298fc1c149afbf4c8996fb924");
    expect(rendered).not.toContain("SECURE WORM LOGGED");
    expect(rendered).not.toContain("Verified Valid");
  });

  it("shows exact backend receipt fields but keeps export disabled until backend verification exists", () => {
    render(
      <DurableReceiptPanel
        assignmentReceipt={assignmentReceipt}
        record={intake}
      />,
    );

    expect(screen.getByTestId("receipt-asg-id")).toHaveTextContent(
      assignmentReceipt.assignment_id,
    );
    expect(screen.getByTestId("receipt-audit-event-id")).toHaveTextContent(
      assignmentReceipt.audit_event_id,
    );
    expect(screen.getByTestId("receipt-verification-status")).toHaveTextContent("UNVERIFIED");
    expect(screen.getByTestId("receipt-export-button")).toBeDisabled();
  });

  it("enables export only for a backend receipt with explicit verification metadata", () => {
    render(
      <DurableReceiptPanel
        assignmentReceipt={assignmentReceipt}
        record={intake}
        verification={{
          status: "VERIFIED",
          checksum: "sha256:checksum-from-backend",
          verifiedAt: "2026-07-24T15:00:00Z",
          wormState: "LOGGED_BY_BACKEND",
        }}
      />,
    );

    expect(screen.getByTestId("receipt-verification-status")).toHaveTextContent("VERIFIED");
    expect(screen.getByTestId("receipt-checksum")).toHaveTextContent(
      "sha256:checksum-from-backend",
    );
    expect(screen.getByTestId("receipt-worm-state")).toHaveTextContent(
      "LOGGED_BY_BACKEND",
    );
    expect(screen.getByTestId("receipt-copy-button")).toBeEnabled();
    expect(screen.getByTestId("receipt-export-button")).toBeEnabled();
  });
});
