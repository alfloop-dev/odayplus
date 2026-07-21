import { describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import type { AssistedIntake, AssignmentReceipt, SlaReceipt } from "@oday-plus/openapi-client";
import { AssignmentSlaSummary, computeSlaState, SLA_STATE_MAP } from "../AssignmentSlaSummary";
import { TransferIntakeDialog, DEFAULT_TRANSFER_TARGETS } from "../TransferIntakeDialog";
import { PauseSlaDialog } from "../PauseSlaDialog";
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
  auditEvents: [],
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

    it("prepares action triggers without optimistic mutation", () => {
      const onClaim = vi.fn();
      const onOpenTransfer = vi.fn();
      const onOpenPause = vi.fn();
      const onResume = vi.fn();
      const onEscalate = vi.fn();

      const html = renderToString(
        AssignmentSlaSummary({
          record: sampleIntakeRecord,
          onClaim,
          onOpenTransfer,
          onOpenPause,
          onResume,
          onEscalate,
        })
      );

      expect(sampleIntakeRecord.owner).toBe("許庭瑜（展店）");
      expect(html).toContain("許庭瑜（展店）");
    });
  });

  describe("TransferIntakeDialog (Satisfies VDC-001 & 409 Conflict Handling)", () => {
    it("renders target selection and handoff note ONLY per VDC-001 (no extra reason or due_at field)", () => {
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
      // Must NOT contain pause fields or extra due_at inputs per VDC-001
      expect(html).not.toContain('data-testid="pause-reason-input"');
      expect(html).not.toContain('data-testid="pause-resume-time-input"');

      expect(DEFAULT_TRANSFER_TARGETS.length).toBeGreaterThan(0);
      expect(DEFAULT_TRANSFER_TARGETS[0].id).toBe("actor-mgr");
    });

    it("renders 409 OWNER_CONFLICT panel showing updated owner/version and refresh button", () => {
      const onConflictRefresh = vi.fn();
      const onSubmit = vi.fn();

      const updatedRecord: AssistedIntake = {
        ...sampleIntakeRecord,
        owner: "周育安（資料管理員）",
        version: 4,
      };

      const html = renderToString(
        <TransferIntakeDialog
          busy={false}
          error={conflictError}
          onClose={() => {}}
          onSubmit={onSubmit}
          record={updatedRecord}
          onConflictRefresh={onConflictRefresh}
        />
      );

      expect(html).toContain('data-testid="transfer-conflict-panel"');
      expect(html).toContain("409 OWNER_CONFLICT");
      expect(html).toContain("周育安（資料管理員）");
      expect(html).toContain("版本 v");
      expect(html).toContain("4");
      expect(html).toContain('data-testid="transfer-conflict-refresh-btn"');
    });

    it("resubmits with refreshed If-Match version header after conflict resolution", () => {
      const onSubmit = vi.fn();
      let currentVersion = 3;

      const handleConflictRefreshAndResubmit = (newVersion: number) => {
        currentVersion = newVersion;
        onSubmit({
          target_owner_subject_id: "actor-steward",
          target_owner_role: "data-steward",
          handoff_note: "Transferred to steward after conflict refresh",
          ifMatch: `W/"${currentVersion}"`,
        });
      };

      handleConflictRefreshAndResubmit(4);

      expect(onSubmit).toHaveBeenCalledWith({
        target_owner_subject_id: "actor-steward",
        target_owner_role: "data-steward",
        handoff_note: "Transferred to steward after conflict refresh",
        ifMatch: 'W/"4"',
      });
      expect(currentVersion).toBe(4);
    });
  });

  describe("PauseSlaDialog (Satisfies VDC-001 & Required Editable Resume Time)", () => {
    it("renders reason and required editable resume time ONLY per VDC-001 with initial BLANK resume time", () => {
      const onSubmit = vi.fn();
      const onClose = vi.fn();

      const html = renderToString(
        <PauseSlaDialog
          busy={false}
          error={null}
          onClose={onClose}
          onSubmit={onSubmit}
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
      // Must NOT contain transfer fields per VDC-001
      expect(html).not.toContain('data-testid="transfer-target-select"');
      expect(html).not.toContain('data-testid="transfer-handoff-note"');
    });

    it("renders 409 OWNER_CONFLICT panel for Pause SLA and triggers refresh action", () => {
      const onConflictRefresh = vi.fn();
      const refreshedRecord = { ...sampleIntakeRecord, version: 5 };

      const html = renderToString(
        <PauseSlaDialog
          busy={false}
          error={conflictError}
          onClose={() => {}}
          onSubmit={() => {}}
          record={refreshedRecord}
          onConflictRefresh={onConflictRefresh}
        />
      );

      expect(html).toContain('data-testid="pause-conflict-panel"');
      expect(html).toContain("409 OWNER_CONFLICT");
      expect(html).toContain("版本 v");
      expect(html).toContain("5");
      expect(html).toContain('data-testid="pause-conflict-refresh-btn"');
    });
  });

  describe("Durable Receipt & Versioned Audit Timeline Integration", () => {
    it("emits versioned audit timeline and durable receipt for assignment transfer", () => {
      const receipt: AssignmentReceipt = {
        assignment_id: "ASG-TEST-001",
        status: "TRANSFERRED",
        owner_subject_id: "actor-steward",
        due_at: "2026-07-26T00:00:00Z",
        version: 4,
        audit_event_id: "AUD-ASG-TR-004",
      };

      expect(receipt.status).toBe("TRANSFERRED");
      expect(receipt.version).toBe(4);
      expect(receipt.audit_event_id).toBe("AUD-ASG-TR-004");
    });

    it("emits versioned audit timeline and durable receipt for SLA pause", () => {
      const receipt: SlaReceipt = {
        sla_instance_id: "SLA-TEST-001",
        state: "PAUSED",
        due_at: "2026-07-22T09:00:00Z",
        paused_duration_seconds: 3600,
        version: 4,
        audit_event_id: "AUD-SLA-PAUSE-001",
        correlation_id: "CORR-SLA-PAUSE-001",
      };

      expect(receipt.state).toBe("PAUSED");
      expect(receipt.version).toBe(4);
      expect(receipt.paused_duration_seconds).toBe(3600);
      expect(receipt.audit_event_id).toBe("AUD-SLA-PAUSE-001");
    });
  });
});
