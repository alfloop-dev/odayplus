import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SurveyEvidenceReview } from "../SurveyEvidenceReview";
import { SurveyEvidenceWork } from "../SurveyEvidenceWork";
import type { SurveyAssignment, SurveyResult } from "../surveyAssignmentClient";
import { createSurveyEvidenceApi, type SurveyEvidence, type SurveyEvidenceApi, type SurveyReview,
  type SurveyReviewed } from "../surveyEvidenceClient";

// Explicit component/HTTP doubles; actual database controls are separate.
const assignment: SurveyAssignment = { assignment_id: "assignment-a", tenant_id: "tenant-a",
  target_entity_kind: "CANDIDATE_SITE", target_entity_id: "candidate-a", survey_id: "survey-a",
  status: "SUBMITTED", revision: 2, created_by: "creator", created_at: "2026-10-18T08:00:00Z",
  assigned_to: "surveyor", expires_at: "2099-01-01T00:00:00Z", updated_at: "2026-10-18T08:01:00Z",
  mediaAvailability: { state: "unavailable", reasonCode: "SURVEY_CONTENT_OWNER_UNAVAILABLE" } };
function evidence(): SurveyEvidence { return { survey_id: "survey-a", assignment_id: "assignment-a", tenant_id: "tenant-a",
  target_entity_kind: "CANDIDATE_SITE", target_entity_id: "candidate-a", submitter_id: "surveyor", revision: 1,
  currentSubjectId: "reviewer", canRequestReview: true, review_status: "PENDING_REVIEW", review_record: null,
  surveyed_at: "2026-10-18T08:00:00Z", submitted_at: "2026-10-18T08:01:00Z",
  attributes: { observation: "Declared synthetic field observation" }, location: { latitude: 25, longitude: 121 }, media_attachments: [] }; }
const input: SurveyReview = { surveyId: "survey-a", expectedRevision: 1, decision: "APPROVED", reason: "Reviewed the actual submitted observation",
  reviewChecklist: {}, conditions: ["Reinspect the actual entrance"] };
function reviewed(): SurveyReviewed { return { evidence: { ...evidence(), revision: 2, review_status: "APPROVED",
  review_record: { review_status: "APPROVED", reviewer_id: "reviewer", reviewed_at: "2026-10-18T09:00:00Z",
    review_comment: input.reason, review_checklist: {}, conditions: input.conditions } },
  receiptId: "review-receipt", replayed: false, auditEventIds: ["review-audit"] }; }
function api(overrides: Partial<SurveyEvidenceApi> = {}): SurveyEvidenceApi { return {
  ...createSurveyEvidenceApi({}, () => async () => true),
  evidence: vi.fn(async () => ({ ok: true as const, value: evidence() })),
  review: vi.fn(async () => ({ ok: true as const, value: reviewed() })), ...overrides }; }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
function response(value: unknown) { return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } }); }
beforeEach(() => { let next = 0; vi.stubGlobal("crypto", { randomUUID: () => `review-key-${++next}` }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
async function preview() {
  await screen.findByText("Declared synthetic field observation", { exact: false });
  fireEvent.change(screen.getByLabelText("審查結果"), { target: { value: input.decision } });
  fireEvent.change(screen.getByLabelText("審查理由"), { target: { value: input.reason } });
  fireEvent.change(screen.getByLabelText("審查條件（選填，每行一項）"), { target: { value: input.conditions.join("\n") } });
  fireEvent.click(screen.getByRole("button", { name: "預覽審查結果" }));
  await screen.findByRole("region", { name: "現勘審查預覽" });
}

describe("canonical survey independent review", () => {
  it("previews actual evidence and only reports an acknowledged saved review", async () => {
    const waiting = deferred<SurveyResult<SurveyReviewed>>();
    const work = api({ review: vi.fn(() => waiting.promise) }); const pending = vi.fn();
    render(<SurveyEvidenceReview api={work} assignment={assignment} onPendingChange={pending} />);
    await preview(); expect(work.review).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "確認保存審查" }));
    expect(work.review).toHaveBeenCalledWith({ assignment_id: assignment.assignment_id, tenant_id: assignment.tenant_id,
      target_entity_kind: assignment.target_entity_kind, target_entity_id: assignment.target_entity_id,
      survey_id: assignment.survey_id }, input, "review-key-1");
    expect(screen.queryByText(/審查已保存/)).not.toBeInTheDocument();
    expect(pending).toHaveBeenLastCalledWith(true);
    await act(async () => waiting.resolve({ ok: true, value: reviewed() }));
    await screen.findByText(/審查已保存：核准/); expect(pending).toHaveBeenLastCalledWith(false);
  });

  it("retains the exact command key after an unknown response", async () => {
    let attempts = 0;
    const work = api({ review: vi.fn(async () => ++attempts === 1
      ? { ok: false as const, message: "Unknown saved outcome" } : { ok: true as const, value: reviewed() }) });
    render(<SurveyEvidenceReview api={work} assignment={assignment} onPendingChange={() => {}} />);
    await preview(); fireEvent.click(screen.getByRole("button", { name: "確認保存審查" }));
    await screen.findByRole("alert"); expect(screen.queryByText(/審查已保存/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "確認保存審查" }));
    await screen.findByText(/審查已保存/);
    expect(vi.mocked(work.review).mock.calls[0]).toEqual(vi.mocked(work.review).mock.calls[1]);
  });

  it("keeps the confirmed receipt when the following fresh read fails", async () => {
    let reads = 0;
    const work = api({ evidence: vi.fn(async () => ++reads === 1 ? { ok: true as const, value: evidence() }
      : { ok: false as const, message: "Temporary read failure" }) });
    render(<SurveyEvidenceReview api={work} assignment={assignment} onPendingChange={() => {}} />);
    await preview(); fireEvent.click(screen.getByRole("button", { name: "確認保存審查" }));
    await screen.findByText(/審查已保存/); await screen.findByText("Temporary read failure");
    expect(screen.queryByRole("button", { name: "預覽審查結果" })).not.toBeInTheDocument();
  });

  it("does not offer review controls to the submitter or an unqualified principal", async () => {
    render(<SurveyEvidenceReview api={api({ evidence: vi.fn(async () => ({ ok: true as const,
      value: { ...evidence(), currentSubjectId: "surveyor", canRequestReview: false } })) })}
      assignment={assignment} onPendingChange={() => {}} />);
    await screen.findByText(/目前帳號無法審查/);
    expect(screen.queryByRole("button", { name: "預覽審查結果" })).not.toBeInTheDocument();
  });

  it("discards a previous task's late review reply", async () => {
    const waiting = deferred<SurveyResult<SurveyReviewed>>(); const old = api({ review: vi.fn(() => waiting.promise) });
    const next = api({ evidence: vi.fn(async () => ({ ok: true as const, value: { ...evidence(), survey_id: "survey-b", assignment_id: "assignment-b" } })) });
    const view = render(<SurveyEvidenceReview api={old} assignment={assignment} onPendingChange={() => {}} />);
    await preview(); fireEvent.click(screen.getByRole("button", { name: "確認保存審查" }));
    view.rerender(<SurveyEvidenceReview api={next} assignment={{ ...assignment, assignment_id: "assignment-b", survey_id: "survey-b" }} onPendingChange={() => {}} />);
    await screen.findByText(/survey-b/); await act(async () => waiting.resolve({ ok: true, value: reviewed() }));
    expect(screen.queryByText(/review-receipt/)).not.toBeInTheDocument(); expect(next.review).not.toHaveBeenCalled();
  });

  it.each(["foreign-parent", "foreign-tenant", "self-authority", "string-version", "missing-record", "seed"])("refuses %s evidence from HTTP", async invalid => {
    const data = invalid === "missing-record" ? reviewed().evidence : evidence();
    const wrong = { ...data, ...(invalid === "foreign-parent" ? { assignment_id: "other" }
      : invalid === "foreign-tenant" ? { tenant_id: "other" }
      : invalid === "self-authority" ? { currentSubjectId: "surveyor" }
      : invalid === "string-version" ? { revision: "1" }
      : invalid === "missing-record" ? { review_record: null } : { metadata: { source: "seed" } }) };
    vi.stubGlobal("fetch", vi.fn(async () => response(wrong)));
    expect((await createSurveyEvidenceApi({}, () => async () => true).evidence(assignment)).ok).toBe(false);
  });

  it("sends no caller reviewer or tenant authority and checks the saved outcome", async () => {
    const fetcher = vi.fn(async () => response(reviewed())); vi.stubGlobal("fetch", fetcher);
    const result = await createSurveyEvidenceApi({}, () => async () => true).review(assignment, input, "same-key");
    expect(result.ok).toBe(true);
    const [, init] = vi.mocked(fetcher).mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual(input);
    expect(init).toMatchObject({ method: "POST", credentials: "include", cache: "no-store" });
    expect(init.headers).toMatchObject({ "Idempotency-Key": "same-key" });
  });

  it("rejects an acknowledged receipt that names another reviewer", async () => {
    const wrong = reviewed(); wrong.evidence.review_record!.reviewer_id = "another-reviewer";
    vi.stubGlobal("fetch", vi.fn(async () => response(wrong)));
    expect((await createSurveyEvidenceApi({}, () => async () => true).review(assignment, input, "same-key")).ok).toBe(false);
  });

  it("discards a response after the captured session is revoked", async () => {
    const waiting = deferred<Response>(); let allowed = true;
    vi.stubGlobal("fetch", vi.fn(() => waiting.promise));
    const result = createSurveyEvidenceApi({}, () => async () => allowed).evidence(assignment);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce()); allowed = false; waiting.resolve(response(evidence()));
    expect(await result).toEqual({ ok: false, superseded: true });
  });

  it("withdraws stale review controls after a same-context read is superseded", async () => {
    let reads = 0;
    const work = api({ evidence: vi.fn(async () => ++reads === 1
      ? { ok: true as const, value: evidence() } : { ok: false as const, superseded: true as const }) });
    render(<SurveyEvidenceReview api={work} assignment={assignment} onPendingChange={() => {}} />);
    await preview(); fireEvent.click(screen.getByRole("button", { name: "重讀實際審查紀錄" }));
    await waitFor(() => expect(work.evidence).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText("讀取審查紀錄中…")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "確認保存審查" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "預覽審查結果" })).not.toBeInTheDocument();
  });

  it("keeps an in-flight review and releases the parent fence after the same task is reread", async () => {
    const waiting = deferred<SurveyResult<SurveyReviewed>>();
    const work = api({
      read: vi.fn(async () => ({ ok: true as const, value: { assignment: { ...assignment }, currentSubjectId: "reviewer", canRequestUpdate: false, items: [] } })),
      review: vi.fn(() => waiting.promise),
    });
    const pending = vi.fn();
    render(<SurveyEvidenceWork api={work} assignment={assignment} onPendingChange={pending} />);
    await preview(); fireEvent.click(screen.getByRole("button", { name: "確認保存審查" }));
    expect(pending).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "重讀回報狀態" }));
    await waitFor(() => expect(work.read).toHaveBeenCalledTimes(2));
    expect(work.evidence).toHaveBeenCalledTimes(1);
    expect(pending).toHaveBeenLastCalledWith(true);
    expect(screen.getByRole("button", { name: "確認保存審查" })).toBeDisabled();
    await act(async () => waiting.resolve({ ok: true, value: reviewed() }));
    await screen.findByText(/審查已保存/);
    expect(pending).toHaveBeenLastCalledWith(false);
    expect(work.review).toHaveBeenCalledTimes(1);
  });

  it("withdraws stale review controls when the command session is superseded", async () => {
    const work = api({ review: vi.fn(async () => ({ ok: false as const, superseded: true as const })) });
    render(<SurveyEvidenceReview api={work} assignment={assignment} onPendingChange={() => {}} />);
    await preview(); fireEvent.click(screen.getByRole("button", { name: "確認保存審查" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "確認保存審查" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "預覽審查結果" })).not.toBeInTheDocument();
  });

  it.each(["download", "review"])("keeps the parent fenced until both overlapping owners finish when %s finishes first", async first => {
    const waiting = deferred<SurveyResult<SurveyReviewed>>(), download = deferred<SurveyResult<Blob>>();
    const item = { mediaId: "media-" + "a".repeat(64), assignmentId: assignment.assignment_id,
      sha256: "b".repeat(64), sizeBytes: 6, declaredContentType: "image/jpeg", mediaKind: "PHOTO",
      submittedCapturedAt: "2026-10-18T08:00:00Z", caption: null, uploadedBy: "surveyor", recordedAt: "2026-10-18T08:01:00Z" };
    const work = api({ read: vi.fn(async () => ({ ok: true as const, value: {
      assignment, currentSubjectId: "reviewer", canRequestUpdate: false, items: [item] } })),
      review: vi.fn(() => waiting.promise), download: vi.fn(() => download.promise) });
    const pending = vi.fn();
    render(<SurveyEvidenceWork api={work} assignment={assignment} onPendingChange={pending} />);
    await preview(); fireEvent.click(screen.getByRole("button", { name: "確認保存審查" }));
    expect(pending).toHaveBeenLastCalledWith(true); const started = pending.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: `下載已核驗附件 ${item.mediaId}` }));
    expect(work.download).toHaveBeenCalledOnce();
    if (first === "download") {
      await act(async () => download.resolve({ ok: false, message: "Declared transient download failure" }));
      await screen.findByText("Declared transient download failure");
    } else {
      await act(async () => waiting.resolve({ ok: true, value: reviewed() }));
      await screen.findByText(/審查已保存/);
    }
    expect(pending).toHaveBeenLastCalledWith(true);
    expect(pending.mock.calls.slice(started)).not.toContainEqual([false]);
    if (first === "download") await act(async () => waiting.resolve({ ok: true, value: reviewed() }));
    else await act(async () => download.resolve({ ok: false, message: "Declared transient download failure" }));
    await waitFor(() => expect(pending).toHaveBeenLastCalledWith(false));
    expect(work.review).toHaveBeenCalledOnce();
  });

  it("releases an abandoned review owner when a denied parent read removes its evidence view", async () => {
    const waiting = deferred<SurveyResult<SurveyReviewed>>(); let reads = 0;
    const work = api({ read: vi.fn(async () => ++reads === 1 ? { ok: true as const, value: {
      assignment, currentSubjectId: "reviewer", canRequestUpdate: false, items: [] } }
      : { ok: false as const, denied: true, message: "Current parent admission revoked" }),
      review: vi.fn(() => waiting.promise) });
    const pending = vi.fn();
    render(<SurveyEvidenceWork api={work} assignment={assignment} onPendingChange={pending} />);
    await preview(); fireEvent.click(screen.getByRole("button", { name: "確認保存審查" }));
    expect(pending).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "重讀回報狀態" }));
    await screen.findByText(/Current parent admission revoked/);
    expect(screen.queryByRole("region", { name: "現勘獨立審查" })).not.toBeInTheDocument();
    expect(pending).toHaveBeenLastCalledWith(false);
    await act(async () => waiting.resolve({ ok: true, value: reviewed() }));
    expect(screen.queryByText(/審查已保存/)).not.toBeInTheDocument();
  });
});
