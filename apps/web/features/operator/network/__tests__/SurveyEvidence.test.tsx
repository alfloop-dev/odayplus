import "@testing-library/jest-dom/vitest";
import { webcrypto } from "node:crypto";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SurveyAssignmentDialog } from "../SurveyAssignmentDialog";
import { SurveyEvidenceWork } from "../SurveyEvidenceWork";
import type { SurveyAssignment, SurveyAssignmentApi, SurveyCreated, SurveyResult } from "../surveyAssignmentClient";
import { createSurveyEvidenceApi, type SurveyAttachment, type SurveyEvidenceApi, type SurveySubmission,
  type SurveySubmitted, type SurveyUpload, type SurveyWorkRead } from "../surveyEvidenceClient";

// Explicit HTTP, session, File and component doubles. No cloud IAM, real photo,
// actual field visit, human review or production media availability is asserted.
const target = { kind: "CANDIDATE_SITE" as const, id: "candidate-owned" };
const bytes = new Uint8Array([0xff, 0xd8, 0x01, 0x02, 0xff, 0xd9]);
const sha = "a".repeat(64);
const mediaId = "media-" + "b".repeat(64);
function assignment(id = "asgn-owned"): SurveyAssignment {
  return { assignment_id: id, tenant_id: "tenant-a", target_entity_kind: target.kind, target_entity_id: target.id,
    status: "ASSIGNED", revision: 1, created_by: "creator-a", created_at: "2026-10-16T08:00:00Z",
    assigned_to: "surveyor-a", expires_at: "2099-10-17T08:00:00Z", updated_at: null,
    mediaAvailability: { state: "configured", reasonCode: "SURVEY_CONTENT_READBACK_REQUIRED" } };
}
function attachment(): SurveyAttachment {
  return { mediaId, assignmentId: "asgn-owned", sha256: sha, sizeBytes: bytes.length, declaredContentType: "image/jpeg",
    mediaKind: "PHOTO", submittedCapturedAt: "2026-10-16T08:00:00Z", caption: null,
    uploadedBy: "surveyor-a", recordedAt: "2026-10-16T08:01:00Z" };
}
function read(row = assignment(), subject = "surveyor-a", items: SurveyAttachment[] = []): SurveyWorkRead {
  return { assignment: row, currentSubjectId: subject, canRequestUpdate: true, items };
}
function created(row = assignment()): SurveyCreated {
  return { assignment: row, auditEventIds: ["native-survey-audit"], receiptId: "canonical-receipt", replayed: false };
}
function submitted(): SurveySubmitted {
  return { ...created({ ...assignment(), revision: 2, status: "SUBMITTED", survey_id: "survey-owned" }),
    surveyId: "survey-owned", observationId: "observation-owned" };
}
function workApi(overrides: Partial<SurveyEvidenceApi> = {}): SurveyEvidenceApi {
  return { evidence: vi.fn(async () => ({ ok: false as const, message: "No review evidence supplied by this explicit test double" })),
    review: vi.fn(async () => ({ ok: false as const, message: "No reviewer supplied by this explicit test double" })),
    read: vi.fn<SurveyEvidenceApi["read"]>(async () => ({ ok: true, value: read() })),
    change: vi.fn<SurveyEvidenceApi["change"]>(async () => ({ ok: true, value: created() })),
    upload: vi.fn<SurveyEvidenceApi["upload"]>(async () => ({ ok: true, value: { attachment: attachment(), receiptId: "upload-receipt", replayed: false, auditEventIds: ["upload-audit"] } })),
    submit: vi.fn<SurveyEvidenceApi["submit"]>(async () => ({ ok: true, value: submitted() })),
    download: vi.fn<SurveyEvidenceApi["download"]>(async () => ({ ok: true, value: new Blob([bytes], { type: "application/octet-stream" }) })), ...overrides };
}
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
const upload: SurveyUpload = { expectedRevision: 1, dataBase64: "/9gBAv/Z", contentType: "image/jpeg", mediaKind: "PHOTO",
  capturedAt: "2026-10-16T08:00:00Z", caption: null, reason: "Actual selected assignment evidence" };
const submission: SurveySubmission = { expectedRevision: 1, location: { latitude: 25.03, longitude: 121.56, srid: 4326 },
  surveyedAt: "2026-10-16T08:00:00Z", attributes: { observation: "Actual entered field observation" },
  mediaIds: [], confidence: null, reason: "Actual selected assignment evidence" };
beforeEach(() => {
  let next = 0;
  vi.stubGlobal("crypto", { subtle: webcrypto.subtle, randomUUID: () => `owned-intent-${++next}` });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
async function ready() { await screen.findByText("已驗證登入帳號"); }
async function fillSubmission() {
  await ready();
  fireEvent.change(screen.getByLabelText("作業理由"), { target: { value: submission.reason } });
  fireEvent.change(screen.getByLabelText("緯度（回報值）"), { target: { value: "25.03" } });
  fireEvent.change(screen.getByLabelText("經度（回報值）"), { target: { value: "121.56" } });
  fireEvent.change(screen.getByLabelText("現勘時間（本機時間）"), { target: { value: "2026-10-16T08:00" } });
  fireEvent.change(screen.getByLabelText("現場觀察"), { target: { value: submission.attributes.observation } });
  fireEvent.click(screen.getByRole("button", { name: "預覽現勘回報" }));
  await screen.findByRole("region", { name: "現勘回報預覽" });
}

describe("Survey actual content adapter and current authenticated pin", () => {
  it("submits actual bytes and native field input without caller actor, tenant, URI or measured-confidence defaults", async () => {
    const fetcher = vi.fn((url: string, init?: RequestInit) => Promise.resolve(init?.method !== "POST"
      ? response({ assignmentId: "asgn-owned", ...read() })
      : url.endsWith("/submit") ? response(submitted()) : response({ attachment: attachment(), receiptId: "upload-receipt", auditEventIds: ["upload-audit"], replayed: false }, 201)));
    vi.stubGlobal("fetch", fetcher);
    const api = createSurveyEvidenceApi({ "X-Operator-Role": "expansion-staff" }, () => async () => true);
    expect((await api.read(assignment())).ok).toBe(true);
    expect((await api.upload(assignment(), upload, "upload-intent")).ok).toBe(true);
    expect((await api.submit(assignment(), submission, "submit-intent")).ok).toBe(true);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual(upload);
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual(submission);
    for (const call of fetcher.mock.calls.slice(1)) {
      const body = JSON.parse(String(call[1]?.body));
      for (const key of ["actor_id", "submittedBy", "tenant_id", "storage_uri", "sha256"]) expect(body).not.toHaveProperty(key);
      expect(call[1]).toMatchObject({ credentials: "include", cache: "no-store" });
    }
    expect((fetcher.mock.calls[1][1]?.headers as Record<string, string>)["Idempotency-Key"]).toBe("upload-intent");
    expect((fetcher.mock.calls[2][1]?.headers as Record<string, string>)["Idempotency-Key"]).toBe("submit-intent");
  });

  it.each(["seed", "wrong-parent", "duplicate-media", "string-size", "fake-kind"])("rejects %s content responses without fallback attachments", async invalid => {
    const item = attachment();
    const value = { assignmentId: "asgn-owned", ...read(), items: [invalid === "wrong-parent" ? { ...item, assignmentId: "other" }
      : invalid === "string-size" ? { ...item, sizeBytes: "6" } : invalid === "fake-kind" ? { ...item, mediaKind: ["PHOTO"] } : item],
      ...(invalid === "seed" ? { metadata: { source: "seed" } } : {}) };
    if (invalid === "duplicate-media") value.items.push(item);
    vi.stubGlobal("fetch", vi.fn(async () => response(value)));
    expect((await createSurveyEvidenceApi({}, () => async () => true).read(assignment())).ok).toBe(false);
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("verifies real downloaded bytes and the response digest against the authorized metadata", async () => {
    const realSha = Array.from(new Uint8Array(await webcrypto.subtle.digest("SHA-256", bytes))).map(v => v.toString(16).padStart(2, "0")).join("");
    const item = { ...attachment(), sha256: realSha };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(bytes, { headers: { "Content-Type": "application/octet-stream",
      "Content-Disposition": "attachment; filename=survey.bin", "X-Content-Type-Options": "nosniff", "X-Content-SHA256": realSha } })));
    const result = await createSurveyEvidenceApi({}, () => async () => true).download(assignment(), item);
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("Expected verified content");
    expect(new Uint8Array(await result.value.arrayBuffer())).toEqual(bytes);
  });

  it.each(["bytes", "digest-header", "nosniff", "disposition"])("refuses a %s-mismatched response instead of opening content", async invalid => {
    const realSha = Array.from(new Uint8Array(await webcrypto.subtle.digest("SHA-256", bytes))).map(v => v.toString(16).padStart(2, "0")).join("");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(invalid === "bytes" ? new Uint8Array([0]) : bytes, { headers: {
      "Content-Type": "application/octet-stream", "Content-Disposition": invalid === "disposition" ? "inline" : "attachment; filename=survey.bin",
      "X-Content-Type-Options": invalid === "nosniff" ? "" : "nosniff", "X-Content-SHA256": invalid === "digest-header" ? sha : realSha } })));
    expect((await createSurveyEvidenceApi({}, () => async () => true).download(assignment(), { ...attachment(), sha256: realSha })).ok).toBe(false);
  });

  it("rejects a native submitted response whose assignment survey ID does not join its returned evidence ID", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...submitted(), surveyId: "foreign-survey" })));
    const result = await createSurveyEvidenceApi({}, () => async () => true).submit(assignment(), submission, "same-intent");
    expect(result.ok).toBe(false);
  });

  it("does not adopt delayed native content after its authenticated context is revoked", async () => {
    const old = deferred<Response>(); let allowed = true;
    vi.stubGlobal("fetch", vi.fn(() => old.promise));
    const pending = createSurveyEvidenceApi({}, () => async () => allowed).read(assignment());
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    allowed = false; old.resolve(response({ assignmentId: "asgn-owned", ...read() }));
    expect(await pending).toEqual({ ok: false, superseded: true });
  });
});

describe("Survey actual native work in the existing assignment dialog", () => {
  it.each(["superseded", "denied", "unavailable"] as const)("withdraws cached work authority and preview after a fresh %s read", async kind => {
    const failed: SurveyResult<SurveyWorkRead> = kind === "superseded" ? { ok: false, superseded: true }
      : { ok: false, denied: kind === "denied", message: "fresh read unavailable" };
    const api = workApi({ read: vi.fn<SurveyEvidenceApi["read"]>()
      .mockResolvedValueOnce({ ok: true, value: read(assignment(), "surveyor-a", [attachment()]) }).mockResolvedValue(failed) });
    render(<SurveyEvidenceWork api={api} assignment={assignment()} onPendingChange={vi.fn()} />);
    await fillSubmission();
    expect(screen.getByRole("button", { name: `下載已核驗附件 ${mediaId}` })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "重讀回報狀態" }));
    await waitFor(() => expect(screen.queryByText("已驗證登入帳號")).not.toBeInTheDocument());
    expect(screen.queryByRole("region", { name: "現勘回報預覽" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: `下載已核驗附件 ${mediaId}` })).not.toBeInTheDocument();
    expect(api.submit).not.toHaveBeenCalled();
  });

  it.each(["claim", "start", "cancel"] as const)("withdraws work authority after a superseded %s command", async operation => {
    const api = workApi({ change: vi.fn<SurveyEvidenceApi["change"]>(async () => ({ ok: false, superseded: true })) });
    const notify = vi.fn();
    render(<SurveyEvidenceWork api={api} assignment={assignment()} onPendingChange={notify} />);
    await ready(); fireEvent.change(screen.getByLabelText("作業理由"), { target: { value: submission.reason } });
    fireEvent.click(screen.getByRole("button", { name: { claim: "領取任務", start: "開始現勘", cancel: "取消任務" }[operation] }));
    await waitFor(() => expect(api.change).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.queryByText("已驗證登入帳號")).not.toBeInTheDocument());
    expect(notify).toHaveBeenLastCalledWith(false);
    expect(api.read).toHaveBeenCalledOnce();
  });

  it("withdraws a superseded submission preview without showing a successful receipt", async () => {
    const api = workApi({ submit: vi.fn<SurveyEvidenceApi["submit"]>(async () => ({ ok: false, superseded: true })) });
    render(<SurveyEvidenceWork api={api} assignment={assignment()} onPendingChange={vi.fn()} />);
    await fillSubmission(); fireEvent.click(screen.getByRole("button", { name: "確認提交回報" }));
    await waitFor(() => expect(api.submit).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.queryByRole("region", { name: "現勘回報預覽" })).not.toBeInTheDocument());
    expect(screen.queryByText("已驗證登入帳號")).not.toBeInTheDocument();
    expect(screen.queryByText(/現勘回報已保存/)).not.toBeInTheDocument();
  });

  it("withdraws a superseded attachment upload preview without claiming bytes were saved", async () => {
    const api = workApi({ upload: vi.fn<SurveyEvidenceApi["upload"]>(async () => ({ ok: false, superseded: true })) });
    render(<SurveyEvidenceWork api={api} assignment={assignment()} onPendingChange={vi.fn()} />);
    await ready();
    const file = new File([bytes], "owned.jpg", { type: "image/jpeg" });
    Object.defineProperty(file, "arrayBuffer", { value: async () => bytes.slice().buffer });
    fireEvent.change(screen.getByLabelText("作業理由"), { target: { value: upload.reason } });
    fireEvent.change(screen.getByLabelText("附件檔案"), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("附件種類"), { target: { value: "PHOTO" } });
    fireEvent.change(screen.getByLabelText("回報拍攝時間（本機時間）"), { target: { value: "2026-10-16T08:00" } });
    fireEvent.click(screen.getByRole("button", { name: "預覽附件上傳" }));
    await screen.findByRole("region", { name: "附件上傳預覽" });
    fireEvent.click(screen.getByRole("button", { name: "確認上傳附件" }));
    await waitFor(() => expect(api.upload).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.queryByRole("region", { name: "附件上傳預覽" })).not.toBeInTheDocument());
    expect(screen.queryByText("已驗證登入帳號")).not.toBeInTheDocument();
    expect(screen.queryByText(/已核驗並保存附件/)).not.toBeInTheDocument();
  });

  it("withdraws revoked attachment metadata without opening a download for a superseded content read", async () => {
    const api = workApi({ read: vi.fn<SurveyEvidenceApi["read"]>(async () => ({ ok: true, value: read(assignment(), "surveyor-a", [attachment()]) })),
      download: vi.fn<SurveyEvidenceApi["download"]>(async () => ({ ok: false, superseded: true })) });
    const createObjectURL = vi.fn(); vi.stubGlobal("URL", class extends URL { static createObjectURL = createObjectURL; });
    render(<SurveyEvidenceWork api={api} assignment={assignment()} onPendingChange={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: `下載已核驗附件 ${mediaId}` }));
    await waitFor(() => expect(api.download).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.queryByText("已驗證登入帳號")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: `下載已核驗附件 ${mediaId}` })).not.toBeInTheDocument();
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("withdraws an acknowledged survey receipt when its following fresh read is superseded", async () => {
    const api = workApi({ read: vi.fn<SurveyEvidenceApi["read"]>()
      .mockResolvedValueOnce({ ok: true, value: read() }).mockResolvedValue({ ok: false, superseded: true }) });
    render(<SurveyEvidenceWork api={api} assignment={assignment()} onPendingChange={vi.fn()} />);
    await fillSubmission(); fireEvent.click(screen.getByRole("button", { name: "確認提交回報" }));
    await waitFor(() => expect(api.read).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText("已驗證登入帳號")).not.toBeInTheDocument());
    expect(screen.queryByText(/現勘回報已保存/)).not.toBeInTheDocument();
    expect(api.submit).toHaveBeenCalledOnce();
  });

  it("requires preview and final confirmation for actual File bytes and retains the saved attachment after history failure", async () => {
    let count = 0;
    const api = workApi({ read: vi.fn<SurveyEvidenceApi["read"]>(async () => ++count === 2 ? { ok: false, message: "history temporarily unavailable" } : { ok: true, value: read() }) });
    render(<SurveyEvidenceWork api={api} assignment={assignment()} onPendingChange={vi.fn()} />);
    await ready();
    const file = new File([bytes], "owned.jpg", { type: "image/jpeg" });
    Object.defineProperty(file, "arrayBuffer", { value: async () => bytes.slice().buffer });
    fireEvent.change(screen.getByLabelText("作業理由"), { target: { value: upload.reason } });
    fireEvent.change(screen.getByLabelText("附件檔案"), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("附件種類"), { target: { value: "PHOTO" } });
    fireEvent.change(screen.getByLabelText("回報拍攝時間（本機時間）"), { target: { value: "2026-10-16T08:00" } });
    fireEvent.click(screen.getByRole("button", { name: "預覽附件上傳" }));
    await screen.findByRole("region", { name: "附件上傳預覽" });
    expect(api.upload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "確認上傳附件" }));
    await screen.findByText(/已核驗並保存附件/);
    await screen.findByText(/狀態重讀失敗：history temporarily unavailable/);
    expect(api.upload).toHaveBeenCalledOnce();
    const [, input] = vi.mocked(api.upload).mock.calls[0];
    expect(input).toEqual({ ...upload, capturedAt: new Date("2026-10-16T08:00").toISOString() });
    expect(atob(input.dataBase64)).toBe(String.fromCharCode(...bytes));
    fireEvent.click(screen.getByRole("button", { name: "重讀回報狀態" }));
    await waitFor(() => expect(count).toBe(3));
    expect(api.upload).toHaveBeenCalledOnce();
    expect(screen.getByText(/已核驗並保存附件/)).toHaveTextContent(mediaId);
  });

  it("allows native zero-photo submission with missing confidence as null and never re-POSTs a successful ID on state refresh", async () => {
    let count = 0;
    const api = workApi({ read: vi.fn<SurveyEvidenceApi["read"]>(async () => ++count === 2 ? { ok: false, message: "history temporarily unavailable" }
      : { ok: true, value: read(count > 2 ? submitted().assignment : assignment()) }) });
    render(<SurveyEvidenceWork api={api} assignment={assignment()} onPendingChange={vi.fn()} />);
    await fillSubmission();
    expect(api.submit).not.toHaveBeenCalled();
    expect(screen.getByRole("region", { name: "現勘回報預覽" })).toHaveTextContent("未提供 · 0 件已保存附件");
    fireEvent.click(screen.getByRole("button", { name: "確認提交回報" }));
    const accepted = await screen.findByText(/現勘回報已保存/);
    expect(accepted).toHaveTextContent("survey-owned");
    expect(accepted).toHaveTextContent("審查結果以實際證據紀錄為準");
    await screen.findByText(/狀態重讀失敗：history temporarily unavailable/);
    expect(api.submit).toHaveBeenCalledWith(assignment(), { ...submission, surveyedAt: new Date("2026-10-16T08:00").toISOString() }, expect.any(String));
    fireEvent.click(screen.getByRole("button", { name: "重讀回報狀態" }));
    await waitFor(() => expect(count).toBe(3));
    expect(api.submit).toHaveBeenCalledOnce();
    expect(accepted).toHaveTextContent("survey-owned");
    expect(screen.getByRole("button", { name: "預覽現勘回報" })).toBeDisabled();
  });

  it("does not borrow another uploader's media for the actual current surveyor's native evidence", async () => {
    const own = attachment(), foreign = { ...attachment(), mediaId: "media-" + "c".repeat(64), uploadedBy: "former-surveyor" };
    const api = workApi({ read: vi.fn<SurveyEvidenceApi["read"]>(async () => ({ ok: true, value: read(assignment(), "surveyor-a", [own, foreign]) })) });
    render(<SurveyEvidenceWork api={api} assignment={assignment()} onPendingChange={vi.fn()} />);
    await fillSubmission(); fireEvent.click(screen.getByRole("button", { name: "確認提交回報" }));
    await screen.findByText(/現勘回報已保存/);
    expect(vi.mocked(api.submit).mock.calls[0][1].mediaIds).toEqual([own.mediaId]);
    expect(vi.mocked(api.submit).mock.calls[0][1].mediaIds).not.toContain(foreign.mediaId);
  });

  it("retries an unknown native submission with the same complete input and idempotency key", async () => {
    let attempts = 0;
    const api = workApi({ submit: vi.fn<SurveyEvidenceApi["submit"]>(async () => ++attempts === 1 ? { ok: false, message: "unknown reply" } : { ok: true, value: submitted() }) });
    render(<SurveyEvidenceWork api={api} assignment={assignment()} onPendingChange={vi.fn()} />);
    await fillSubmission(); fireEvent.click(screen.getByRole("button", { name: "確認提交回報" }));
    await screen.findByText("unknown reply");
    expect(screen.queryByText(/現勘回報已保存/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "確認提交回報" }));
    await screen.findByText(/現勘回報已保存/);
    const calls = vi.mocked(api.submit).mock.calls;
    expect(calls).toHaveLength(2); expect(calls[0]).toEqual(calls[1]);
  });

  it("discards a former task's late submission after the new task is already readable", async () => {
    const old = deferred<SurveyResult<SurveySubmitted>>();
    const first = workApi({ submit: vi.fn<SurveyEvidenceApi["submit"]>(() => old.promise) });
    const second = workApi({ read: vi.fn<SurveyEvidenceApi["read"]>(async () => ({ ok: true, value: read(assignment("asgn-B")) })) });
    const view = render(<SurveyEvidenceWork api={first} assignment={assignment()} onPendingChange={vi.fn()} />);
    await fillSubmission(); fireEvent.click(screen.getByRole("button", { name: "確認提交回報" }));
    await waitFor(() => expect(first.submit).toHaveBeenCalledOnce());
    view.rerender(<SurveyEvidenceWork api={second} assignment={assignment("asgn-B")} onPendingChange={vi.fn()} />);
    await waitFor(() => expect(second.read).toHaveBeenCalledOnce());
    await ready();
    await act(async () => old.resolve({ ok: true, value: submitted() }));
    expect(screen.queryByText(/現勘回報已保存/)).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "現勘回報與附件" })).toHaveTextContent("asgn-B");
    expect(second.submit).not.toHaveBeenCalled();
  });

  it("uses current server assignee and revision for claim/start, and does not offer a foreign surveyor's submit action", async () => {
    let row = assignment();
    const api = workApi({ read: vi.fn<SurveyEvidenceApi["read"]>(async () => ({ ok: true, value: read(row) })),
      change: vi.fn<SurveyEvidenceApi["change"]>(async (_selected, operation) => { row = { ...row, revision: row.revision + 1,
        status: operation === "claim" ? "CLAIMED" : "IN_PROGRESS" }; return { ok: true, value: created(row) }; }) });
    const view = render(<SurveyEvidenceWork api={api} assignment={assignment()} onPendingChange={vi.fn()} />);
    await ready(); fireEvent.change(screen.getByLabelText("作業理由"), { target: { value: submission.reason } });
    fireEvent.click(screen.getByRole("button", { name: "領取任務" }));
    await waitFor(() => expect(api.change).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "開始現勘" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "開始現勘" }));
    await waitFor(() => expect(api.change).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.change).mock.calls[1][0]).toMatchObject({ revision: 2, status: "CLAIMED" });
    const foreign = workApi({ read: vi.fn<SurveyEvidenceApi["read"]>(async () => ({ ok: true, value: read(assignment(), "other-legitimate-subject") })) });
    view.rerender(<SurveyEvidenceWork api={foreign} assignment={assignment()} onPendingChange={vi.fn()} />);
    await waitFor(() => expect(foreign.read).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByRole("button", { name: "領取任務" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "預覽現勘回報" })).toBeDisabled();
    expect(foreign.submit).not.toHaveBeenCalled();
  });

  it("opens work for an actual persisted row in the existing dialog and blocks another assignment while native work is pending", async () => {
    const old = deferred<SurveyResult<SurveySubmitted>>();
    const work = workApi({ submit: vi.fn<SurveyEvidenceApi["submit"]>(() => old.promise) });
    const api: SurveyAssignmentApi = { work, read: vi.fn<SurveyAssignmentApi["read"]>(async () => ({ ok: true, value: {
      items: [assignment()], assignees: [{ subjectId: "surveyor-a", displayName: "Actual surveyor" }], canAssign: true, assigneeError: null } })), create: vi.fn<SurveyAssignmentApi["create"]>() };
    render(<SurveyAssignmentDialog api={api} target={target} onClose={vi.fn()} />);
    expect(screen.queryByRole("region", { name: "現勘回報與附件" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "現勘回報／附件 asgn-owned" }));
    await fillSubmission(); fireEvent.click(screen.getByRole("button", { name: "確認提交回報" }));
    await waitFor(() => expect(work.submit).toHaveBeenCalledOnce());
    expect(screen.getByRole("button", { name: "重新讀取任務狀態" })).toBeDisabled();
    const preview = screen.getByRole("button", { name: "預覽指派" });
    expect(preview).toBeDisabled();
    fireEvent.submit(preview.closest("form")!);
    expect(screen.queryByRole("region", { name: "指派預覽" })).not.toBeInTheDocument();
    expect(api.create).not.toHaveBeenCalled();
    await act(async () => old.resolve({ ok: true, value: submitted() }));
    expect(within(screen.getByRole("region", { name: "現勘回報與附件" })).getByText(/現勘回報已保存/)).toHaveTextContent("survey-owned");
  });
});
