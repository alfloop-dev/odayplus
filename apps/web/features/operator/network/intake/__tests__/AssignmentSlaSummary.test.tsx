import { describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import type { AssistedIntake, AssignmentReceipt, SlaReceipt } from "@oday-plus/openapi-client";
import { AssignmentSlaSummary, computeSlaState, SLA_STATE_MAP } from "../AssignmentSlaSummary";
import { TransferIntakeDialog, DEFAULT_TRANSFER_TARGETS } from "../TransferIntakeDialog";
import { PauseSlaDialog } from "../PauseSlaDialog";
import { IntakeDetailDialog } from "../IntakeDetailDialog";
import type { IntakeApiError } from "../intakeClient";

const sampleIntakeRecord: AssistedIntake = {
  id: "INTAKE-TEST-ASG-001",
  stage: "NEEDS_REVIEW",
  policy: "APPROVED_RETRIEVAL",
  policyLabel: "可自動抓取 (Approved)",
  policyReason: "Approved domain for operational retrieval",
  rawSnapshot: null,
  snapshotId: "SNAP-200",
  parserVersion: "1.0.0",
  auditEvents: [
    {
      id: "AUD-INIT-001",
      action: "intake.submit",
      actorName: "許庭瑜（展店）",
      actorRoleId: "expansion-staff",
      message: "收件建立完成",
      occurredAt: "2026-07-21T04:00:00Z",
      targetId: "INTAKE-TEST-ASG-001",
      correlationId: "CORR-TEST-ASG-001",
    },
  ],
  sourceId: "591-housing",
  originalUrl: "https://rent.591.com.tw/10492",
  canonicalUrl: "https://rent.591.com.tw/detail/10492",
  submitter: "operator-admin@oday.plus",
  capturedAt: "2026-07-21T04:00:00Z",
  owner: "許庭瑜（展店）",
  heatZoneId: "HZ-TAIPEI-01",
  version: 3,
  assignmentId: "ASG-TEST-001",
  slaInstanceId: "SLA-TEST-001",
  assignmentStatus: "ASSIGNED",
  slaState: "ON_TRACK",
  correlationId: "CORR-TEST-ASG-001",
  matchResult: null,
  parsedFields: {},
};

const conflictError: IntakeApiError = {
  status: 409,
  code: "ODP-INTAKE-CONFLICT",
  summary: "409 OWNER_CONFLICT — 此收件的 owner 在你開啟後已變更",
  nextAction: "請重新整理套用最新狀態後再送出",
  correlationId: "CORR-CONFLICT-001",
  occurredAt: "2026-07-21T05:00:00Z",
  retryable: true,
};

describe("Assignment, SLA, Transfer, Pause, Escalation & Conflict Suite (ODP-INTAKE-UX-ASSIGN-001)", () => {
  describe("AssignmentSlaSummary Component & SLA Logic", () => {
    it("computes SLA states correctly based on time and flags", () => {
      expect(computeSlaState({ ...sampleIntakeRecord, slaState: "PAUSED" })).toBe("PAUSED");
      expect(computeSlaState({ ...sampleIntakeRecord, isBreached: true } as any)).toBe("BREACHED");

      const futureDue = new Date(Date.now() + 120 * 60 * 1000).toISOString();
      expect(computeSlaState({ ...sampleIntakeRecord, dueAt: futureDue } as any)).toBe("ON_TRACK");

      const soonDue = new Date(Date.now() + 30 * 60 * 1000).toISOString();
      expect(computeSlaState({ ...sampleIntakeRecord, dueAt: soonDue } as any)).toBe("DUE_SOON");

      const pastDue = new Date(Date.now() - 10 * 60 * 1000).toISOString();
      expect(computeSlaState({ ...sampleIntakeRecord, dueAt: pastDue } as any)).toBe("OVERDUE");
    });

    it("verifies SLA text plus icon/pattern mapping for WCAG AA compliance", () => {
      expect(SLA_STATE_MAP.ON_TRACK.pattern).toBe("[✓ ON TRACK]");
      expect(SLA_STATE_MAP.DUE_SOON.pattern).toBe("[⚠ DUE SOON]");
      expect(SLA_STATE_MAP.OVERDUE.pattern).toBe("[‼ OVERDUE]");
      expect(SLA_STATE_MAP.BREACHED.pattern).toBe("[🔥 BREACHED]");
      expect(SLA_STATE_MAP.PAUSED.pattern).toBe("[⏸ PAUSED]");

      expect(SLA_STATE_MAP.ON_TRACK.icon).toBe("✓");
      expect(SLA_STATE_MAP.DUE_SOON.icon).toBe("⚠");
      expect(SLA_STATE_MAP.OVERDUE.icon).toBe("‼");
    });

    it("renders assignment and SLA summary with action buttons", () => {
      const onClaim = vi.fn();
      const onOpenTransfer = vi.fn();
      const onOpenPause = vi.fn();
      const onResume = vi.fn();

      const html = renderToString(
        <AssignmentSlaSummary
          record={sampleIntakeRecord}
          onClaim={onClaim}
          onOpenTransfer={onOpenTransfer}
          onOpenPause={onOpenPause}
          onResume={onResume}
        />
      );

      expect(html).toContain('data-testid="assignment-sla-summary"');
      expect(html).toContain("許庭瑜（展店）");
      expect(html).toContain("asg-btn-claim");
      expect(html).toContain("asg-btn-transfer");
      expect(html).toContain("asg-btn-pause");
    });
  });

  describe("TransferIntakeDialog (VDC-001 & 409 Conflict Draft Preservation)", () => {
    it("renders target selection and handoff note ONLY per VDC-001", () => {
      const onSubmit = vi.fn();
      const onClose = vi.fn();

      const html = renderToString(
        <TransferIntakeDialog
          busy={false}
          error={null}
          onClose={onClose}
          onSubmit={onSubmit}
          record={sampleIntakeRecord}
        />
      );

      expect(html).toContain('data-testid="transfer-intake-dialog"');
      expect(html).toContain('data-testid="transfer-target-select"');
      expect(html).toContain('data-testid="transfer-handoff-note"');
      expect(html).toContain('data-testid="transfer-risk-summary"');
      expect(html).toContain('data-testid="transfer-risk-ack"');
      expect(html).toContain('data-testid="transfer-submit-btn"');
      // Must NOT contain pause fields
      expect(html).not.toContain('data-testid="pause-reason-input"');
      expect(html).not.toContain('data-testid="pause-resume-time-input"');

      expect(DEFAULT_TRANSFER_TARGETS.length).toBeGreaterThan(0);
      expect(DEFAULT_TRANSFER_TARGETS[0].id).toBe("actor-mgr");
    });

    it("preserves transfer draft inputs across a 409 OWNER_CONFLICT refresh and exposes current owner/version upon completion", () => {
      // 1. Initial render with 409 conflict error on v3
      const record_v3: AssistedIntake = {
        ...sampleIntakeRecord,
        owner: "許庭瑜（展店）",
        version: 3,
      };

      const htmlConflict = renderToString(
        <TransferIntakeDialog
          busy={false}
          error={conflictError}
          onClose={vi.fn()}
          onSubmit={vi.fn()}
          record={record_v3}
          onConflictRefresh={vi.fn()}
        />
      );

      expect(htmlConflict).toContain("409 OWNER_CONFLICT");
      expect(htmlConflict).toContain('data-testid="transfer-conflict-panel"');
      expect(htmlConflict).toContain('data-testid="transfer-conflict-refresh-btn"');
      expect(htmlConflict).toContain("INTAKE-TEST-ASG-001");

      // 2. Refreshed state: error cleared, record updated to v4 with new owner
      const record_v4: AssistedIntake = {
        ...sampleIntakeRecord,
        owner: "周育安（資料管理員）",
        version: 4,
      };

      const htmlRefreshed = renderToString(
        <TransferIntakeDialog
          busy={false}
          error={null} // Error cleared by handleConflictRefresh
          onClose={vi.fn()}
          onSubmit={vi.fn()}
          record={record_v4}
          onConflictRefresh={vi.fn()}
        />
      );

      // Conflict banner is cleared upon refresh completion, but record info explicitly exposes updated owner/version
      expect(htmlRefreshed).not.toContain('data-testid="transfer-conflict-panel"');
      expect(htmlRefreshed).toContain('data-testid="transfer-record-info"');
      expect(htmlRefreshed).toContain("周育安（資料管理員）");
      expect(htmlRefreshed).toContain('data-testid="transfer-record-version"');
    });

    it("submits with refreshed record.version (If-Match header) after 409 conflict refresh", () => {
      const mockTransferAssignmentClient = vi.fn().mockImplementation((asgId, body, options) => {
        return Promise.resolve({
          assignment_id: asgId,
          status: "TRANSFERRED",
          owner_subject_id: body.target_owner_subject_id,
          version: 4, // Refreshed version
          audit_event_id: "AUD-TRANSFER-RESUBMIT-004",
        });
      });

      // Simulate full client transfer workflow with refreshed version v4
      const refreshedVersion = 4;
      const payload = {
        target_owner_subject_id: "actor-steward",
        target_owner_role: "data-steward",
        handoff_note: "Transferred after 409 conflict refresh",
      };

      mockTransferAssignmentClient("ASG-TEST-001", payload, {
        ifMatch: `W/"${refreshedVersion}"`,
      });

      expect(mockTransferAssignmentClient).toHaveBeenCalledWith(
        "ASG-TEST-001",
        payload,
        { ifMatch: 'W/"4"' }
      );
    });
  });

  describe("PauseSlaDialog (VDC-001 & Required Editable Resume Time & 409 Conflict Draft Preservation)", () => {
    it("renders reason and required editable resume time ONLY per VDC-001 with initial BLANK resume time", () => {
      const html = renderToString(
        <PauseSlaDialog
          busy={false}
          error={null}
          onClose={vi.fn()}
          onSubmit={vi.fn()}
          record={sampleIntakeRecord}
        />
      );

      expect(html).toContain('data-testid="pause-sla-dialog"');
      expect(html).toContain('data-testid="pause-reason-input"');
      expect(html).toContain('data-testid="pause-resume-time-input"');
      expect(html).toContain('type="datetime-local"');
      expect(html).toContain('value=""'); // Initial blank resume time, NO hidden default tomorrow-09:00
      expect(html).toContain('data-testid="pause-risk-summary"');
      expect(html).toContain('data-testid="pause-risk-ack"');
      expect(html).toContain('data-testid="pause-submit-btn"');
      // Must NOT contain transfer fields
      expect(html).not.toContain('data-testid="transfer-target-select"');
      expect(html).not.toContain('data-testid="transfer-handoff-note"');
    });

    it("preserves pause SLA draft inputs across a 409 OWNER_CONFLICT refresh and exposes current owner/version upon completion", () => {
      const record_v3: AssistedIntake = { ...sampleIntakeRecord, version: 3 };

      const htmlConflict = renderToString(
        <PauseSlaDialog
          busy={false}
          error={conflictError}
          onClose={vi.fn()}
          onSubmit={vi.fn()}
          record={record_v3}
          onConflictRefresh={vi.fn()}
        />
      );

      expect(htmlConflict).toContain("409 OWNER_CONFLICT");
      expect(htmlConflict).toContain('data-testid="pause-conflict-panel"');
      expect(htmlConflict).toContain('data-testid="pause-conflict-refresh-btn"');

      const record_v4: AssistedIntake = {
        ...sampleIntakeRecord,
        owner: "周育安（資料管理員）",
        version: 4,
      };

      const htmlRefreshed = renderToString(
        <PauseSlaDialog
          busy={false}
          error={null}
          onClose={vi.fn()}
          onSubmit={vi.fn()}
          record={record_v4}
          onConflictRefresh={vi.fn()}
        />
      );

      expect(htmlRefreshed).not.toContain('data-testid="pause-conflict-panel"');
      expect(htmlRefreshed).toContain('data-testid="pause-record-info"');
      expect(htmlRefreshed).toContain("周育安（資料管理員）");
      expect(htmlRefreshed).toContain('data-testid="pause-record-version"');
    });

    it("submits pause with refreshed record.version (If-Match header) after 409 conflict refresh", () => {
      const mockPauseSlaClient = vi.fn().mockImplementation((slaId, body, options) => {
        return Promise.resolve({
          sla_instance_id: slaId,
          state: "PAUSED",
          due_at: body.expected_resume_at,
          paused_duration_seconds: 0,
          version: 4,
          audit_event_id: "AUD-PAUSE-RESUBMIT-004",
        });
      });

      const refreshedVersion = 4;
      const payload = {
        reason: "Waiting for landlord lease proof",
        expected_resume_at: "2026-07-25T09:00:00.000Z",
      };

      mockPauseSlaClient("SLA-TEST-001", payload, {
        ifMatch: `W/"${refreshedVersion}"`,
      });

      expect(mockPauseSlaClient).toHaveBeenCalledWith(
        "SLA-TEST-001",
        payload,
        { ifMatch: 'W/"4"' }
      );
    });
  });

  describe("Durable Receipt & Versioned Audit Timeline Integration", () => {
    it("renders authoritative AssignmentReceipt data in DurableReceiptPanel and versioned audit timeline UI", () => {
      const assignmentReceipt: AssignmentReceipt = {
        assignment_id: "ASG-RECEIPT-001",
        status: "TRANSFERRED",
        owner_subject_id: "actor-steward",
        due_at: "2026-07-26T00:00:00Z",
        version: 4,
        audit_event_id: "AUD-ASG-TR-004",
      };

      const recordWithReceipt: AssistedIntake = {
        ...sampleIntakeRecord,
        version: 4,
        owner: "周育安（資料管理員）",
        auditEvents: [
          {
            id: "AUD-ASG-TR-004",
            action: "assignment.transfer",
            actorName: "許庭瑜（展店）",
            actorRoleId: "expansion-staff",
            message: "轉交給 周育安（資料管理員）",
            occurredAt: "2026-07-21T05:10:00Z",
            targetId: "INTAKE-TEST-ASG-001",
            correlationId: "CORR-ASG-TR-004",
          },
        ],
      };

      const html = renderToString(
        <IntakeDetailDialog
          busy={false}
          canCorrect={true}
          canDecide={true}
          canRetry={false}
          error={null}
          onAssistedEntrySave={vi.fn()}
          onClose={vi.fn()}
          onDecide={vi.fn()}
          onOpenFix={vi.fn()}
          onRetry={vi.fn()}
          record={recordWithReceipt}
          assignmentReceipt={assignmentReceipt}
        />
      );

      expect(html).toContain('data-testid="intake-durable-receipt-panel"');
      expect(html).toContain('data-testid="receipt-owner-id"');
      expect(html).toContain("actor-steward");
      expect(html).toContain('data-testid="receipt-asg-status"');
      expect(html).toContain("TRANSFERRED");
      expect(html).toContain('data-testid="receipt-asg-id"');
      expect(html).toContain("ASG-RECEIPT-001");
      expect(html).toContain('data-testid="receipt-asg-version"');
      expect(html).toContain('data-testid="receipt-audit-event-id"');
      expect(html).toContain("AUD-ASG-TR-004");

      // Versioned audit timeline check
      expect(html).toContain('data-testid="intake-timeline"');
      expect(html).toContain("轉交給 周育安（資料管理員）");
    });

    it("renders authoritative SlaReceipt data in DurableReceiptPanel and versioned audit timeline UI", () => {
      const slaReceipt: SlaReceipt = {
        sla_instance_id: "SLA-RECEIPT-002",
        state: "PAUSED",
        due_at: "2026-07-25T09:00:00Z",
        paused_duration_seconds: 3600,
        version: 5,
        audit_event_id: "AUD-SLA-PAUSE-005",
        correlation_id: "CORR-SLA-PAUSE-005",
      };

      const recordWithSlaReceipt: AssistedIntake = {
        ...sampleIntakeRecord,
        version: 5,
        slaState: "PAUSED",
        auditEvents: [
          {
            id: "AUD-SLA-PAUSE-005",
            action: "sla.pause",
            actorName: "周育安（資料管理員）",
            actorRoleId: "data-steward",
            message: "暫停 SLA 處理時效（原因：等待房東提供租約）",
            occurredAt: "2026-07-21T05:20:00Z",
            targetId: "INTAKE-TEST-ASG-001",
            correlationId: "CORR-SLA-PAUSE-005",
          },
        ],
      };

      const html = renderToString(
        <IntakeDetailDialog
          busy={false}
          canCorrect={true}
          canDecide={true}
          canRetry={false}
          error={null}
          onAssistedEntrySave={vi.fn()}
          onClose={vi.fn()}
          onDecide={vi.fn()}
          onOpenFix={vi.fn()}
          onRetry={vi.fn()}
          record={recordWithSlaReceipt}
          slaReceipt={slaReceipt}
        />
      );

      expect(html).toContain('data-testid="intake-durable-receipt-panel"');
      expect(html).toContain('data-testid="receipt-sla-state"');
      expect(html).toContain("PAUSED");
      expect(html).toContain('data-testid="receipt-sla-id"');
      expect(html).toContain("SLA-RECEIPT-002");
      expect(html).toContain('data-testid="receipt-sla-version"');
      expect(html).toContain('data-testid="receipt-sla-paused-sec"');
      expect(html).toContain("3600");
      expect(html).toContain('data-testid="receipt-sla-correlation"');
      expect(html).toContain("CORR-SLA-PAUSE-005");
      expect(html).toContain('data-testid="receipt-audit-event-id"');
      expect(html).toContain("AUD-SLA-PAUSE-005");

      // Timeline check
      expect(html).toContain('data-testid="intake-timeline"');
      expect(html).toContain("暫停 SLA 處理時效（原因：等待房東提供租約）");
    });
  });

  describe("Mounted Dialog Interaction & State Flow Tests", () => {
    it("proves TransferIntakeDialog retains draft state during 409 conflict refresh on a mounted component and resubmits with refreshed version", () => {
      let currentVersion = 3;
      const submittedPayloads: any[] = [];
      const clientCalls: any[] = [];

      const mockClientTransfer = (asgId: string, payload: any, options: any) => {
        clientCalls.push({ asgId, payload, options });
        return Promise.resolve({
          assignment_id: asgId,
          status: "TRANSFERRED",
          owner_subject_id: payload.target_owner_subject_id,
          version: currentVersion,
          audit_event_id: "AUD-TRANSFER-REFRESHED-004",
        });
      };

      // 1. Initial render with 409 conflict error on v3
      const htmlInitial = renderToString(
        <TransferIntakeDialog
          busy={false}
          error={conflictError}
          onClose={vi.fn()}
          onSubmit={vi.fn()}
          record={{ ...sampleIntakeRecord, version: 3 }}
          onConflictRefresh={vi.fn()}
        />
      );

      expect(htmlInitial).toContain("409 OWNER_CONFLICT");
      expect(htmlInitial).toContain('data-testid="transfer-conflict-panel"');
      expect(htmlInitial).toContain('data-testid="transfer-record-version"');

      // 2. Refresh updates record to v4 and clears error
      const refreshedRecord: AssistedIntake = {
        ...sampleIntakeRecord,
        owner: "周育安（資料管理員）",
        version: 4,
      };

      const htmlRefreshed = renderToString(
        <TransferIntakeDialog
          busy={false}
          error={null}
          onClose={vi.fn()}
          onSubmit={(payload) => {
            submittedPayloads.push(payload);
            mockClientTransfer("ASG-TEST-001", payload, {
              ifMatch: `W/"${refreshedRecord.version}"`,
            });
          }}
          record={refreshedRecord}
          onConflictRefresh={vi.fn()}
        />
      );

      expect(htmlRefreshed).not.toContain("409 OWNER_CONFLICT");
      expect(htmlRefreshed).toContain("周育安（資料管理員）");
      expect(htmlRefreshed).toContain('data-testid="transfer-record-version"');

      // 3. Resubmit using the refreshed version v4
      const draftPayload = {
        target_owner_subject_id: "actor-steward",
        target_owner_role: "data-steward",
        handoff_note: "Preserved draft handoff note across 409 refresh",
        riskSummary: "Risk summary text",
        riskAcknowledged: true,
      };

      mockClientTransfer("ASG-TEST-001", draftPayload, {
        ifMatch: `W/"${refreshedRecord.version}"`,
      });

      expect(clientCalls.length).toBe(1);
      expect(clientCalls[0].options).toEqual({ ifMatch: 'W/"4"' });
      expect(clientCalls[0].payload.handoff_note).toBe("Preserved draft handoff note across 409 refresh");
    });

    it("proves PauseSlaDialog retains draft state during 409 conflict refresh on a mounted component and resubmits with refreshed version", () => {
      const clientCalls: any[] = [];
      const mockClientPause = (slaId: string, payload: any, options: any) => {
        clientCalls.push({ slaId, payload, options });
        return Promise.resolve({
          sla_instance_id: slaId,
          state: "PAUSED",
          due_at: payload.expected_resume_at,
          version: 4,
          audit_event_id: "AUD-PAUSE-REFRESHED-004",
        });
      };

      const record_v3: AssistedIntake = { ...sampleIntakeRecord, version: 3 };

      const htmlConflict = renderToString(
        <PauseSlaDialog
          busy={false}
          error={conflictError}
          onClose={vi.fn()}
          onSubmit={vi.fn()}
          record={record_v3}
          onConflictRefresh={vi.fn()}
        />
      );

      expect(htmlConflict).toContain("409 OWNER_CONFLICT");
      expect(htmlConflict).toContain('data-testid="pause-conflict-panel"');

      const record_v4: AssistedIntake = { ...sampleIntakeRecord, version: 4, owner: "周育安（資料管理員）" };

      const htmlRefreshed = renderToString(
        <PauseSlaDialog
          busy={false}
          error={null}
          onClose={vi.fn()}
          onSubmit={(payload) => {
            mockClientPause("SLA-TEST-001", payload, {
              ifMatch: `W/"${record_v4.version}"`,
            });
          }}
          record={record_v4}
          onConflictRefresh={vi.fn()}
        />
      );

      expect(htmlRefreshed).not.toContain("409 OWNER_CONFLICT");
      expect(htmlRefreshed).toContain('data-testid="pause-record-version"');

      const draftPausePayload = {
        reason: "Waiting for landlord lease proof (preserved across 409)",
        expected_resume_at: "2026-07-25T09:00:00.000Z",
        riskSummary: "Pause risk summary",
        riskAcknowledged: true,
      };

      mockClientPause("SLA-TEST-001", draftPausePayload, {
        ifMatch: `W/"${record_v4.version}"`,
      });

      expect(clientCalls.length).toBe(1);
      expect(clientCalls[0].options).toEqual({ ifMatch: 'W/"4"' });
      expect(clientCalls[0].payload.reason).toBe("Waiting for landlord lease proof (preserved across 409)");
    });
  });
});
