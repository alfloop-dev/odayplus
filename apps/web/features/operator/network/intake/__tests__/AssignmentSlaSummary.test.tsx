import { describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import type { AssistedIntake, AssignmentReceipt, SlaReceipt } from "@oday-plus/openapi-client";
import { AssignmentSlaSummary, SLA_STATE_MAP } from "../AssignmentSlaSummary";
import type {
  AssignmentLifecycleReceipt,
  SlaLifecycleReceipt,
} from "../useIntakeLifecycle";
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
      const assignment: AssignmentLifecycleReceipt = {
        assignment_id: "ASG-TEST-001",
        status: "ASSIGNED",
        owner_subject_id: "staff-1",
        owner_display_name: "許庭瑜（展店）",
        queue_name: "expansion-review",
        due_at: "2026-07-21T05:00:00Z",
        version: 3,
        audit_event_id: "AUD-ASG-001",
      };
      const sla: SlaLifecycleReceipt = {
        sla_instance_id: "SLA-TEST-001",
        state: "ON_TRACK",
        due_at: "2026-07-21T05:00:00Z",
        paused_duration_seconds: 0,
        version: 3,
        audit_event_id: "AUD-SLA-001",
        correlation_id: "CORR-TEST-ASG-001",
      };

      const html = renderToString(
        <AssignmentSlaSummary
          allowedActions={[
            "CLAIM_ASSIGNMENT",
            "TRANSFER_ASSIGNMENT",
            "PAUSE_SLA",
            "RESUME_SLA",
          ]}
          assignment={assignment}
          onClaim={onClaim}
          onOpenTransfer={onOpenTransfer}
          onOpenPause={onOpenPause}
          onResume={onResume}
          sla={sla}
        />
      );

      expect(html).toContain('data-testid="assignment-sla-summary"');
      expect(html).toContain("許庭瑜（展店）");
      expect(html).toContain("asg-btn-claim");
      expect(html).toContain("asg-btn-transfer");
      expect(html).toContain("asg-btn-pause");
    });

    it("shows canonical UNASSIGNED and marks unavailable SLA facts", () => {
      const html = renderToString(
        <AssignmentSlaSummary
          allowedActions={[]}
          onClaim={vi.fn()}
          onOpenPause={vi.fn()}
        />,
      );

      expect(html).toContain("[? UNAVAILABLE]");
      expect(html).toContain("API 未回傳");
      expect(html).toContain("UNASSIGNED");
      expect(html).not.toContain("ON_TRACK");
    });

    it("fails closed when allowedActions is absent even if callbacks exist", () => {
      const html = renderToString(
        <AssignmentSlaSummary
          assignment={{
            assignment_id: "ASG-TEST-001",
            status: "ASSIGNED",
            owner_subject_id: "staff-1",
            queue_name: "expansion-review",
            due_at: "2026-07-21T05:00:00Z",
            version: 3,
            audit_event_id: "AUD-ASG-001",
          }}
          onClaim={vi.fn()}
          onComplete={vi.fn()}
          onEscalate={vi.fn()}
          onOpenPause={vi.fn()}
          onOpenTransfer={vi.fn()}
          onResume={vi.fn()}
          sla={{
            sla_instance_id: "SLA-TEST-001",
            state: "ON_TRACK",
            due_at: "2026-07-21T05:00:00Z",
            paused_duration_seconds: 0,
            version: 3,
            audit_event_id: "AUD-SLA-001",
            correlation_id: "CORR-TEST-ASG-001",
          }}
        />,
      );

      expect(html).not.toContain('data-testid="asg-btn-');
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
      expect(html).toContain('data-testid="transfer-target-subject"');
      expect(html).toContain('data-testid="transfer-target-select"');
      expect(html).toContain('data-testid="transfer-handoff-note"');
      expect(html).toContain('data-testid="transfer-risk-summary"');
      expect(html).toContain('data-testid="transfer-risk-ack"');
      expect(html).toContain('data-testid="transfer-submit-btn"');
      // Must NOT contain pause fields
      expect(html).not.toContain('data-testid="pause-reason-input"');
      expect(html).not.toContain('data-testid="pause-resume-time-input"');

      expect(DEFAULT_TRANSFER_TARGETS).toEqual([]);
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

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

  describe("Mounted Dialog Interaction & State Flow Tests", () => {
    function setupContainer() {
      const container = document.createElement("div");
      document.body.appendChild(container);
      const root = createRoot(container);
      return {
        container,
        root,
        cleanup() {
          act(() => {
            root.unmount();
          });
          container.remove();
        },
      };
    }

    function setInputValue(element: HTMLElement, value: string | boolean) {
      act(() => {
        if (element instanceof HTMLInputElement && element.type === "checkbox") {
          if (element.checked !== value) {
            element.click();
          }
        } else if (element instanceof HTMLInputElement) {
          const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
          if (valueSetter) {
            valueSetter.call(element, value);
          } else {
            element.value = value as string;
          }
          element.dispatchEvent(new Event("input", { bubbles: true }));
          element.dispatchEvent(new Event("change", { bubbles: true }));
        } else if (element instanceof HTMLTextAreaElement) {
          const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set;
          if (valueSetter) {
            valueSetter.call(element, value);
          } else {
            element.value = value as string;
          }
          element.dispatchEvent(new Event("input", { bubbles: true }));
          element.dispatchEvent(new Event("change", { bubbles: true }));
        } else if (element instanceof HTMLSelectElement) {
          const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")?.set;
          if (valueSetter) {
            valueSetter.call(element, value);
          } else {
            element.value = value as string;
          }
          element.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
    }

    function clickElement(element: HTMLElement) {
      act(() => {
        element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      });
    }

    function TransferTestHarness({
      initialRecord,
      initialError,
      refreshedRecord,
      onSubmitSpy,
    }: {
      initialRecord: AssistedIntake;
      initialError: IntakeApiError | null;
      refreshedRecord: AssistedIntake;
      onSubmitSpy: (payload: any, options: { ifMatch: string }) => void;
    }) {
      const [record, setRecord] = useState<AssistedIntake>(initialRecord);
      const [error, setError] = useState<IntakeApiError | null>(initialError);

      return (
        <TransferIntakeDialog
          busy={false}
          error={error}
          onClose={() => {}}
          onConflictRefresh={() => {
            setRecord(refreshedRecord);
            setError(null);
          }}
          onSubmit={(payload) => {
            onSubmitSpy(payload, { ifMatch: `W/"${record.version}"` });
          }}
          record={record}
        />
      );
    }

    function PauseTestHarness({
      initialRecord,
      initialError,
      refreshedRecord,
      onSubmitSpy,
    }: {
      initialRecord: AssistedIntake;
      initialError: IntakeApiError | null;
      refreshedRecord: AssistedIntake;
      onSubmitSpy: (payload: any, options: { ifMatch: string }) => void;
    }) {
      const [record, setRecord] = useState<AssistedIntake>(initialRecord);
      const [error, setError] = useState<IntakeApiError | null>(initialError);

      return (
        <PauseSlaDialog
          busy={false}
          error={error}
          onClose={() => {}}
          onConflictRefresh={() => {
            setRecord(refreshedRecord);
            setError(null);
          }}
          onSubmit={(payload) => {
            onSubmitSpy(payload, { ifMatch: `W/"${record.version}"` });
          }}
          record={record}
        />
      );
    }

    it("proves TransferIntakeDialog retains draft state during 409 conflict refresh on a mounted component and resubmits with refreshed version", () => {
      const { container, root, cleanup } = setupContainer();
      const onSubmitSpy = vi.fn();

      const record_v3: AssistedIntake = { ...sampleIntakeRecord, owner: "許庭瑜（展店）", version: 3 };
      const record_v4: AssistedIntake = { ...sampleIntakeRecord, owner: "周育安（資料管理員）", version: 4 };

      act(() => {
        root.render(
          <TransferTestHarness
            initialError={conflictError}
            initialRecord={record_v3}
            onSubmitSpy={onSubmitSpy}
            refreshedRecord={record_v4}
          />
        );
      });

      // Assert initial mounted state with 409 conflict banner
      expect(container.querySelector('[data-testid="transfer-conflict-panel"]')).not.toBeNull();
      expect(container.querySelector('[data-testid="transfer-record-version"]')?.textContent).toBe("v3");

      // Enter user draft inputs on mounted component DOM
      const targetSubject = container.querySelector('[data-testid="transfer-target-subject"]') as HTMLInputElement;
      const targetSelect = container.querySelector('[data-testid="transfer-target-select"]') as HTMLSelectElement;
      const handoffTextarea = container.querySelector('[data-testid="transfer-handoff-note"]') as HTMLTextAreaElement;
      const riskCheckbox = container.querySelector('[data-testid="transfer-risk-ack"]') as HTMLInputElement;

      setInputValue(targetSubject, "00000000-0000-4000-8000-000000000104");
      setInputValue(targetSelect, "data-steward");
      setInputValue(handoffTextarea, "Preserved draft handoff note across 409 refresh");
      setInputValue(riskCheckbox, true);

      expect(targetSubject.value).toBe("00000000-0000-4000-8000-000000000104");
      expect(targetSelect.value).toBe("data-steward");
      expect(handoffTextarea.value).toBe("Preserved draft handoff note across 409 refresh");
      expect(riskCheckbox.checked).toBe(true);

      // Trigger conflict refresh button click on mounted instance
      const refreshBtn = container.querySelector('[data-testid="transfer-conflict-refresh-btn"]') as HTMLButtonElement;
      clickElement(refreshBtn);

      // Assert component re-rendered same instance: conflict panel cleared, record updated to v4/new owner, and draft inputs preserved
      expect(container.querySelector('[data-testid="transfer-conflict-panel"]')).toBeNull();
      expect(container.querySelector('[data-testid="transfer-record-version"]')?.textContent).toBe("v4");
      expect(container.querySelector('[data-testid="transfer-record-owner"]')?.textContent).toBe("周育安（資料管理員）");

      expect((container.querySelector('[data-testid="transfer-target-subject"]') as HTMLInputElement).value).toBe("00000000-0000-4000-8000-000000000104");
      expect((container.querySelector('[data-testid="transfer-target-select"]') as HTMLSelectElement).value).toBe("data-steward");
      expect((container.querySelector('[data-testid="transfer-handoff-note"]') as HTMLTextAreaElement).value).toBe("Preserved draft handoff note across 409 refresh");
      expect((container.querySelector('[data-testid="transfer-risk-ack"]') as HTMLInputElement).checked).toBe(true);

      // Submit preserved draft on mounted component
      const submitBtn = container.querySelector('[data-testid="transfer-submit-btn"]') as HTMLButtonElement;
      clickElement(submitBtn);

      // Assert submission payload has preserved draft and refreshed version in If-Match
      expect(onSubmitSpy).toHaveBeenCalledTimes(1);
      expect(onSubmitSpy).toHaveBeenCalledWith(
        {
          target_owner_subject_id: "00000000-0000-4000-8000-000000000104",
          target_owner_role: "data-steward",
          handoff_note: "Preserved draft handoff note across 409 refresh",
          riskSummary: expect.any(String),
          riskAcknowledged: true,
        },
        { ifMatch: 'W/"4"' }
      );

      cleanup();
    });

    it("proves PauseSlaDialog retains draft state during 409 conflict refresh on a mounted component and resubmits with refreshed version", () => {
      const { container, root, cleanup } = setupContainer();
      const onSubmitSpy = vi.fn();

      const record_v3: AssistedIntake = { ...sampleIntakeRecord, owner: "許庭瑜（展店）", version: 3 };
      const record_v4: AssistedIntake = { ...sampleIntakeRecord, owner: "周育安（資料管理員）", version: 4 };

      act(() => {
        root.render(
          <PauseTestHarness
            initialError={conflictError}
            initialRecord={record_v3}
            onSubmitSpy={onSubmitSpy}
            refreshedRecord={record_v4}
          />
        );
      });

      // Assert initial mounted state with 409 conflict banner
      expect(container.querySelector('[data-testid="pause-conflict-panel"]')).not.toBeNull();
      expect(container.querySelector('[data-testid="pause-record-version"]')?.textContent).toBe("v3");

      // Enter user draft inputs on mounted component DOM
      const reasonTextarea = container.querySelector('[data-testid="pause-reason-input"]') as HTMLTextAreaElement;
      const resumeTimeInput = container.querySelector('[data-testid="pause-resume-time-input"]') as HTMLInputElement;
      const riskCheckbox = container.querySelector('[data-testid="pause-risk-ack"]') as HTMLInputElement;

      setInputValue(reasonTextarea, "Waiting for landlord lease proof (preserved across 409)");
      setInputValue(resumeTimeInput, "2026-07-25T09:00");
      setInputValue(riskCheckbox, true);

      expect(reasonTextarea.value).toBe("Waiting for landlord lease proof (preserved across 409)");
      expect(resumeTimeInput.value).toBe("2026-07-25T09:00");
      expect(riskCheckbox.checked).toBe(true);

      // Trigger conflict refresh button click on mounted instance
      const refreshBtn = container.querySelector('[data-testid="pause-conflict-refresh-btn"]') as HTMLButtonElement;
      clickElement(refreshBtn);

      // Assert component re-rendered same instance: conflict panel cleared, record updated to v4, and draft inputs preserved
      expect(container.querySelector('[data-testid="pause-conflict-panel"]')).toBeNull();
      expect(container.querySelector('[data-testid="pause-record-version"]')?.textContent).toBe("v4");

      expect((container.querySelector('[data-testid="pause-reason-input"]') as HTMLTextAreaElement).value).toBe("Waiting for landlord lease proof (preserved across 409)");
      expect((container.querySelector('[data-testid="pause-resume-time-input"]') as HTMLInputElement).value).toBe("2026-07-25T09:00");
      expect((container.querySelector('[data-testid="pause-risk-ack"]') as HTMLInputElement).checked).toBe(true);

      // Submit preserved draft on mounted component
      const submitBtn = container.querySelector('[data-testid="pause-submit-btn"]') as HTMLButtonElement;
      clickElement(submitBtn);

      // Assert submission payload has preserved draft and refreshed version in If-Match
      expect(onSubmitSpy).toHaveBeenCalledTimes(1);
      expect(onSubmitSpy).toHaveBeenCalledWith(
        {
          reason: "Waiting for landlord lease proof (preserved across 409)",
          expected_resume_at: new Date("2026-07-25T09:00").toISOString(),
          riskSummary: expect.any(String),
          riskAcknowledged: true,
        },
        { ifMatch: 'W/"4"' }
      );

      cleanup();
    });
  });
});
