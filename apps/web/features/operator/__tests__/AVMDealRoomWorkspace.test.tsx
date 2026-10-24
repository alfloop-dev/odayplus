import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AVMDealRoomWorkspace } from "../avm/AVMDealRoomWorkspace";
import { createAVMDealRoomClient, SupersededAVMRead, type Detail } from "../avm/avmDealRoomClient";

const context = { subjectId: "actual-finance", tenantId: "actual-tenant", contextKey: "a".repeat(64), canCreate: true };
const report = { report_id: "actual-report", case_id: "actual-case", store_id: "actual-store", valuation_version: 3,
  fair_price: { p10: 200, p50: 250, p90: 300 }, reserve_price: 194, asking_price: 315, confidence: "low",
  model_version: "actual-model", feature_version: "actual-feature", depreciation_version: "actual-depreciation",
  prediction_origin_time: "2026-10-15T00:00:00Z", valued_at: "2026-10-15T01:00:00Z", finance_approval: null,
  lenses: ["income", "asset", "market", "blended"].map((lens) => ({ lens, p10: 200, p50: 250, p90: 300, method: "actual-recorded-method", evidence: { source_snapshot_ids: ["actual-source"] } })) };
function detail(): Detail { return { context, expectedVersion: "d".repeat(64), case: { case_id: "actual-case", store_id: "actual-store", status: "REVIEW_REQUIRED", created_by: "actual-creator", created_at: "2026-10-15T00:00:00Z", status_history: [] },
  report, reportHistory: [report], normalizedMargin: null, dataRoom: null, sourceSnapshotIds: ["actual-source"], sensitiveValuesVisible: true,
  creatorVerification: { verified: true }, financeVerification: { verified: false },
  exportCount: 0, reportAvailability: { state: "available", reasonCode: null }, actions: { normalize: false, value: false, approve: true, build: false, export: false }, exports: [], attachmentProvider: { available: false, reasonCode: "AVM_SOURCE_DOCUMENT_BYTES_NOT_PROVIDED" } }; }
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const prior = `${window.location.pathname}${window.location.search}`;
afterEach(() => { cleanup(); window.history.replaceState(null, "", prior); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function transport(initial = detail(), postStatus = 403) {
  const fetcher = vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
    const url = String(path);
    if (url === "/auth/session") return Promise.resolve(response({ subject: context.subjectId }));
    if (url.endsWith("/context")) return Promise.resolve(response(context));
    if (init?.method === "POST") return Promise.resolve(response({ detail: initial }, postStatus));
    if (url.endsWith("/cases")) return Promise.resolve(response({ context, items: [initial], count: 1 }));
    if (url.endsWith("/cases/actual-case")) return Promise.resolve(response(initial));
    return Promise.resolve(response({}, 404));
  }); vi.stubGlobal("fetch", fetcher); return fetcher;
}

it("uses the exact original detail order and real four-lens intervals without inventing missing source bytes", async () => {
  window.history.replaceState(null, "", "/w/dealroom/cases/actual-case"); transport(); render(<AVMDealRoomWorkspace />);
  await screen.findByText("由 AVM 模型產生 · actual-model");
  const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
  expect(headings).toEqual(["Summary", "Status & History", "Normalized Margin", "Three-Lens Valuation", "Approval (Finance)", "財務核准", "DataRoom", "Version / Audit"]);
  expect(screen.getByRole("table")).toHaveTextContent("P10"); expect(screen.getByRole("table")).toHaveTextContent("P90");
  expect(screen.getByRole("note")).toHaveTextContent("低信心");
  expect(screen.getByText(/原始附件檔案尚無提供者/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "退回此決策" })).not.toBeInTheDocument();
  expect(screen.getByText(/原始 snapshot IDs：actual-source/)).toBeVisible();
});

it("retains reason and original idempotency key after denial; sends no caller-supplied actor", async () => {
  window.history.replaceState(null, "", "/w/dealroom/cases/actual-case"); const fetcher = transport(); render(<AVMDealRoomWorkspace />);
  fireEvent.change(await screen.findByLabelText("Decision reason"), { target: { value: "Actual canonical finance decision reason" } });
  fireEvent.click(screen.getByLabelText("我已檢視風險、證據與資料限制。"));
  fireEvent.click(screen.getByRole("button", { name: "核准此決策" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 403");
  expect(screen.getByLabelText("Decision reason")).toHaveValue("Actual canonical finance decision reason");
  fireEvent.click(screen.getByRole("button", { name: "核准此決策" }));
  await waitFor(() => expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(2));
  const puts = fetcher.mock.calls.filter(([, init]) => init?.method === "POST").map(([, init]) => init!);
  expect(puts[0].headers).toEqual(puts[1].headers);
  expect(JSON.parse(String(puts[0].body))).toEqual({ expectedVersion: "d".repeat(64), reason: "Actual canonical finance decision reason", reservePrice: null });
  expect((puts[0].headers as Record<string, string>)["If-Actor-Match"]).toBe(context.subjectId);
  expect(screen.queryByText(/Decision id:/)).not.toBeInTheDocument();
});

it("keeps an explicit missing case unavailable instead of opening a list's first case", async () => {
  window.history.replaceState(null, "", "/w/dealroom/cases/missing"); transport(); render(<AVMDealRoomWorkspace />);
  expect(await screen.findByRole("alert")).toHaveTextContent("指定案件未改開其他案件");
  expect(screen.queryByText("由 AVM 模型產生 · actual-model")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "核准此決策" })).not.toBeInTheDocument();
});

it("uses the compact real case list and exact Drawer selection with filters in the URL", async () => {
  window.history.replaceState(null, "", "/w/dealroom/cases?store_id=actual-store"); transport(); render(<AVMDealRoomWorkspace />);
  const table = await screen.findByRole("table"); await waitFor(() => expect(table).toHaveTextContent("actual-case／actual-store"));
  fireEvent.click(within(table).getByText("actual-case／actual-store"));
  const drawer = await screen.findByRole("dialog", { name: "估值案件摘要" });
  expect(await within(drawer).findByRole("link", { name: "開啟案件詳情" })).toHaveAttribute("href", "/w/dealroom/cases/actual-case");
  expect(new URL(window.location.href).searchParams.get("selected")).toBe("actual-case");
  expect(new URL(window.location.href).searchParams.get("store_id")).toBe("actual-store");
  expect(within(drawer).queryByRole("button", { name: "核准此決策" })).not.toBeInTheDocument();
});

it("never substitutes report values when the canonical owner is unavailable", async () => {
  window.history.replaceState(null, "", "/w/dealroom/cases");
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => Promise.resolve(response(String(input) === "/auth/session" ? { subject: context.subjectId } : {}, String(input) === "/auth/session" ? 200 : 503))));
  render(<AVMDealRoomWorkspace />); expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 503");
  expect(screen.queryByText("actual-model")).not.toBeInTheDocument();
});

it("rejects a late JSON response after an A-B-A server context epoch without consuming it", async () => {
  let actual = context; let resolve!: (value: Response) => void;
  const pending = new Promise<Response>((done) => { resolve = done; }); const json = vi.fn(async () => detail());
  const fetcher = vi.fn((input: RequestInfo | URL) => String(input) === "/auth/session" ? Promise.resolve(response({ subject: actual.subjectId }))
    : String(input).endsWith("/context") ? Promise.resolve(response(actual)) : pending);
  const client = createAVMDealRoomClient(() => true, fetcher); const expected = await client.context();
  const reading = client.detail("actual-case", expected); await waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/cases/actual-case"))).toBe(true));
  const rejected = expect(reading).rejects.toBeInstanceOf(SupersededAVMRead);
  actual = { ...context, subjectId: "other-account", contextKey: "b".repeat(64) }; actual = { ...context, contextKey: "c".repeat(64) };
  await act(async () => resolve({ ok: true, json } as unknown as Response));
  await rejected; expect(json).not.toHaveBeenCalled();
});


it("opens the original create form without fake financial defaults and retains typed source after denial", async () => {
  window.history.replaceState(null, "", "/w/dealroom/cases"); const fetcher = transport(); render(<AVMDealRoomWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "建立估值案件" }));
  expect(screen.getByLabelText("近十二月毛利（必填）")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("實際門市 ID"), { target: { value: "actual-store" } });
  for (const label of ["近十二月毛利", "未來十二月毛利預測", "資產帳面價值", "設備公允價值"]) {
    fireEvent.change(screen.getByLabelText(`${label}（必填）`), { target: { value: "200" } });
  }
  fireEvent.change(screen.getByLabelText("原始 snapshot IDs（逗號分隔）"), { target: { value: "actual-source" } });
  fireEvent.click(screen.getByRole("button", { name: "提交建立估值案件" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 403");
  expect(screen.getByLabelText("實際門市 ID")).toHaveValue("actual-store");
  const post = fetcher.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(String(post[0])).toBe("/api/v1/operator/avm/cases");
  expect(JSON.parse(String(post[1]?.body))).toEqual({ valuationInput: { store_id: "actual-store", gm_ttm: 200, forecast_gm_next_12m: 200,
    asset_book_value: 200, equipment_fair_value: 200, source_snapshot_ids: ["actual-source"] } });
  expect(screen.getByLabelText("實際資料品質分數（選填）")).toHaveValue("");
});

it("shows unavailable production report provenance without exposing POC prices or a finance action", async () => {
  const data = detail(); data.report = null; data.reportHistory = []; data.actions.approve = false;
  data.reportAvailability = { state: "unavailable", reasonCode: "AVM_PRODUCTION_REPORT_NOT_VERIFIED" };
  window.history.replaceState(null, "", "/w/dealroom/cases/actual-case"); transport(data); render(<AVMDealRoomWorkspace />);
  expect(await screen.findByText(/AVM_PRODUCTION_REPORT_NOT_VERIFIED/)).toBeVisible();
  expect(screen.queryByText("由 AVM 模型產生 · actual-model")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "核准此決策" })).not.toBeInTheDocument();
});
