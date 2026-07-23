import { describe, expect, it } from "vitest";
import type {
  CanonicalIntakeRuntimeDetail,
  IntakeInboxPage,
} from "@oday-plus/openapi-client";
import {
  canonicalDetailToLifecycle,
  canonicalIdentityReceipt,
  canonicalPageToInbox,
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
      source_snapshot_id: "snapshot-001",
      captured_at: "2026-07-23T10:04:00Z",
      parser_run_id: "parser-run-001",
      parser_version: "parser-v1",
      correlation_id: "correlation-001",
      freshness_state: "CURRENT",
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
  });
});
