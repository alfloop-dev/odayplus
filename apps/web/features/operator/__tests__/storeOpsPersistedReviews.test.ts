import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { confirmsSavedReview, latestOutcomeReview, latestReplyReview } from "../storeOpsPersistedReviews";
import { digestRecord, nativePydanticNormalizationCases, outcomeRecord, receiptForRecord, replyRecord, reviewIssue } from "./storeOpsPersistedReviews.fixtures";
const payload = (record: ReturnType<typeof replyRecord> | ReturnType<typeof outcomeRecord>) => ({ issueId: reviewIssue.id, storeId: reviewIssue.storeId, ...record.submission });
describe("saved manual review source contract", () => {
  beforeEach(() => vi.stubGlobal("crypto", webcrypto));
  afterEach(() => vi.unstubAllGlobals());
  it("distinguishes absent history and nullable fields from malformed history", () => {
    expect(latestReplyReview(reviewIssue).status).toBe("absent");
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [] }).status).toBe("absent");
    expect(latestReplyReview({ ...reviewIssue, replyReviews: null }).status).toBe("unavailable");
    expect(latestOutcomeReview({ ...reviewIssue, outcomeReviews: [outcomeRecord()] }).latest?.submission.evidenceSummary).toBe("");
  });
  it("uses native append order without sorting microsecond timestamps", () => {
    const a = replyRecord(), b = { ...replyRecord(), id: "AUD-OPS-2", workflowAuditId: "AUD-OPS-2", recordedAt: "2026-10-01T01:05:00.000800+00:00" };
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [a, b] }).latest?.id).toBe(b.id);
  });
  it.each(["issueId", "storeId"] as const)("rejects foreign %s anywhere in the history", (key) => {
    const bad = { ...replyRecord(), [key]: "FOREIGN" };
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [bad, { ...replyRecord(), id: "AUD-OPS-2", workflowAuditId: "AUD-OPS-2" }] }).status).toBe("unavailable");
  });
  it("does not silently fall back when newest record has invalid delivery or missing nullable key", () => {
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [{ ...replyRecord(), deliveryStatus: "sent" }] }).status).toBe("unavailable");
    const r = replyRecord(); delete (r.submission as Partial<typeof r.submission>).reviewerNote;
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [r] }).status).toBe("unavailable");
  });
  it("rejects duplicate IDs and forged ordinary actor evidence", () => {
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [replyRecord(), replyRecord()] }).status).toBe("unavailable");
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [{ ...replyRecord(), actorSubjectId: "forged" }] }).status).toBe("unavailable");
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [{ ...replyRecord(), actorEvidence: "authenticated_principal", actorSubjectId: "verified-subject" }] }).status).toBe("available");
  });
  it("refuses array enum coercion from malformed JSON submissions", () => {
    const r = replyRecord();
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [{ ...r, submission: { ...r.submission, channel: ["google"] } }] }).status).toBe("unavailable");
    const o = outcomeRecord();
    expect(latestOutcomeReview({ ...reviewIssue, outcomeReviews: [{ ...o, submission: { ...o.submission, outcome: ["effective"], followUpTarget: "growth", followUpAction: "follow up" } }] }).status).toBe("unavailable");
  });
  it("rejects invalid native calendars without losing supported microseconds and offsets", () => {
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [{ ...replyRecord(), recordedAt: "2026-02-30T00:00:00+00:00" }] }).status).toBe("unavailable");
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [{ ...replyRecord(), recordedAt: "2026-10-01T09:05:00.000900+08:00" }] }).status).toBe("available");
  });
  it.each(["outcome", "replyReview"] as const)("confirms %s only from matching typed saved record and actual aggregate audit", async kind => {
    const record = kind === "outcome" ? outcomeRecord() : replyRecord(); const receipt = receiptForRecord(reviewIssue, kind, record);
    expect(await confirmsSavedReview(receipt, kind, payload(record), record.correlationId, "key-original")).toBe(true);
    expect(await confirmsSavedReview(receipt, kind, payload(record), "foreign-correlation", "key-original")).toBe(false);
  });
  it("selects original operation by aggregate audit after replay, even when it is no longer latest", async () => {
    const first = replyRecord(); const later = { ...replyRecord(), id: "AUD-OPS-2", workflowAuditId: "AUD-OPS-2", submission: { ...replyRecord().submission, draftReply: "later content" } };
    const receipt = receiptForRecord(reviewIssue, "replyReview", first); receipt.idempotentReplay = true; receipt.issue.replyReviews = [first, later];
    expect(await confirmsSavedReview(receipt, "replyReview", payload(first), "new-retry-CID", "key-original")).toBe(true);
    expect(await confirmsSavedReview(receipt, "replyReview", payload(later), "new-retry-CID", "key-original")).toBe(false);
  });
  it("does not confuse shared audit UUID with aggregate audit or accept changed digest/owner/key", async () => {
    const r = replyRecord(); const receipt = receiptForRecord(reviewIssue, "replyReview", r);
    receipt.auditEvent.id = r.auditEventId;
    expect(await confirmsSavedReview(receipt, "replyReview", payload(r), r.correlationId, "key-original")).toBe(false);
    receipt.auditEvent.id = r.id; receipt.auditEvent.metadata.reviewRecordHash = "0".repeat(64);
    expect(await confirmsSavedReview(receipt, "replyReview", payload(r), r.correlationId, "key-original")).toBe(false);
    receipt.auditEvent.metadata.reviewRecordHash = digestRecord(r);
    expect(await confirmsSavedReview(receipt, "replyReview", payload(r), r.correlationId, "foreign-key")).toBe(false);
    receipt.issue.storeId = "foreign";
    expect(await confirmsSavedReview(receipt, "replyReview", payload(r), r.correlationId, "key-original")).toBe(false);
  });
  it("confirms actual Pydantic C0/NEL/FEFF-normalized records and rejects the old mirrored specimen", async () => {
    for (const row of nativePydanticNormalizationCases) {
      const receipt = receiptForRecord(reviewIssue, "replyReview", row.record);
      receipt.auditEvent.metadata.reviewRecordHash = row.recordHash;
      expect(latestReplyReview(receipt.issue).status, row.codePoint).toBe("available");
      expect(await confirmsSavedReview(receipt, "replyReview", { ...payload(row.record), draftReply: row.input }, row.record.correlationId, "key-original"), row.codePoint).toBe(true);
    }
    // V1 wrongly assumed the trailing C0 character was removed. Keep that false
    // positive exposed: a matching-looking record that lost it is not this native submission.
    const r = replyRecord(), oldMirroredReceipt = receiptForRecord(reviewIssue, "replyReview", r);
    expect(await confirmsSavedReview(oldMirroredReceipt, "replyReview", { ...payload(r), draftReply: "\u0085" + r.submission.draftReply + "\u001f" }, r.correlationId, "key-original")).toBe(false);
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [{ ...r, submission: { ...r.submission, draftReply: "\u0085" } }] }).status).toBe("unavailable");
    // Native actor evidence uses its separate Python guard; C0-only subject is invalid.
    expect(latestReplyReview({ ...reviewIssue, replyReviews: [{ ...r, actorEvidence: "authenticated_principal", actorSubjectId: "\u001c" }] }).status).toBe("unavailable");
  });
  it("compares the native stripped strings and default nulls instead of raw optional field shape", async () => {
    const r = replyRecord(), receipt = receiptForRecord(reviewIssue, "replyReview", r);
    const p = { issueId: reviewIssue.id, storeId: reviewIssue.storeId, ...r.submission, draftReply: "  " + r.submission.draftReply + "  ", reviewerNote: undefined };
    expect(await confirmsSavedReview(receipt, "replyReview", p, r.correlationId, "key-original")).toBe(true);
  });
});
