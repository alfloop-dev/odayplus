import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SurveyAssignmentDialog } from "../SurveyAssignmentDialog";
import { createSurveyAssignmentApi, type SurveyAssignmentApi, type SurveyCreated, type SurveyRead, type SurveyTarget } from "../surveyAssignmentClient";

// Explicit HTTP/session and native-response doubles. No real account, visit,
// upload, Task Center acceptance or external media availability is asserted.
const target: SurveyTarget = { kind: "CANDIDATE_SITE", id: "candidate-real-id" };
function assignment(id = "asgn-owned") {
  return { assignment_id: id, tenant_id: "tenant-a", target_entity_kind: target.kind, target_entity_id: target.id,
    status: "ASSIGNED", revision: 1, created_by: "trusted-server-subject", created_at: "2026-10-15T08:00:00Z",
    assigned_to: "field-a", expires_at: "2099-10-17T08:00:00Z", updated_at: null,
    mediaAvailability: { state: "unavailable" as const, reasonCode: "SURVEY_CONTENT_OWNER_UNAVAILABLE" as const } };
}
function created(): SurveyCreated { return { assignment: assignment(), auditEventIds: ["actual-native-audit-id"], receiptId: "actual-command-receipt", replayed: false }; }
function read(items: ReturnType<typeof assignment>[] = []): SurveyRead {
  return { items, assignees: [{ subjectId: "field-a", displayName: "Actual response account" }], canAssign: true, assigneeError: null };
}
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
async function fillAndPreview() {
  await screen.findByRole("option", { name: "Actual response account · field-a" });
  fireEvent.change(screen.getByLabelText("指派帳號"), { target: { value: "field-a" } });
  fireEvent.change(screen.getByLabelText("現勘期限（本機時間）"), { target: { value: "2099-10-17T08:00" } });
  fireEvent.change(screen.getByLabelText("指派理由"), { target: { value: "Inspect the actual selected parent" } });
  fireEvent.click(screen.getByRole("button", { name: "預覽指派" }));
  await screen.findByRole("region", { name: "指派預覽" });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Canonical Survey read and trusted assignment adapter", () => {
  it("reads only the exact canonical target and submits no caller actor, media or tenant authority", async () => {
    const fetcher = vi.fn((input: string, init?: RequestInit) => Promise.resolve(input.includes("assignees")
      ? response({ items: [{ subjectId: "field-a", displayName: "Actual response account" }] })
      : init?.method === "POST" ? response(created(), 201) : response({ targetKind: target.kind, targetId: target.id, items: [assignment()] })));
    vi.stubGlobal("fetch", fetcher);
    const api = createSurveyAssignmentApi({ "X-Operator-Role": "expansion-staff" }, () => async () => true);
    const loaded = await api.read(target);
    expect(loaded.ok && loaded.value.items[0].assignment_id).toBe("asgn-owned");
    expect(fetcher.mock.calls[0][0]).toContain("targetId=candidate-real-id");
    const result = await api.create(target, { assignedTo: "field-a", expiresAt: "2099-10-17T08:00:00Z", reason: "Actual reason" }, "one-intent-key");
    expect(result.ok).toBe(true);
    const [, init] = fetcher.mock.calls[2];
    expect(JSON.parse(String(init?.body))).toEqual({ targetKind: target.kind, targetId: target.id, assignedTo: "field-a", expiresAt: "2099-10-17T08:00:00Z", reason: "Actual reason" });
    expect((init?.headers as Record<string, string>)["Idempotency-Key"]).toBe("one-intent-key");
  });

  it.each(["source", "target", "shape"])("rejects %s-invalid response without borrowing prototype assignments", async (invalid) => {
    const value = { targetKind: target.kind, targetId: invalid === "target" ? "different" : target.id,
      items: invalid === "shape" ? [{ ...assignment(), revision: "1" }] : [assignment()], ...(invalid === "source" ? { source: "seed" } : {}) };
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(value))));
    const result = await createSurveyAssignmentApi({}, () => async () => true).read(target);
    expect(result.ok).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("keeps real assignment history readable when the assignee owner is unavailable, but cannot create", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string) => Promise.resolve(input.includes("assignees") ? response({}, 503)
      : response({ targetKind: target.kind, targetId: target.id, items: [assignment()] }))));
    const result = await createSurveyAssignmentApi({}, () => async () => true).read(target);
    expect(result.ok && result.value.items).toEqual([assignment()]);
    expect(result.ok && result.value.canAssign).toBe(false);
    expect(result.ok && result.value.assigneeError).toContain("無法取得");
  });

  it("never adopts a delayed HTTP response after the authenticated session pin changes", async () => {
    const delayed = deferred<Response>(); let allowed = true;
    vi.stubGlobal("fetch", vi.fn(() => delayed.promise));
    const pending = createSurveyAssignmentApi({}, () => async () => allowed).read(target);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    allowed = false; delayed.resolve(response({ targetKind: target.kind, targetId: target.id, items: [assignment()] }));
    expect(await pending).toEqual({ ok: false, superseded: true });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe("Original Survey assignment preview and final confirmation", () => {
  it("requires an explicit real assignee, deadline and reason, and creates only after final confirmation", async () => {
    const api: SurveyAssignmentApi = { read: vi.fn<SurveyAssignmentApi["read"]>(async () => ({ ok: true, value: read() })), create: vi.fn<SurveyAssignmentApi["create"]>(async () => ({ ok: true, value: created() })) };
    render(<SurveyAssignmentDialog api={api} target={target} onClose={vi.fn()} />);
    await fillAndPreview();
    expect(api.create).not.toHaveBeenCalled();
    expect(screen.getByRole("region", { name: "指派預覽" })).toHaveTextContent(target.id);
    fireEvent.click(screen.getByRole("button", { name: "確認指派" }));
    await screen.findByRole("status", { name: "已建立的現勘任務" });
    expect(api.create).toHaveBeenCalledOnce();
    expect(api.create).toHaveBeenCalledWith(target, { assignedTo: "field-a", expiresAt: new Date("2099-10-17T08:00").toISOString(), reason: "Inspect the actual selected parent" }, expect.any(String));
    expect(screen.getByText(/照片／附件內容尚未接通/)).toBeVisible();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("retains the accepted assignment ID after history failure and status retry does not create again", async () => {
    let reads = 0;
    const api: SurveyAssignmentApi = { read: vi.fn<SurveyAssignmentApi["read"]>(async () => ++reads === 2 ? { ok: false, message: "temporary read failure" } : { ok: true, value: read(reads > 2 ? [assignment()] : []) }),
      create: vi.fn<SurveyAssignmentApi["create"]>(async () => ({ ok: true, value: created() })) };
    render(<SurveyAssignmentDialog api={api} target={target} onClose={vi.fn()} />);
    await fillAndPreview(); fireEvent.click(screen.getByRole("button", { name: "確認指派" }));
    const accepted = await screen.findByRole("status", { name: "已建立的現勘任務" });
    expect(accepted).toHaveTextContent("asgn-owned");
    await screen.findByText(/狀態重讀失敗：temporary read failure/);
    fireEvent.click(screen.getByRole("button", { name: "重新讀取任務狀態" }));
    await waitFor(() => expect(reads).toBe(3));
    expect(api.create).toHaveBeenCalledOnce();
    expect(accepted).toHaveTextContent("asgn-owned");
  });

  it("retries an unconfirmed command with the same key and never marks media as uploaded", async () => {
    let attempts = 0;
    const api: SurveyAssignmentApi = { read: vi.fn<SurveyAssignmentApi["read"]>(async () => ({ ok: true, value: read() })),
      create: vi.fn<SurveyAssignmentApi["create"]>(async () => ++attempts === 1 ? { ok: false, message: "reply unknown" } : { ok: true, value: created() }) };
    render(<SurveyAssignmentDialog api={api} target={target} onClose={vi.fn()} />);
    await fillAndPreview(); fireEvent.click(screen.getByRole("button", { name: "確認指派" }));
    await screen.findByText("reply unknown");
    fireEvent.click(screen.getByRole("button", { name: "確認指派" }));
    await screen.findByRole("status", { name: "已建立的現勘任務" });
    const calls = vi.mocked(api.create).mock.calls;
    expect(calls).toHaveLength(2); expect(calls[0][2]).toBe(calls[1][2]);
    expect(screen.getByText(/不代表已上傳或可讀取影像/)).toBeVisible();
  });

  it("discards the former target's late command after target B is ready, including its ID and success state", async () => {
    const old = deferred<Awaited<ReturnType<SurveyAssignmentApi["create"]>>>();
    const first: SurveyAssignmentApi = { read: vi.fn<SurveyAssignmentApi["read"]>(async () => ({ ok: true, value: read() })), create: vi.fn<SurveyAssignmentApi["create"]>(() => old.promise) };
    const second: SurveyAssignmentApi = { read: vi.fn<SurveyAssignmentApi["read"]>(async () => ({ ok: true, value: read() })), create: vi.fn<SurveyAssignmentApi["create"]>() };
    const view = render(<SurveyAssignmentDialog api={first} target={target} onClose={vi.fn()} />);
    await fillAndPreview(); fireEvent.click(screen.getByRole("button", { name: "確認指派" }));
    await waitFor(() => expect(first.create).toHaveBeenCalledOnce());
    view.rerender(<SurveyAssignmentDialog api={second} target={{ kind: "HEAT_ZONE", id: "zone-B" }} onClose={vi.fn()} />);
    await screen.findByRole("dialog", { name: "指派熱區任務" });
    await act(async () => old.resolve({ ok: true, value: created() }));
    expect(screen.queryByRole("status", { name: "已建立的現勘任務" })).not.toBeInTheDocument();
    expect(screen.queryByText("asgn-owned")).not.toBeInTheDocument();
    expect(second.create).not.toHaveBeenCalled();
  });
});
