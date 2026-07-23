import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type {
  ApiError,
  AssistedIntake,
  AssignmentReceipt,
  CorrectionReceipt,
  DecisionReceipt,
  FieldValue,
  IntakeSubmissionReceipt,
  JobReceipt,
  PromotionDecisionReceipt,
  SlaReceipt,
} from "@oday-plus/openapi-client";
import { afterEach, describe, expect, it } from "vitest";
import { DurableReceiptPanel } from "../DurableReceiptPanel";
import { EvidencePanel } from "../EvidencePanel";
import { IntakeErrorRecovery } from "../IntakeErrorRecovery";
import { StructuredAuditTimeline } from "../StructuredAuditTimeline";
import type {
  AuthoritativeEvidenceReceipt,
  AuthoritativeEvidenceVerification,
  AuthoritativeExportReceipt,
  AuthoritativeGovernedExportAccess,
  AuthoritativeIdentityReceipt,
} from "../evidenceContracts";

afterEach(cleanup);

const record: AssistedIntake = {
  id: "server-intake-001",
  originalUrl: "https://example.com/listings/1?campaign=source",
  canonicalUrl: "https://example.com/listings/1",
  submitter: "server-submitter-001",
  owner: "server-owner-001",
  heatZoneId: "server-zone-001",
  stage: "READY",
  sourceId: "server-source-001",
  policy: "APPROVED_RETRIEVAL",
  policyLabel: "server policy label",
  policyReason: "server policy reason",
  rawSnapshot: null,
  snapshotId: "server-snapshot-001",
  capturedAt: "2026-07-23T10:00:00Z",
  parserVersion: "server-parser-3.2.1",
  correlationId: "server-record-correlation",
  parsedFields: {},
  matchResult: null,
  auditEvents: [],
  version: 7,
};

const verificationFixture: AuthoritativeEvidenceVerification = {
  status: "VERIFIED",
  verified_at: "2026-07-23T10:05:00Z",
  checksum_algorithm: "SHA-256",
  content_sha256: "4f7d9ef63586713d31b739f36c49769c8492ef7b840d5f2b6f4e0f55c1d53aa1",
  signature: "server-signature-001",
  signer_key_version: "kms-key-2026-07-v3",
  worm_sink_id: "worm-receipt-server-001",
  worm_checksum: "worm-checksum-server-001",
  evidence_state: "WORM_COMMITTED",
  audit_event_id: "audit-verification-server-001",
  correlation_id: "correlation-verification-server-001",
};

const exportReceipt: AuthoritativeExportReceipt = {
  export_manifest_id: "export-manifest-server-001",
  requested_by: "privacy-requester-server-001",
  approved_by: "privacy-reviewer-server-002",
  purpose: "Expansion decision review",
  scope: { intake_id: "server-intake-001" },
  field_mask: { masking_profile: "restricted-contact-redacted" },
  source_snapshot_ids: ["server-snapshot-001"],
  audit_event_ids: ["audit-export-server-001"],
  object_uri: "gs://tenant-evidence/export-manifest-server-001.json",
  content_sha256: "a2142f2ac3da52a0a885629b98d70c654abdf2bc49fa3b280082be5af44b63ed",
  watermark: "EXPORT export-manifest-server-001",
  expires_at: "2026-07-23T14:00:00Z",
  created_at: "2026-07-23T10:00:00Z",
  download_evidence_id: "download-evidence-server-001",
  signer_key_version: "kms-key-2026-07-v3",
  worm_sink_id: "worm-export-server-001",
  worm_checksum: "worm-export-checksum-server-001",
};

describe("DurableReceiptPanel authoritative-only rendering", () => {
  it("renders nothing when the API supplied no receipt fields", () => {
    const { container } = render(<DurableReceiptPanel record={record} />);

    expect(screen.queryByTestId("intake-durable-receipt-panel")).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
    expect(container.textContent).not.toContain("CORR-server-intake-001");
    expect(container.textContent).not.toContain("AUD-server-intake-001");
    expect(container.textContent).not.toContain("LISTING-server-intake-001");
    expect(container.textContent).not.toContain("SITE-");
    expect(container.textContent).not.toContain(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
    expect(container.textContent).not.toContain("Verified Valid");
    expect(container.textContent).not.toContain("WORM");
  });

  it("does not invent audit, verification, Listing, Candidate, hash, or WORM values", () => {
    const submission: IntakeSubmissionReceipt = {
      intake_id: "submission-server-001",
      state: "SUBMITTED",
      version: 1,
      job_id: "job-server-submit-001",
      correlation_id: "correlation-server-submit-001",
      submitted_at: "2026-07-23T09:00:00Z",
    };

    const { container } = render(
      <DurableReceiptPanel record={record} submissionReceipt={submission} />,
    );

    expect(screen.getByTestId("receipt-submission")).toHaveTextContent(
      "correlation-server-submit-001",
    );
    expect(screen.queryByTestId("receipt-verification")).not.toBeInTheDocument();
    expect(container.textContent).not.toContain("AUD-");
    expect(container.textContent).not.toContain("LISTING-");
    expect(container.textContent).not.toContain("SITE-");
    expect(container.textContent).not.toContain("sha256:e3b0");
    expect(container.textContent).not.toContain("WORM");
  });

  it("renders every receipt family using only server-issued values", () => {
    const submission: IntakeSubmissionReceipt = {
      intake_id: "submission-server-001",
      state: "SUBMITTED",
      version: 1,
      job_id: "job-server-submit-001",
      correlation_id: "correlation-server-submit-001",
      submitted_at: "2026-07-23T09:00:00Z",
    };
    const assignment: AssignmentReceipt = {
      assignment_id: "assignment-server-001",
      status: "CLAIMED",
      owner_subject_id: "owner-server-001",
      due_at: "2026-07-24T09:00:00Z",
      version: 3,
      audit_event_id: "audit-assignment-server-001",
    };
    const sla: SlaReceipt = {
      sla_instance_id: "sla-server-001",
      state: "PAUSED",
      due_at: "2026-07-24T09:00:00Z",
      paused_duration_seconds: 3600,
      version: 4,
      audit_event_id: "audit-sla-server-001",
      correlation_id: "correlation-sla-server-001",
    };
    const correction: CorrectionReceipt = {
      correction_id: "correction-server-001",
      status: "APPLIED",
      intake_id: "server-intake-001",
      listing_revision_id: "revision-server-001",
      version: 8,
      audit_event_id: "audit-correction-server-001",
      correlation_id: "correlation-correction-server-001",
    };
    const decision: DecisionReceipt = {
      decision_id: "decision-server-001",
      status: "EXECUTED",
      resource_versions: { listing: 9 },
      job_id: "job-decision-server-001",
      audit_event_id: "audit-decision-server-001",
      correlation_id: "correlation-decision-server-001",
    };
    const identity: AuthoritativeIdentityReceipt = {
      identity_receipt_id: "identity-receipt-server-001",
      operation: "UNMERGE",
      status: "EXECUTED",
      decision_id: "identity-decision-server-001",
      identity_edge_ids: ["edge-server-001", "edge-server-002"],
      resource_versions: { property: 11 },
      audit_event_id: "audit-identity-server-001",
      correlation_id: "correlation-identity-server-001",
      occurred_at: "2026-07-23T10:10:00Z",
    };
    const promotion: PromotionDecisionReceipt = {
      promotion_decision_id: "promotion-server-001",
      intake_id: "server-intake-001",
      listing_id: "listing-server-001",
      status: "CANDIDATE_CREATED",
      decision_type: "STANDARD",
      version: 5,
      audit_event_id: "audit-promotion-server-001",
      correlation_id: "correlation-promotion-server-001",
      candidate_site_id: "candidate-server-001",
      proposer_subject_id: "proposer-server-001",
      reviewer_subject_id: "reviewer-server-002",
      site_score_job_id: "score-job-server-001",
    };
    const job: JobReceipt = {
      job_id: "score-job-server-001",
      status: "SUCCEEDED",
      checkpoint: "SCORE_QUEUED",
      attempt: 2,
      version: 3,
      correlation_id: "correlation-job-server-001",
    };
    const evidence: AuthoritativeEvidenceReceipt = {
      evidence_receipt_id: "evidence-receipt-server-001",
      status: "CAPTURED",
      created_at: "2026-07-23T10:00:00Z",
      source_snapshot_id: "server-snapshot-001",
      parser_run_id: "parser-run-server-001",
      audit_event_id: "audit-evidence-server-001",
      correlation_id: "correlation-evidence-server-001",
      version: 2,
    };

    render(
      <DurableReceiptPanel
        submissionReceipt={submission}
        assignmentReceipt={assignment}
        slaReceipt={sla}
        correctionReceipts={[correction]}
        decisionReceipt={decision}
        identityReceipts={[identity]}
        promotionReceipt={promotion}
        jobReceipts={[job]}
        evidenceReceipts={[evidence]}
        exportReceipts={[exportReceipt]}
        verification={verificationFixture}
      />,
    );

    expect(screen.getByTestId("receipt-submission")).toHaveTextContent(
      "submission-server-001",
    );
    expect(screen.getByTestId("receipt-assignment")).toHaveTextContent(
      "audit-assignment-server-001",
    );
    expect(screen.getByTestId("receipt-sla")).toHaveTextContent(
      "correlation-sla-server-001",
    );
    expect(
      screen.getByTestId("receipt-correction-correction-server-001"),
    ).toHaveTextContent("revision-server-001");
    expect(screen.getByTestId("receipt-decision")).toHaveTextContent(
      "decision-server-001",
    );
    expect(
      screen.getByTestId("receipt-identity-identity-receipt-server-001"),
    ).toHaveTextContent("edge-server-001, edge-server-002");
    expect(screen.getByTestId("receipt-promotion")).toHaveTextContent(
      "candidate-server-001",
    );
    expect(screen.getByTestId("receipt-job-score-job-server-001")).toHaveTextContent(
      "score-job-server-001",
    );
    expect(
      screen.getByTestId("receipt-evidence-evidence-receipt-server-001"),
    ).toHaveTextContent("parser-run-server-001");
    expect(
      screen.getByTestId("receipt-export-export-manifest-server-001"),
    ).toHaveTextContent("worm-export-server-001");
  });

  it("shows checksum, signature, verification and WORM only from an API fixture", () => {
    render(<DurableReceiptPanel verification={verificationFixture} />);

    expect(screen.getByTestId("receipt-verification-status")).toHaveTextContent(
      "VERIFIED",
    );
    expect(screen.getByTestId("receipt-checksum")).toHaveTextContent(
      verificationFixture.content_sha256!,
    );
    expect(screen.getByTestId("receipt-worm-sink-id")).toHaveTextContent(
      "worm-receipt-server-001",
    );
    expect(screen.getByTestId("receipt-verification")).toHaveTextContent(
      "server-signature-001",
    );
  });

  it("never creates a local export or copy action from receipt data", () => {
    render(<DurableReceiptPanel exportReceipts={[exportReceipt]} />);

    expect(
      screen.getByTestId("receipt-export-export-manifest-server-001"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("receipt-copy-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("receipt-export-button")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("receipt-governed-export-link"),
    ).not.toBeInTheDocument();
  });

  it("offers only a server-issued governed export with matching permission and purpose", () => {
    const governedExportAccess: AuthoritativeGovernedExportAccess = {
      export_manifest_id: exportReceipt.export_manifest_id,
      allowed: true,
      permission: "evidence.export.download",
      purpose: exportReceipt.purpose,
      download_url: "/api/v1/governed-exports/export-manifest-server-001/content",
    };
    const { rerender } = render(
      <DurableReceiptPanel
        exportReceipts={[exportReceipt]}
        governedExportAccess={governedExportAccess}
      />,
    );

    expect(screen.getByTestId("receipt-governed-export-link")).toHaveAttribute(
      "href",
      governedExportAccess.download_url,
    );
    expect(screen.queryByTestId("receipt-copy-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("receipt-export-button")).not.toBeInTheDocument();

    rerender(
      <DurableReceiptPanel
        exportReceipts={[exportReceipt]}
        governedExportAccess={{
          ...governedExportAccess,
          purpose: "Different purpose",
        }}
      />,
    );
    expect(
      screen.queryByTestId("receipt-governed-export-link"),
    ).not.toBeInTheDocument();

    rerender(
      <DurableReceiptPanel
        exportReceipts={[exportReceipt]}
        governedExportAccess={{
          ...governedExportAccess,
          permission: "evidence.export.request",
        }}
      />,
    );
    expect(
      screen.queryByTestId("receipt-governed-export-link"),
    ).not.toBeInTheDocument();

    rerender(
      <DurableReceiptPanel
        exportReceipts={[exportReceipt]}
        governedExportAccess={{
          ...governedExportAccess,
          export_manifest_id: "different-export-manifest",
        }}
      />,
    );
    expect(
      screen.queryByTestId("receipt-governed-export-link"),
    ).not.toBeInTheDocument();

    rerender(
      <DurableReceiptPanel
        exportReceipts={[{
          ...exportReceipt,
          download_evidence_id: null,
        }]}
        governedExportAccess={governedExportAccess}
      />,
    );
    expect(
      screen.queryByTestId("receipt-governed-export-link"),
    ).not.toBeInTheDocument();

    rerender(
      <DurableReceiptPanel
        exportReceipts={[exportReceipt]}
        governedExportAccess={{
          ...governedExportAccess,
          allowed: false,
        }}
      />,
    );
    expect(
      screen.queryByTestId("receipt-governed-export-link"),
    ).not.toBeInTheDocument();
  });
});

describe("EvidencePanel authoritative metadata", () => {
  it("renders source, purpose, classification, expiry, legal hold, export and verification fields", () => {
    render(
      <EvidencePanel
        record={record}
        sourceEvidence={{
          parser_run_id: "parser-run-server-009",
          observed_at: "2026-07-23T09:59:00Z",
          policy_version: "policy-server-v7",
          policy_expires_at: "2026-08-01T00:00:00Z",
        }}
        access={{
          purpose: "Expansion decision review",
          purpose_binding_id: "purpose-binding-server-001",
          classification: "RESTRICTED",
          expires_at: "2026-07-23T12:00:00Z",
          masked: true,
          mask_reason_code: "FIELD_MASKED",
          audit_notice: "Access recorded by server",
          legal_hold_state: "ACTIVE",
          legal_hold_id: "legal-hold-server-001",
        }}
        verification={verificationFixture}
        exportReceipt={exportReceipt}
        etag={'W/"server-etag-v7"'}
      />,
    );

    expect(screen.getByTestId("evidence-parser-run-id")).toHaveTextContent(
      "parser-run-server-009",
    );
    expect(screen.getByTestId("evidence-access-section")).toHaveTextContent(
      "purpose-binding-server-001",
    );
    expect(screen.getByTestId("evidence-access-section")).toHaveTextContent(
      "legal-hold-server-001",
    );
    expect(screen.getByTestId("evidence-verification-status")).toHaveTextContent(
      "VERIFIED",
    );
    expect(screen.getByTestId("evidence-export-result")).toHaveTextContent(
      "EXPORT export-manifest-server-001",
    );
    expect(screen.getByTestId("evidence-etag")).toHaveTextContent(
      'W/"server-etag-v7"',
    );
  });

  it("never infers classification, confidence, parser run or effective value", () => {
    const recordWithOwnerField: AssistedIntake = {
      ...record,
      parsedFields: {
        owner_contact: {
          key: "owner_contact",
          label: "Owner contact",
          sourceValue: "server-field-value",
          normalizedValue: "server-normalized-value",
          correctedValue: null,
          correctionReason: null,
          identity: false,
          lowConfidence: false,
        },
      },
    };
    const { container } = render(<EvidencePanel record={recordWithOwnerField} />);

    expect(container.textContent).not.toContain("CONFIDENTIAL");
    expect(container.textContent).not.toContain("PUBLIC");
    expect(container.textContent).not.toContain("95%");
    expect(container.textContent).not.toContain("PR-RUN-88412");
    expect(screen.queryByTestId("evidence-parser-run-id")).not.toBeInTheDocument();
    const row = screen.getByText("owner_contact").closest("tr");
    expect(row).toHaveTextContent("server-field-value");
    expect(row).toHaveTextContent("server-normalized-value");
  });

  it("masks every value column when the API marks a field masked", () => {
    const fields: FieldValue[] = [{
      field_path: "broker_contact",
      parsed: "private-parsed-value",
      normalized: "private-normalized-value",
      corrected: "private-corrected-value",
      effective: "private-effective-value",
      confidence: 0.8,
      classification: "RESTRICTED",
      masked: true,
      mask_reason_code: "FIELD_MASKED",
    }];

    const { container } = render(<EvidencePanel record={record} fields={fields} />);

    expect(container.textContent).not.toContain("private-parsed-value");
    expect(container.textContent).not.toContain("private-normalized-value");
    expect(container.textContent).not.toContain("private-corrected-value");
    expect(container.textContent).not.toContain("private-effective-value");
    expect(screen.getByTestId("field-mask-broker_contact")).toHaveTextContent(
      "FIELD_MASKED",
    );
  });

  it("forces every evidence field masked when access is globally masked", () => {
    const fields: FieldValue[] = [
      {
        field_path: "broker_contact",
        parsed: "global-private-parsed-one",
        normalized: "global-private-normalized-one",
        corrected: "global-private-corrected-one",
        effective: "global-private-effective-one",
        classification: "RESTRICTED",
        masked: false,
      },
      {
        field_path: "owner_contact",
        parsed: "global-private-parsed-two",
        normalized: "global-private-normalized-two",
        corrected: "global-private-corrected-two",
        effective: "global-private-effective-two",
        classification: "RESTRICTED",
        masked: false,
      },
    ];

    const { container } = render(
      <EvidencePanel
        record={record}
        fields={fields}
        access={{
          masked: true,
          mask_reason_code: "ACCESS_MASKED",
        }}
      />,
    );

    for (const field of fields) {
      expect(container.textContent).not.toContain(String(field.parsed));
      expect(container.textContent).not.toContain(String(field.normalized));
      expect(container.textContent).not.toContain(String(field.corrected));
      expect(container.textContent).not.toContain(String(field.effective));
      expect(
        screen.getByTestId(`field-mask-${field.field_path}`),
      ).toHaveTextContent("ACCESS_MASKED");
    }
  });
});

describe("StructuredAuditTimeline", () => {
  it("renders the complete structured persisted event", () => {
    render(
      <StructuredAuditTimeline
        events={[{
          id: "event-server-001",
          audit_event_id: "audit-event-server-001",
          occurred_at: "2026-07-23T10:12:00Z",
          actor_name: "Operator Server",
          actor_role_id: "expansion-manager",
          action: "intake.decision.executed",
          result: "SUCCEEDED",
          reason: "Verified source and comparison",
          reason_code: "HUMAN_CONFIRMED",
          before_after: {
            rent: { before: 120000, after: 110000 },
          },
          source_snapshot_id: "snapshot-server-001",
          parser_run_id: "parser-run-server-001",
          parser_version: "parser-server-v4",
          related_ids: {
            listing_id: "listing-server-001",
            candidate_site_id: "candidate-server-001",
          },
          correlation_id: "correlation-audit-server-001",
          version: 12,
          evidence_state: "WORM_COMMITTED",
          message: "Server persisted decision",
        }]}
      />,
    );

    const event = screen.getByTestId("audit-event-event-server-001");
    expect(event).toHaveTextContent("Operator Server");
    expect(event).toHaveTextContent("expansion-manager");
    expect(event).toHaveTextContent("Verified source and comparison");
    expect(event).toHaveTextContent("snapshot-server-001");
    expect(event).toHaveTextContent("parser-run-server-001");
    expect(event).toHaveTextContent("listing-server-001");
    expect(event).toHaveTextContent("candidate-server-001");
    expect(event).toHaveTextContent("correlation-audit-server-001");
    expect(event).toHaveTextContent("WORM_COMMITTED");
    expect(screen.getByTestId("audit-before-after-event-server-001")).toHaveTextContent(
      "120000",
    );
    expect(screen.getByTestId("audit-before-after-event-server-001")).toHaveTextContent(
      "110000",
    );
  });
});

const namedErrorFamilies = [
  "PRECONDITION_REQUIRED",
  "VERSION_CONFLICT",
  "IDEMPOTENCY_KEY_REUSED",
  "OWNER_CONFLICT",
  "REVIEW_CONFLICT",
  "WORK_INCOMPLETE",
  "LEGAL_HOLD_CONFLICT",
  "SELF_REVIEW_DENIED",
  "SOURCE_POLICY_DENIED",
  "SCOPE_DENIED",
  "OWNERSHIP_REQUIRED",
  "CORRECTION_INVALID",
  "RISK_ACKNOWLEDGEMENT_REQUIRED",
  "RETRIEVAL_TIMEOUT",
  "PAGE_REMOVED",
  "AUTH_WALL",
  "BOT_CHALLENGE",
  "PARSER_PARTIAL",
  "PARSER_RETRYABLE",
  "PARSER_PERMANENT",
  "STALE_SNAPSHOT",
  "QUARANTINED",
  "RETRY_BUDGET_EXHAUSTED",
  "DEAD_LETTER",
] as const;

describe("IntakeErrorRecovery complete contract", () => {
  it.each(namedErrorFamilies)("renders the complete %s recovery envelope", (code) => {
    const error = {
      status: code === "PRECONDITION_REQUIRED" ? 428 : 409,
      code,
      summary: `Server summary for ${code}`,
      nextAction: `Server next action for ${code}`,
      correlationId: `correlation-${code}`,
      occurredAt: "2026-07-23T11:00:00Z",
      retryable: code.includes("RETRY") || code === "VERSION_CONFLICT",
    };

    render(
      <IntakeErrorRecovery
        error={error}
        recovery={{
          operation: `operation-${code}`,
          current_state: "NEEDS_REVIEW",
          current_version: 14,
          server_value: { owner: "server-owner-002" },
          preserved_input: { reason: "operator draft" },
        }}
      />,
    );

    expect(screen.getByTestId("error-code")).toHaveTextContent(code);
    expect(screen.getByTestId("error-message")).toHaveTextContent(
      `Server summary for ${code}`,
    );
    expect(screen.getByTestId("error-correlation-id")).toHaveTextContent(
      `correlation-${code}`,
    );
    expect(screen.getByTestId("error-occurred-at")).toHaveTextContent(
      "2026-07-23T11:00:00Z",
    );
    expect(screen.getByTestId("error-current-state")).toHaveTextContent(
      "NEEDS_REVIEW",
    );
    expect(screen.getByTestId("error-current-version")).toHaveTextContent("14");
    expect(screen.getByTestId("error-operation")).toHaveTextContent(
      `operation-${code}`,
    );
    expect(screen.getByTestId("error-server-value")).toHaveTextContent(
      "server-owner-002",
    );
    expect(screen.getByTestId("error-next-action")).toHaveTextContent(
      `Server next action for ${code}`,
    );
    cleanup();
  });

  it("renders exact conflict fields and field errors from the API", () => {
    const error: ApiError = {
      code: "VERSION_CONFLICT",
      message: "Server version has changed",
      retryable: true,
      correlation_id: "correlation-conflict-server-001",
      occurred_at: "2026-07-23T11:05:00Z",
      next_action: "REFRESH",
      current_version: 15,
      field_errors: [{
        field: "rent",
        code: "SERVER_VALUE_CHANGED",
        message: "Rent changed on the server",
      }],
      retry_after_seconds: 3,
    };

    render(
      <IntakeErrorRecovery
        error={error}
        recovery={{
          operation: "apply correction",
          current_state: "NEEDS_REVIEW",
          server_value: { rent: 115000 },
          preserved_input: {
            rent: 110000,
            nested: {
              bearer_token: "must-not-render",
              note: "keep-this-draft",
            },
          },
        }}
      />,
    );

    expect(screen.getByText("SERVER_VALUE_CHANGED")).toBeInTheDocument();
    expect(screen.getByText("Rent changed on the server")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("error-toggle-preserved-input"));
    const draft = screen.getByTestId("error-preserved-input-box");
    expect(draft).toHaveTextContent("keep-this-draft");
    expect(draft).toHaveTextContent("[REDACTED]");
    expect(draft).not.toHaveTextContent("must-not-render");
  });

  it("renders nothing without an authoritative error and invents no fallback metadata", () => {
    const { container, rerender } = render(
      <IntakeErrorRecovery
        error={null}
        stage="FAILED"
        correlationId="record-correlation-server-001"
      />,
    );

    expect(container).toBeEmptyDOMElement();

    rerender(
      <IntakeErrorRecovery
        error={{
          status: 500,
          code: "SERVER_ONLY_CODE",
          summary: "Server-only summary",
          nextAction: "Server-only next action",
          correlationId: null,
          occurredAt: "",
          retryable: false,
        }}
      />,
    );

    expect(container.textContent).not.toContain("CORR-ERR-991204");
    expect(container.textContent).not.toContain("ERR_PARSE_MALFORMED_HTML");
    expect(container.textContent).not.toContain("v1");
    expect(screen.getByTestId("error-correlation-id")).toHaveAttribute(
      "data-authoritative",
      "missing",
    );
    expect(screen.getByTestId("error-occurred-at")).toHaveAttribute(
      "data-authoritative",
      "missing",
    );
  });
});
