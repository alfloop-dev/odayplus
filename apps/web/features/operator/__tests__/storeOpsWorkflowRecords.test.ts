import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { confirmsWorkflowRecord, hasExplicitWorkflowOffset, latestWorkflowRecord, normalizeWorkflowSubmission, type WorkflowRecord } from "../storeOpsWorkflowRecords";
import { digestRecord, nativeWorkflowRecordCases, reviewIssue, workflowReceiptForRecord } from "./storeOpsPersistedReviews.fixtures";
describe("actual native seven-action manual record contract", () => {
  beforeEach(() => vi.stubGlobal("crypto", webcrypto)); afterEach(() => vi.unstubAllGlobals());
  it.each(nativeWorkflowRecordCases)("accepts actual native $action model record/hash and submitted values", async row => {
    const receipt = workflowReceiptForRecord(reviewIssue, row.record); receipt.auditEvent.metadata.reviewRecordHash = row.recordHash;
    expect(latestWorkflowRecord(receipt.issue, row.action).latest).toEqual(row.record);
    expect(await confirmsWorkflowRecord(receipt, row.action, { issueId: reviewIssue.id, storeId: reviewIssue.storeId, ...row.payload }, row.record.correlationId, "key-original")).toBe(true);
  });
  it.each(nativeWorkflowRecordCases)("preserves actual native nullable $action fields without invented defaults", async row => {
    const record = row.nullableRecord, receipt = workflowReceiptForRecord(reviewIssue, record); receipt.auditEvent.metadata.reviewRecordHash = row.nullableRecordHash;
    expect(latestWorkflowRecord(receipt.issue, row.action).latest?.submission).toEqual(record.submission);
    const payload = row.action === "camera-purpose" ? { purpose: "明確目的", privacyAcknowledged: true } : {};
    expect(await confirmsWorkflowRecord(receipt, row.action, { issueId: reviewIssue.id, storeId: reviewIssue.storeId, ...payload }, record.correlationId, "key-original")).toBe(true);
  });
  it("refuses every forged operational truth flag", () => {
    for (const row of nativeWorkflowRecordCases) {
      const record = row.record as WorkflowRecord & Record<string, unknown>;
      for (const key of Object.keys(record).filter(k => /Status$|clockAdvanced/.test(k))) {
        expect(latestWorkflowRecord({ ...reviewIssue, workflowRecords: [{ ...record, [key]: key === "clockAdvanced" ? true : "completed" }] }, row.action).status, row.action + key).toBe("unavailable");
      }
    }
  });
  it("validates whole history, rejects foreign owner/duplicate/missing nullable key", () => {
    const a = nativeWorkflowRecordCases[0];
    expect(latestWorkflowRecord({ ...reviewIssue, workflowRecords: [{ ...a.record, storeId: "FOREIGN" }, nativeWorkflowRecordCases[1].record] }, "assign").status).toBe("unavailable");
    expect(latestWorkflowRecord({ ...reviewIssue, workflowRecords: [a.record, a.record] }, a.action).status).toBe("unavailable");
    const submission = { ...a.record.submission } as Record<string, unknown>; delete submission.notes;
    expect(latestWorkflowRecord({ ...reviewIssue, workflowRecords: [{ ...a.record, submission }] }, a.action).status).toBe("unavailable");
    expect(latestWorkflowRecord(reviewIssue, "triage").status).toBe("absent");
  });
  it("keeps exact action-specific native append order and original replay operation", async () => {
    const row = nativeWorkflowRecordCases.find(r => r.action === "escalate")!;
    const first = row.record, later = { ...first, id: "AUD-LATER", workflowAuditId: "AUD-LATER", submission: { ...first.submission, reason: "later reason" } } as WorkflowRecord;
    const receipt = workflowReceiptForRecord(reviewIssue, first); receipt.issue.workflowRecords = [first, nativeWorkflowRecordCases[1].record, later]; receipt.idempotentReplay = true;
    expect(latestWorkflowRecord(receipt.issue, row.action).latest?.id).toBe("AUD-LATER");
    expect(await confirmsWorkflowRecord(receipt, row.action, { issueId: reviewIssue.id, storeId: reviewIssue.storeId, ...row.payload }, "new-attempt-CID", "key-original")).toBe(true);
    expect(await confirmsWorkflowRecord(receipt, row.action, { issueId: reviewIssue.id, storeId: reviewIssue.storeId, ...row.payload, reason: "later reason" }, "new-attempt-CID", "key-original")).toBe(false);
  });
  it("refuses bare HTTP issue, wrong action/audit/key/CID/digest and shared-audit alias", async () => {
    const row = nativeWorkflowRecordCases[0], p = { issueId: reviewIssue.id, storeId: reviewIssue.storeId, ...row.payload };
    expect(await confirmsWorkflowRecord({ issue: reviewIssue }, row.action, p, row.record.correlationId, "key-original")).toBe(false);
    const receipt = workflowReceiptForRecord(reviewIssue, row.record);
    expect(await confirmsWorkflowRecord(receipt, row.action, p, "wrong-CID", "key-original")).toBe(false);
    expect(await confirmsWorkflowRecord(receipt, row.action, p, row.record.correlationId, "wrong-key")).toBe(false);
    receipt.auditEvent.metadata.reviewRecordHash = "0".repeat(64);
    expect(await confirmsWorkflowRecord(receipt, row.action, p, row.record.correlationId, "key-original")).toBe(false);
    receipt.auditEvent.metadata.reviewRecordHash = digestRecord(row.record); receipt.auditEvent.id = row.record.auditEventId;
    expect(await confirmsWorkflowRecord(receipt, row.action, p, row.record.correlationId, "key-original")).toBe(false);
  });
  it("distinguishes false from null, validates integer retention, and never infers a timezone", () => {
    const a = nativeWorkflowRecordCases.find(r => r.action === "actions")!;
    expect(normalizeWorkflowSubmission(a.action, a.payload).needEvidence).toBe(false);
    expect(normalizeWorkflowSubmission(a.action, {}).needEvidence).toBeNull();
    const c = nativeWorkflowRecordCases.find(r => r.action === "camera-purpose")!;
    expect(latestWorkflowRecord({ ...reviewIssue, workflowRecords: [{ ...c.record, submission: { ...c.record.submission, retentionHours: 1.5 } }] }, c.action).status).toBe("unavailable");
    expect(hasExplicitWorkflowOffset("2026-10-20 09:00:00.000900+08:00")).toBe(true);
    expect(hasExplicitWorkflowOffset("2026-10-20 09:00")).toBe(false);
    expect(hasExplicitWorkflowOffset("2026-10-20")).toBe(false);
    expect(hasExplicitWorkflowOffset("2026-10-20T09:00:00+00:00.5")).toBe(true);
  });
});
