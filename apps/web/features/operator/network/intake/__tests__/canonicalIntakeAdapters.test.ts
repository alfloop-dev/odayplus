import { describe, expect, it } from "vitest";
import type {
  CanonicalIntakeRuntimeDetail,
  IntakeInboxPage,
} from "@oday-plus/openapi-client";
import {
  canonicalCorrectionsByField,
  canonicalDetailToRecord,
  canonicalDetailPresentationFacts,
  canonicalDetailToLifecycle,
  canonicalIdentityReceipt,
  canonicalIdentityWorkflow,
  canonicalPageToInbox,
  canonicalSensitiveEvidenceAccess,
  canonicalSourceEvidence,
} from "../canonicalIntakeAdapters";

function runtimeDetail(): CanonicalIntakeRuntimeDetail {
  return {
    intake_id: "intake-001",
    state: "READY",
    intake_method: "URL",
    source_id: "SRC-SYNTHETIC",
    match_outcome: "NEW",
    submitted_by: "staff-001",
    assigned_to: "manager-001",
    due_at: null,
    submitted_at: "2026-07-23T10:00:00Z",
    updated_at: "2026-07-23T10:05:00Z",
    version: 4,
    scope: { tenant_id: "tenant-001" },
    masked_fields: [],
    original_url: "https://www.synthetic.example/detail-001.html",
    canonical_url: "https://www.synthetic.example/detail-001.html",
    policy_state: "APPROVED_RETRIEVAL",
    source_snapshot_id: "snapshot-001",
    parser_run_id: "parser-run-001",
    match_case_id: null,
    match_case_version: null,
    match_case: null,
    processing_history: [],
    fields: [],
    audit: [],
    evidence: {
      original_url: "https://www.synthetic.example/detail-001.html",
      canonical_url: "https://www.synthetic.example/detail-001.html",
      source_id: "SRC-SYNTHETIC",
      policy_state: "APPROVED_RETRIEVAL",
      policy_reason: "Source registry permits approved retrieval.",
      policy_version: "source-policy-v7",
      policy_evaluated_at: "2026-07-23T10:01:00Z",
      policy_expires_at: "2026-08-01T00:00:00Z",
      source_snapshot_id: "snapshot-001",
      captured_at: "2026-07-23T10:04:00Z",
      parser_run_id: "parser-run-001",
      parser_version: "parser-v1",
      correlation_id: "correlation-001",
      freshness_state: "CURRENT",
      resource_version: 4,
      etag: 'W/"4"',
    },
    lifecycle: {
      intake_id: "intake-001",
      version: 4,
      etag: 'W/"4"',
      actor_facts: {
        role_mode: "expansion-manager",
        allowed_actions: ["VIEW"],
        denied_action_reasons: {
          REQUEST_PROMOTION: "WORKFLOW_STATE_DENIED",
        },
        scope: {
          principal_tenant_id: "tenant-001",
          resource: { tenant_id: "tenant-001" },
          in_scope: true,
        },
        masking: {
          masked_fields: [],
          reason_codes: [],
          has_masked_fields: false,
          clearance: "INTERNAL",
        },
        purpose: {
          value: null,
          required: false,
          bound: false,
          reason_code: null,
        },
        second_actor: {
          required: false,
          pending_decision_ids: [],
          proposer_subject_ids: [],
          self_review_denied: false,
          reason_code: null,
        },
      },
      assignment: {
        assignment_id: "assignment-001",
        intake_id: "intake-001",
        status: "ASSIGNED",
        owner_subject_id: "manager-001",
        queue_id: "expansion",
        due_at: null,
        version: 2,
      },
      sla: null,
      decisions: [],
      promotion: null,
      job: null,
      assignment_history: [
        {
          receipt_id: null,
          category: "assignment",
          action: "ASSIGN",
          resource_id: "assignment-001",
          resource_version: 2,
          status: "ASSIGNED",
          actor: "manager-001",
          correlation_id: "correlation-001",
          occurred_at: "2026-07-23T10:03:00Z",
          receipt: {
            assignment_id: "assignment-001",
            state: "ASSIGNED",
            version: 2,
          },
        },
      ],
      sla_history: [],
      decision_history: [],
      promotion_history: [],
      job_history: [],
      mutation_receipts: [],
      latest_decision_receipt: {
        decision_id: "decision-001",
        receipt_id: "decision-receipt-001",
        status: "EXECUTED",
        action: "CREATE",
        version: 1,
        proposer: "manager-001",
        reviewer: null,
        graph_plan: null,
        correlation_id: "correlation-001",
        created_at: "2026-07-23T10:05:00Z",
        updated_at: "2026-07-23T10:05:00Z",
      },
      submission_receipt: null,
    },
  };
}

describe("canonical intake adapters", () => {
  it("hydrates a persisted runtime failure for durable error recovery", () => {
    const detail = runtimeDetail();
    detail.state = "FAILED";
    detail.issue = "ODP-INTAKE-RETRIEVAL-404";
    detail.next_action = "REPLAY_FROM_CHECKPOINT";
    detail.retryable = false;

    expect(canonicalDetailToRecord(detail).failure).toEqual({
      code: "ODP-INTAKE-RETRIEVAL-404",
      summary: "ODP-INTAKE-RETRIEVAL-404",
      nextAction: "REPLAY_FROM_CHECKPOINT",
      retryable: false,
    });
  });

  it("hydrates parser partial from the durable processing transition", () => {
    const detail = runtimeDetail();
    detail.state = "AWAITING_ASSISTED_ENTRY";
    detail.processing_history = [
      {
        transition_id: "transition-partial",
        from_state: "PARSING",
        to_state: "AWAITING_ASSISTED_ENTRY",
        occurred_at: "2026-07-23T10:04:00Z",
        actor: "worker",
        reason_code: "PARSER_PARTIAL",
        version_after: 3,
      },
    ];

    expect(canonicalDetailToRecord(detail).failure).toEqual({
      code: "PARSER_PARTIAL",
      summary: "PARSER_PARTIAL",
      nextAction: "ENTER_DATA",
      retryable: false,
    });
  });

  it("preserves the server-issued exact-source navigation receipt", () => {
    const detail = runtimeDetail();
    detail.lifecycle.submission_receipt = {
      receipt_id: "submission-receipt-001",
      receipt_type: "EXACT_SOURCE_IDENTITY",
      intake_id: detail.intake_id,
      state: "READY",
      existing_listing_id: "L-EXISTING",
      navigation_target: "/w/expansion/listings/L-EXISTING",
      correlation_id: "correlation-exact",
      issued_at: "2026-07-23T10:06:00Z",
    };

    expect(canonicalDetailToRecord(detail).submissionReceipt).toEqual({
      receiptId: "submission-receipt-001",
      receiptType: "EXACT_SOURCE_IDENTITY",
      existingListingId: "L-EXISTING",
      navigationTarget: "/w/expansion/listings/L-EXISTING",
      issuedAt: "2026-07-23T10:06:00Z",
    });
  });

  it("never manufactures transition or audit IDs when the server omitted them", () => {
    const lifecycle = canonicalDetailToLifecycle(runtimeDetail());

    expect(lifecycle.assignment_history).toEqual([]);
    expect(lifecycle.assignment?.assignment_id).toBe("assignment-001");
    expect(lifecycle.assignment?.audit_event_id).toBeNull();

    const receipt = canonicalIdentityReceipt(runtimeDetail());
    expect(receipt?.decisionId).toBe("decision-001");
    expect(receipt?.auditEventId).toBe("");
    expect(receipt?.redirectIds).toEqual([]);
  });

  it("rehydrates the latest pending identity decision from the authoritative lifecycle stream", () => {
    const detail = runtimeDetail();
    detail.lifecycle.latest_decision_receipt = null;
    detail.lifecycle.decisions = [
      {
        decision_id: "pending-decision-001",
        receipt_id: null,
        status: "PENDING_REVIEW",
        action: "MARK_DUPLICATE",
        version: 1,
        proposer: "manager-001",
        reviewer: null,
        graph_plan: null,
        correlation_id: "correlation-pending",
        created_at: "2026-07-23T10:06:00Z",
        updated_at: "2026-07-23T10:06:00Z",
      },
    ];
    detail.lifecycle.actor_facts.second_actor = {
      required: true,
      pending_decision_ids: ["pending-decision-001"],
      proposer_subject_ids: ["manager-001"],
      self_review_denied: true,
      reason_code: "SELF_REVIEW_DENIED",
    };

    const workflow = canonicalIdentityWorkflow(detail, {
      subjectId: "manager-001",
      displayName: "Manager",
      role: "expansion-manager",
    });
    const receipt = canonicalIdentityReceipt(detail);

    expect(workflow.status).toBe("PENDING_REVIEW");
    expect(workflow.decisionId).toBe("pending-decision-001");
    expect(workflow.denialReasonCode).toBe("SELF_REVIEW_DENIED");
    expect(receipt?.decisionId).toBe("pending-decision-001");
    expect(receipt?.status).toBe("PENDING_REVIEW");
    expect(receipt?.outcomeAction).toBe("MARK_DUPLICATE");
    expect(receipt?.correlationId).toBe("correlation-pending");
  });

  it("maps the complete authoritative detail summary and policy evidence facts", () => {
    const detail = runtimeDetail();
    detail.lifecycle.actor_facts.purpose = {
      value: "Expansion listing review",
      required: true,
      bound: true,
      reason_code: null,
    };
    detail.fields = [
      {
        field_path: "owner.contact",
        classification: "RESTRICTED",
        masked: true,
        parsed: null,
        normalized: null,
        corrected: null,
        effective: null,
        confidence: null,
        mask_reason_code: "FIELD_MASKED",
        correction_actor: null,
        correction_actor_role: null,
        correction_reason: null,
        corrected_at: null,
        source_snapshot_id: "snapshot-001",
        parser_run_id: "parser-run-001",
        parser_version: "parser-v1",
      },
    ];
    detail.lifecycle.actor_facts.masking = {
      masked_fields: ["owner.contact"],
      reason_codes: ["FIELD_MASKED"],
      has_masked_fields: true,
      clearance: "CONFIDENTIAL",
    };

    expect(canonicalDetailPresentationFacts(detail)).toMatchObject({
      sourceId: "SRC-SYNTHETIC",
      submitter: "staff-001",
      owner: "manager-001",
      submittedAt: "2026-07-23T10:00:00Z",
      scope: { tenant_id: "tenant-001" },
      policyReason: "Source registry permits approved retrieval.",
      policyVersion: "source-policy-v7",
      policyExpiresAt: "2026-08-01T00:00:00Z",
      etag: 'W/"4"',
      version: 4,
    });
    expect(canonicalSourceEvidence(detail)).toMatchObject({
      source_snapshot_id: "snapshot-001",
      parser_run_id: "parser-run-001",
      parser_version: "parser-v1",
      policy_version: "source-policy-v7",
      policy_expires_at: "2026-08-01T00:00:00Z",
    });
    expect(canonicalSensitiveEvidenceAccess(detail)).toMatchObject({
      purpose: "Expansion listing review",
      classification: "RESTRICTED",
      masked: true,
      mask_reason_code: "FIELD_MASKED",
      legal_hold_state: null,
    });
  });

  it("rehydrates authoritative correction lineage and assignment timestamps", () => {
    const detail = runtimeDetail();
    detail.audit = [
      {
        audit_event_id: "audit-correction-001",
        action: "intake.correct",
        occurred_at: "2026-07-23T10:04:00Z",
        result: "SUCCEEDED",
        reason_code: "Verified against landlord document",
        actor: "staff-001",
        actor_role: "expansion-staff",
        before: {
          fields: [
            {
              field: "location.address",
              before: "台北市松仁路 10 號",
              after: "台北市松仁路 100 號",
            },
          ],
        },
        after: {
          fields: [
            {
              field: "location.address",
              before: "台北市松仁路 10 號",
              after: "台北市松仁路 100 號",
            },
          ],
        },
        source_snapshot_id: "snapshot-001",
        parser_version: "parser-v1",
        related_ids: {
          correction_id: "correction-001",
          supersedes_correction_id: "correction-000",
        },
        correlation_id: "correlation-correction-001",
        resource_version: 4,
        evidence_state: "COMPLETE",
      },
      {
        audit_event_id: "audit-correction-002",
        action: "intake.correct",
        occurred_at: "2026-07-23T10:05:00Z",
        result: "SUCCEEDED",
        reason_code: "Reverted after source verification",
        actor: "manager-001",
        actor_role: "expansion-manager",
        before: {
          fields: [
            {
              field: "location.address",
              before: "台北市松仁路 100 號",
              after: "台北市松仁路 10 號",
            },
          ],
        },
        after: {
          fields: [
            {
              field: "location.address",
              before: "台北市松仁路 100 號",
              after: "台北市松仁路 10 號",
            },
          ],
        },
        source_snapshot_id: "snapshot-002",
        parser_version: "parser-v1",
        related_ids: {
          correction_id: "correction-002",
          reversal_of_correction_id: "correction-001",
        },
        correlation_id: "correlation-correction-002",
        resource_version: 5,
        evidence_state: "COMPLETE",
      },
    ];
    detail.lifecycle.assignment_history = [
      {
        ...detail.lifecycle.assignment_history[0]!,
        receipt_id: "assignment-history-001",
        occurred_at: "2026-07-23T10:03:00Z",
      },
      {
        ...detail.lifecycle.assignment_history[0]!,
        receipt_id: "assignment-history-002",
        status: "CLAIMED",
        occurred_at: "2026-07-23T10:04:00Z",
        resource_version: 3,
        receipt: {
          assignment_id: "assignment-001",
          state: "CLAIMED",
          version: 3,
        },
      },
    ];

    const corrections = canonicalCorrectionsByField(detail);
    const lifecycle = canonicalDetailToLifecycle(detail);

    expect(corrections["location.address"]).toEqual([
      expect.objectContaining({
        correctionId: "correction-001",
        status: "APPLIED",
        beforeEffectiveValue: "台北市松仁路 10 號",
        afterEffectiveValue: "台北市松仁路 100 號",
        supersedesCorrectionId: "correction-000",
      }),
      expect.objectContaining({
        correctionId: "correction-002",
        reversalOfCorrectionId: "correction-001",
        beforeEffectiveValue: "台北市松仁路 100 號",
        afterEffectiveValue: "台北市松仁路 10 號",
      }),
    ]);
    expect(lifecycle.assignment?.assigned_at).toBe("2026-07-23T10:03:00Z");
    expect(lifecycle.assignment?.claimed_at).toBe("2026-07-23T10:04:00Z");
    expect(lifecycle.assignment_history).toHaveLength(2);
  });

  it("does not invent a previous cursor and reports incomplete evidence honestly", () => {
    const page: IntakeInboxPage = {
      items: [
        {
          intake_id: "intake-001",
          state: "SUBMITTED",
          intake_method: "URL",
          source_id: "SRC-SYNTHETIC",
          match_outcome: null,
          submitted_by: "staff-001",
          assigned_to: null,
          assignment_id: null,
          assignment_status: null,
          assignment_version: null,
          owner_subject_id: null,
          queue_id: null,
          sla_instance_id: null,
          sla_state: null,
          sla_version: null,
          due_at: null,
          last_observed_at: null,
          submitted_at: "2026-07-23T10:00:00Z",
          updated_at: "2026-07-23T10:00:00Z",
          version: 1,
          original_url: "https://www.synthetic.example/detail-001.html",
          canonical_url: "https://www.synthetic.example/detail-001.html",
          policy_state: "APPROVED_RETRIEVAL",
          scope: {
            tenant_id: "tenant-001",
            brand_id: null,
            region_id: null,
            assigned_area_id: null,
            heat_zone_id: null,
          },
          issue: null,
          next_action: null,
          retryable: false,
          quarantined: false,
          failed: false,
          location: {
            address: null,
            district: null,
            assigned_area_id: null,
            heat_zone_id: null,
            latitude: 25.033,
            longitude: 121.5654,
            confidence: 0.94,
            source: "effective.latitude_longitude",
          },
          masking: {
            restricted_data: false,
            has_masked_fields: false,
            masked_fields: [],
            reason_codes: [],
          },
          masked_fields: [],
        },
      ],
      next_cursor: "server-next-cursor",
      page_size: 50,
      total_count: 1,
      total_count_accuracy: "EXACT",
      snapshot_time: "2026-07-23T10:00:00Z",
      query_fingerprint: "query-001",
    };

    const inbox = canonicalPageToInbox(page, 2);
    expect(inbox.previousCursor).toBeUndefined();
    expect(inbox.nextCursor).toBe("server-next-cursor");
    expect(inbox.evidenceState).toBe("partial");
    expect(inbox.items[0]?.location).toEqual({
      latitude: 25.033,
      longitude: 121.5654,
      confidence: 0.94,
      source: "effective.latitude_longitude",
    });

    const failed = canonicalPageToInbox(
      {
        ...page,
        items: [
          {
            ...page.items[0]!,
            state: "FAILED",
            issue: "MAX_RETRIES_EXHAUSTED",
            next_action: "REPLAY_FROM_CHECKPOINT",
            retryable: false,
            failed: true,
          },
        ],
      },
      1,
    );
    expect(failed.items[0]?.failure).toEqual({
      code: "MAX_RETRIES_EXHAUSTED",
      summary: "MAX_RETRIES_EXHAUSTED",
      nextAction: "REPLAY_FROM_CHECKPOINT",
      retryable: false,
    });
  });
});
