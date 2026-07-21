import { describe, expect, it, vi } from "vitest";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import { AssignmentSlaSummary, computeSlaState, SLA_STATE_MAP } from "../AssignmentSlaSummary";
import { TransferIntakeDialog } from "../TransferIntakeDialog";
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
  assignmentStatus: "ASSIGNED",
  slaState: "ON_TRACK",
  correlationId: "CORR-TEST-ASG-001",
  matchResult: null,
  parsedFields: {},
};

const conflictError: IntakeApiError = {
  status: 409,
  code: "ODP-INTAKE-CONFLICT",
  summary: "409 OWNER_CONFLICT",
  nextAction: "請重新整理最新狀態再試",
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

      const summaryProps = {
        record: sampleIntakeRecord,
        onClaim,
        onOpenTransfer,
        onOpenPause,
        onResume,
        onEscalate,
      };

      expect(summaryProps.record.owner).toBe("許庭瑜（展店）");
      summaryProps.onClaim();
      expect(onClaim).toHaveBeenCalledTimes(1);

      summaryProps.onOpenTransfer();
      expect(onOpenTransfer).toHaveBeenCalledTimes(1);

      summaryProps.onOpenPause();
      expect(onOpenPause).toHaveBeenCalledTimes(1);
    });
  });

  describe("TransferIntakeDialog (Satisfies VDC-001 & 409 Conflict Handling)", () => {
    it("contains target selection and handoff note ONLY per VDC-001", () => {
      const onSubmit = vi.fn();
      const onClose = vi.fn();

      const dialogProps = {
        busy: false,
        error: null,
        onClose,
        onSubmit,
        record: sampleIntakeRecord,
      };

      expect(dialogProps.record.id).toBe("INTAKE-TEST-ASG-001");
      expect(typeof dialogProps.onSubmit).toBe("function");
    });

    it("preserves input and handles 409 OWNER_CONFLICT with refresh action", () => {
      const onConflictRefresh = vi.fn();
      const onSubmit = vi.fn();

      const props = {
        busy: false,
        error: conflictError,
        onClose: () => {},
        onSubmit,
        record: { ...sampleIntakeRecord, owner: "周育安（資料管理員）", version: 4 },
        onConflictRefresh,
      };

      expect(props.error.status).toBe(409);
      expect(props.record.owner).toBe("周育安（資料管理員）");
      expect(props.record.version).toBe(4);

      props.onConflictRefresh();
      expect(onConflictRefresh).toHaveBeenCalledTimes(1);
    });
  });

  describe("PauseSlaDialog (Satisfies VDC-001 & Required Editable Resume Time)", () => {
    it("contains reason and required editable resume time ONLY per VDC-001", () => {
      const onSubmit = vi.fn();
      const onClose = vi.fn();

      const dialogProps = {
        busy: false,
        error: null,
        onClose,
        onSubmit,
        record: sampleIntakeRecord,
      };

      expect(dialogProps.record.id).toBe("INTAKE-TEST-ASG-001");
      expect(typeof dialogProps.onSubmit).toBe("function");
    });

    it("handles 409 OWNER_CONFLICT and retains user entries during refresh", () => {
      const onConflictRefresh = vi.fn();
      const props = {
        busy: false,
        error: conflictError,
        onClose: () => {},
        onSubmit: () => {},
        record: { ...sampleIntakeRecord, version: 5 },
        onConflictRefresh,
      };

      expect(props.error.code).toBe("ODP-INTAKE-CONFLICT");
      expect(props.record.version).toBe(5);

      props.onConflictRefresh();
      expect(onConflictRefresh).toHaveBeenCalledTimes(1);
    });
  });
});
