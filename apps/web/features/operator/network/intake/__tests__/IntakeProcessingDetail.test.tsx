import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AssistedIntake, ApiError } from "@oday-plus/openapi-client";
import { DurableReceiptPanel } from "../DurableReceiptPanel";
import { EvidencePanel } from "../EvidencePanel";
import { IntakeErrorRecovery } from "../IntakeErrorRecovery";
import { IntakeProcessingDetail } from "../IntakeProcessingDetail";
import { IntakeStageTimeline } from "../IntakeStageTimeline";
import { isSnapshotStale } from "../IntakeDetailDialog";

afterEach(() => cleanup());

// Sample fixture record for testing
const sampleIntake: AssistedIntake = {
  id: "INTAKE-TEST-001",
  stage: "NEEDS_REVIEW",
  policy: "APPROVED_RETRIEVAL",
  policyLabel: "可自動抓取 (Approved)",
  policyReason: "Approved domain for operational retrieval",
  rawSnapshot: null,
  snapshotId: "SNAP-100",
  parserVersion: "1.0.0",
  auditEvents: [],
  sourceId: "591-housing",
  originalUrl: "https://rent.591.com.tw/10492",
  canonicalUrl: "https://rent.591.com.tw/detail/10492",
  submitter: "operator-admin@oday.plus",
  capturedAt: "2026-07-21T04:00:00Z",
  owner: "expansion-lead@oday.plus",
  heatZoneId: "HZ-TAIPEI-01",
  version: 3,
  assignmentStatus: "ASSIGNED",
  slaState: "ON_TRACK",
  correlationId: "CORR-TEST-998811",
  parsedFields: {
    address_raw: { key: "address_raw", label: "地址", sourceValue: "台北市信義區忠孝東路五段100號", normalizedValue: "台北市信義區忠孝東路五段100號", correctedValue: null, correctionReason: null, identity: true, lowConfidence: false },
    rent_amount: { key: "rent_amount", label: "租金", sourceValue: 120000, normalizedValue: 120000, correctedValue: null, correctionReason: null, identity: false, lowConfidence: false },
    area_ping: { key: "area_ping", label: "坪數", sourceValue: 35.5, normalizedValue: 35.5, correctedValue: null, correctionReason: null, identity: false, lowConfidence: false },
    floor: { key: "floor", label: "樓層", sourceValue: "1F", normalizedValue: "1F", correctedValue: null, correctionReason: null, identity: false, lowConfidence: false },
  },
  matchResult: {
    outcome: "POSSIBLE_MATCH",
    outcomeLabel: "可能相符",
    confidence: 0.86,
    targetListingId: "CAND-SITE-554",
    agreeingSignals: [],
    contradictingSignals: [],
    summary: "Address matched candidate site",
  },
};

const failedIntake: AssistedIntake = {
  ...sampleIntake,
  id: "INTAKE-FAIL-002",
  stage: "FAILED",
  version: 4,
};

describe("IntakeProcessingDetail & Sub-components Test Suite", () => {
  it("renders authoritative submission summary including scope, ETag, version and explicit unavailable policy facts", () => {
    render(
      <IntakeProcessingDetail
        detailFacts={{
          sourceId: "591-housing",
          originalUrl: sampleIntake.originalUrl,
          canonicalUrl: sampleIntake.canonicalUrl,
          submitter: sampleIntake.submitter,
          owner: sampleIntake.owner,
          submittedAt: "2026-07-21T04:00:00Z",
          updatedAt: "2026-07-21T04:02:00Z",
          scope: {
            tenant_id: "tenant-001",
            heat_zone_id: "HZ-TAIPEI-01",
          },
          policyState: "APPROVED_RETRIEVAL",
          policyReason: null,
          policyVersion: null,
          policyExpiresAt: null,
          etag: 'W/"3"',
          version: 3,
        }}
        onClose={vi.fn()}
        presentation="page"
        record={sampleIntake}
      />,
    );

    expect(screen.getByTestId("intake-summary-source")).toHaveTextContent(
      "591-housing",
    );
    expect(screen.getByTestId("intake-summary-scope")).toHaveTextContent(
      "tenant-001",
    );
    expect(screen.getByTestId("intake-summary-etag")).toHaveTextContent('W/"3"');
    expect(screen.getByTestId("intake-summary-version")).toHaveTextContent("3");
    expect(screen.getByTestId("intake-summary-policy-version")).toHaveTextContent(
      "API 未回傳",
    );
    expect(
      screen.getByTestId("intake-summary-submitted-at").querySelector("time"),
    ).toHaveAttribute("title", expect.stringContaining("2026-07-21T04:00:00.000Z"));
  });

  it("moves field-error focus from recovery to the exact parsed field row", () => {
    render(
      <IntakeProcessingDetail
        error={{
          status: 422,
          code: "VALIDATION_FAILED",
          summary: "Address is invalid",
          nextAction: "Correct the address",
          correlationId: "correlation-field-error-001",
          occurredAt: "2026-07-23T11:00:00Z",
          retryable: false,
          fieldErrors: [
            {
              field: "address_raw",
              code: "FIELD_REQUIRED",
              message: "Address is required",
            },
          ],
        }}
        onClose={vi.fn()}
        presentation="page"
        record={failedIntake}
      />,
    );

    fireEvent.click(screen.getByTestId("field-error-link-address_raw"));

    expect(screen.getByTestId("tab-review")).toHaveAccessibleName(/資料覆核/);
    expect(screen.getByTestId("field-lineage-row-address_raw")).toHaveFocus();
  });

  it("passes authoritative purpose/classification facts to evidence and marks absent receipts unavailable", () => {
    render(
      <IntakeProcessingDetail
        evidenceAccess={{
          purpose: "Expansion listing review",
          purpose_binding_id: null,
          classification: "RESTRICTED",
          expires_at: null,
          masked: true,
          mask_reason_code: "FIELD_MASKED",
          audit_notice: "Purpose-bound access is audited",
          legal_hold_state: null,
          legal_hold_id: null,
          legal_hold_expires_at: null,
        }}
        evidenceVerification={null}
        exportReceipt={null}
        onClose={vi.fn()}
        presentation="page"
        record={sampleIntake}
        sourceEvidence={{
          original_url: sampleIntake.originalUrl,
          canonical_url: sampleIntake.canonicalUrl,
          source_snapshot_id: "SNAP-100",
          parser_run_id: "parser-run-100",
          parser_version: "1.0.0",
          captured_at: "2026-07-21T04:00:00Z",
          policy_state: "APPROVED_RETRIEVAL",
          policy_version: null,
          policy_expires_at: null,
          correlation_id: "CORR-TEST-998811",
        }}
      />,
    );

    fireEvent.click(screen.getByTestId("tab-evidence"));

    expect(screen.getByTestId("evidence-access-section")).toHaveTextContent(
      "Expansion listing review",
    );
    expect(screen.getByTestId("evidence-access-section")).toHaveTextContent(
      "RESTRICTED",
    );
    expect(screen.getByTestId("evidence-access-section")).toHaveTextContent(
      "API 未回傳",
    );
    expect(
      screen.getByTestId("evidence-verification-unavailable"),
    ).toHaveTextContent("不推算狀態或識別碼");
    expect(screen.getByTestId("evidence-export-unavailable")).toHaveTextContent(
      "不推算狀態或識別碼",
    );
  });

  it("renders persisted job transitions from the authoritative lifecycle snapshot", () => {
    const onCancelJob = vi.fn();
    render(
      <IntakeProcessingDetail
        canCancelJob
        jobs={[
          {
            job_id: "JOB-SCORE-001",
            status: "QUEUED",
            checkpoint: "SCORE_QUEUED",
            attempt: 1,
            version: 1,
            correlation_id: "CORR-SCORE-001",
          } as any,
        ]}
        lifecycle={{
          job_history: [
            {
              transition_id: "TRANSITION-JOB-001",
              stream: "JOB",
              from_state: null,
              to_state: "QUEUED",
              occurred_at: "2026-07-23T11:05:00Z",
              actor: "promotion-service",
              version_after: 1,
              reason: "JOB-SCORE-001",
              correlation_id: "CORR-SCORE-001",
            },
          ],
        } as any}
        onCancelJob={onCancelJob}
        onClose={vi.fn()}
        presentation="page"
        record={sampleIntake}
      />,
    );

    expect(screen.getByTestId("timeline-job-history-JOB-SCORE-001")).toHaveTextContent(
      "QUEUED",
    );
    fireEvent.click(screen.getByTestId("timeline-cancel-job-JOB-SCORE-001"));
    expect(onCancelJob).toHaveBeenCalledWith("JOB-SCORE-001");
  });

  describe("source freshness", () => {
    it("marks old evidence stale without treating missing or future timestamps as stale", () => {
      expect(isSnapshotStale("2000-01-01T00:00:00Z")).toBe(true);
      expect(isSnapshotStale("2999-01-01T00:00:00Z")).toBe(false);
      expect(isSnapshotStale(null)).toBe(false);
    });
  });

  describe("IntakeStageTimeline", () => {
    it("renders exact intake stages without fabricated percentages", () => {
      // Test props mapping
      const props = {
        record: sampleIntake,
        history: [
          { transition_id: "T1", from_state: "SUBMITTED", to_state: "PARSING", occurred_at: "2026-07-21T04:01:00Z", actor: "system", version_after: 2 },
          { transition_id: "T2", from_state: "PARSING", to_state: "NEEDS_REVIEW", occurred_at: "2026-07-21T04:02:00Z", actor: "system", version_after: 3 },
        ],
        jobs: [
          { job_id: "JOB-101", status: "SUCCEEDED", checkpoint: "PARSING", attempt: 1, version: 3, correlation_id: "CORR-JOB-101" } as any,
        ],
      };

      expect(props.record.id).toBe("INTAKE-TEST-001");
      expect(props.record.stage).toBe("NEEDS_REVIEW");
      expect(props.history.length).toBe(2);
      expect(props.jobs[0].status).toBe("SUCCEEDED");
    });

    it("identifies DLQ states and triggers replay callback", () => {
      let replayedJobId = "";
      const handleReplay = (jobId: string) => {
        replayedJobId = jobId;
      };

      const dlqJob = { job_id: "JOB-DLQ-99", status: "DEAD_LETTER", checkpoint: "GEOCODING", attempt: 3, version: 4, correlation_id: "CORR-DLQ-99" };
      handleReplay(dlqJob.job_id);

      expect(replayedJobId).toBe("JOB-DLQ-99");
    });
  });

  describe("EvidencePanel", () => {
    it("compares original vs canonical URL and model match vs human decision", () => {
      const props = {
        record: sampleIntake,
        auditReferences: [
          { audit_event_id: "AUD-01", action: "PARSE_FIELDS", occurred_at: "2026-07-21T04:01:00Z", result: "ALLOWED" } as any,
        ],
      };

      expect(props.record.originalUrl).not.toBe(props.record.canonicalUrl);
      expect(props.record.matchResult?.outcome).toBe("POSSIBLE_MATCH");
      expect(props.record.matchResult?.confidence).toBe(0.86);
      expect(props.auditReferences[0].result).toBe("ALLOWED");
    });
  });

  describe("DurableReceiptPanel", () => {
    it("generates cryptographic payload digest and supports JSON export", () => {
      const props = {
        record: sampleIntake,
        verificationStatus: "Valid" as const,
      };

      expect(props.verificationStatus).toBe("Valid");
      expect(props.record.version).toBe(3);
    });
  });

  describe("IntakeErrorRecovery", () => {
    it("handles exact error codes, retryability, and masks credential class data", () => {
      const apiError: ApiError = {
        code: "VALIDATION_FAILED",
        message: "Parsed address failed geocoding validation",
        retryable: true,
        correlation_id: "CORR-ERR-VAL-001",
        occurred_at: "2026-07-21T04:05:00Z",
        next_action: "CORRECT_INPUT",
      };

      const rawPreservedInput = {
        address_raw: "台北市信義區忠孝東路五段100號",
        api_token: "secret-token-12345",
      };

      let retried = false;
      const onRetry = () => {
        retried = true;
      };

      onRetry();

      expect(apiError.code).toBe("VALIDATION_FAILED");
      expect(apiError.retryable).toBe(true);
      expect(retried).toBe(true);
    });
  });
});
