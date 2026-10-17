import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StoreOpsForecastSection } from "../StoreOpsForecastSection";
import {
  parseCurrentForecast, readCurrentForecast, visibleForecastCsv,
  type ForecastBand, type ForecastReadGuardFactory, type RecordedForecast,
} from "../storeOpsForecastClient";

const STORE = "store-owned";
const ISSUE = "ISS-FC-alert-owned";
const HORIZONS: ForecastBand["horizonWeeks"][] = [4, 8, 12, 24];

// Isolated wire fixture for consumer controls. No model was run or approved.
function forecast(): RecordedForecast {
  return {
    alertId: "alert-owned", forecastOutputId: "forecast-owned", predictionRunId: "run-owned",
    forecastVersion: 3, targetMetric: "revenue",
    bands: HORIZONS.map(weeks => ({
      horizonWeeks: weeks,
      p10: weeks * 10, p50: weeks * 10 + 5, p90: weeks * 10 + 10,
    })),
    actualRevenue: 0, siteScoreBaselineP50: 100, siteScoreGapRatio: -1,
    trajectoryClass: "declining", turningPointProbability: 0.2,
    predictionOriginTime: "2026-10-15T00:00:00+00:00", scoredAt: "2026-10-15T00:01:00+00:00",
    featureVersion: "features-owned", sourceSnapshotIds: ["pos-owned"],
    dataQualityScore: null, dataStalenessDays: null,
    modelBinding: {
      model_service: "forecastops", model_id: "model-owned", model_name: "model-owner", model_version: "7",
      model_stage: "production", dataset_snapshot_id: "dataset-owned", feature_schema_version: "schema-owned",
      label_version: "label-owned", artifact_sha256: "a".repeat(64), model_engine: "registered-oss",
      registry_version: "registry-owned", source_run_id: "training-run-owned", mlflow_run_id: null,
      model_approved_by: null, model_approved_at: null, binding_resolved_at: "2026-10-15T00:00:00+00:00",
    },
    policy: { id: "policy-owned", version: "policy-v1", versionId: "policy-tenant-version" },
  };
}

function envelope() {
  return {
    issue: { id: ISSUE, storeId: STORE, source: "forecastOps", sourceRef: { kind: "forecastops.alert", id: "alert-owned" } },
    forecast: { status: "recorded", reason: null, issueId: ISSUE, storeId: STORE, data: forecast() },
  };
}

function deferred<T>() {
  let settle: (value: T) => void = () => { throw new Error("Deferred not initialized"); };
  let fail: (reason: unknown) => void = () => { throw new Error("Deferred not initialized"); };
  const promise = new Promise<T>((resolve, reject) => { settle = resolve; fail = reject; });
  return { promise, resolve: (value: T) => settle(value), reject: (reason: unknown) => fail(reason) };
}

function ownerLease() {
  let epoch = 0;
  const capture: ForecastReadGuardFactory = current => {
    const original = epoch;
    return async () => current() && epoch === original;
  };
  return { capture, rotate: () => { ++epoch; } };
}

function reply(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => { vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "production"); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("Store Ops actual Forecast consumer", () => {
  it("renders the four original intervals, visible table, zero actual and real source identities", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(reply(envelope())); vi.stubGlobal("fetch", fetcher);
    const owner = ownerLease();
    render(<StoreOpsForecastSection scopeKey="owner-a" issueId={ISSUE} storeId={STORE} roleId="ops-lead" createAuthenticatedReadGuard={owner.capture} />);
    const table = await screen.findByRole("table", { name: /四個原始預測週期/ });
    expect(within(table).getAllByRole("row")).toHaveLength(5);
    for (const weeks of [4, 8, 12, 24]) expect(within(table).getByText(`${weeks} 週`)).toBeVisible();
    expect(within(table).getByText("45")).toBeVisible();
    expect(screen.getByText(/實際營收：0；SiteScore P50 基準：100/)).toBeVisible();
    expect(screen.getByText(/資料品質：未提供；來源距預測日：未提供/)).toBeVisible();
    expect(screen.getByRole("img", { name: /四個預測週期/ })).toBeVisible();
    fireEvent.click(screen.getByText("版本與來源"));
    expect(screen.getByText("run-owned")).toBeVisible();
    expect(screen.getByText("2026-10-15T00:00:00+00:00")).toBeVisible();
    expect(screen.getByText("pos-owned")).toBeVisible();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/operator/store-ops/issues/${ISSUE}/evidence`);
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: "GET", cache: "no-store", credentials: "same-origin",
      headers: { "X-Operator-Role": "ops-lead" } });
    expect(fetcher.mock.calls[0][1]).not.toHaveProperty("body");
    expect(screen.queryByText(/28 天/)).not.toBeInTheDocument();
  });

  const malformed: [string, (value: ReturnType<typeof envelope>) => void][] = [
    ["wrong issue", value => { value.issue.id = "unrelated"; }],
    ["wrong store", value => { value.issue.storeId = "unrelated"; }],
    ["wrong envelope store", value => { value.forecast.storeId = "unrelated"; }],
    ["wrong alert", value => { value.issue.sourceRef.id = "unrelated"; }],
    ["duplicate horizon", value => { value.forecast.data.bands[3].horizonWeeks = 4; }],
    ["missing horizon", value => { value.forecast.data.bands.pop(); }],
    ["nonfinite interval", value => { value.forecast.data.bands[2].p90 = Number.POSITIVE_INFINITY; }],
    ["unordered interval", value => { value.forecast.data.bands[2].p10 = 999; }],
    ["missing run", value => { value.forecast.data.predictionRunId = ""; }],
    ["naive origin", value => { value.forecast.data.predictionOriginTime = "2026-10-15T00:00:00"; }],
    ["malformed origin", value => { value.forecast.data.predictionOriginTime = "2026-02-30T00:00:00Z"; }],
    ["baseline", value => { value.forecast.data.modelBinding.model_engine = "baseline"; }],
    ["missing model identity", value => { value.forecast.data.modelBinding.model_id = ""; }],
    ["missing artifact evidence", value => { value.forecast.data.modelBinding.artifact_sha256 = "invalid"; }],
    ["missing source", value => { value.forecast.data.sourceSnapshotIds = []; }],
  ];
  it.each(malformed)("rejects %s without synthesizing another horizon or source", (_name, mutate) => {
    const value = envelope(); mutate(value);
    expect(parseCurrentForecast(value, ISSUE, STORE).state).toBe("error");
  });

  it("keeps explicit unavailable distinct from zero and never substitutes a fixture", async () => {
    const value = envelope();
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(reply({ ...value, forecast: {
      status: "unavailable", reason: "CURRENT_FORECAST_SOURCE_UNAVAILABLE", issueId: ISSUE, storeId: STORE, data: null,
    } })));
    const owner = ownerLease();
    render(<StoreOpsForecastSection scopeKey="owner-a" issueId={ISSUE} storeId={STORE} roleId="ops-lead" createAuthenticatedReadGuard={owner.capture} />);
    expect(await screen.findByText(/此事件尚無可核對的預測資料/)).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /匯出/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/實際營收：0/)).not.toBeInTheDocument();
  });

  it("does not fetch without the Console authenticated read owner", async () => {
    const fetcher = vi.fn<typeof fetch>(); vi.stubGlobal("fetch", fetcher);
    render(<StoreOpsForecastSection scopeKey="owner-a" issueId={ISSUE} storeId={STORE} roleId="ops-lead" />);
    expect(await screen.findByText(/此事件尚無可核對的預測資料/)).toBeVisible();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("does not fetch without the Console owner epoch even if a guard function is supplied", async () => {
    const fetcher = vi.fn<typeof fetch>(); vi.stubGlobal("fetch", fetcher); const owner = ownerLease();
    render(<StoreOpsForecastSection scopeKey="" issueId={ISSUE} storeId={STORE} roleId="ops-lead" createAuthenticatedReadGuard={owner.capture} />);
    expect(await screen.findByText(/此事件尚無可核對的預測資料/)).toBeVisible();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects response seed metadata even when the Forecast subtree looks complete", () => {
    expect(parseCurrentForecast({ ...envelope(), _meta: { dataMode: "fixture" } }, ISSUE, STORE).state).toBe("error");
  });

  it("does not adopt a response after its authenticated owner changes during HTTP", async () => {
    const response = deferred<Response>(); const owner = ownerLease();
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockReturnValue(response.promise));
    const result = readCurrentForecast({ issueId: ISSUE, storeId: STORE, headers: {},
      guard: owner.capture(() => true), signal: new AbortController().signal });
    await act(async () => { await Promise.resolve(); });
    owner.rotate(); response.resolve(reply(envelope()));
    expect(await result).toEqual({ state: "superseded" });
  });

  it("rejects A to B to A epochs while JSON is pending", async () => {
    const body = deferred<unknown>(); const response = reply({}); const owner = ownerLease();
    vi.spyOn(response, "json").mockReturnValue(body.promise);
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response));
    const result = readCurrentForecast({ issueId: ISSUE, storeId: STORE, headers: {},
      guard: owner.capture(() => true), signal: new AbortController().signal });
    await waitFor(() => expect(response.json).toHaveBeenCalledTimes(1));
    owner.rotate(); owner.rotate(); body.resolve(envelope());
    expect(await result).toEqual({ state: "superseded" });
  });

  it("a stale JSON failure cannot replace the new current object's forecast", async () => {
    const pending = deferred<unknown>(); const stale = reply({});
    vi.spyOn(stale, "json").mockReturnValue(pending.promise);
    const newer = envelope(); newer.issue.id = "ISS-FC-alert-new"; newer.issue.sourceRef.id = "alert-new";
    newer.forecast.issueId = newer.issue.id; newer.forecast.data.alertId = "alert-new";
    newer.forecast.data.predictionRunId = "run-new";
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(stale).mockResolvedValue(reply(newer));
    vi.stubGlobal("fetch", fetcher); const owner = ownerLease();
    const view = render(<StoreOpsForecastSection scopeKey="owner-a" issueId={ISSUE} storeId={STORE} roleId="ops-lead" createAuthenticatedReadGuard={owner.capture} />);
    await waitFor(() => expect(stale.json).toHaveBeenCalledTimes(1));
    owner.rotate();
    view.rerender(<StoreOpsForecastSection scopeKey="owner-b" issueId={newer.issue.id} storeId={STORE} roleId="ops-lead" createAuthenticatedReadGuard={owner.capture} />);
    await screen.findByRole("table");
    await act(async () => { pending.reject(new Error("Previous response JSON failure")); });
    fireEvent.click(screen.getByText("版本與來源"));
    expect(screen.getByText("run-new")).toBeVisible();
    expect(screen.queryByText("run-owned")).not.toBeInTheDocument();
    expect(screen.queryByText(/暫時無法取得/)).not.toBeInTheDocument();
  });

  it("masks the old owner's already displayed table immediately on a new Console epoch", async () => {
    const next = deferred<Response>(); const owner = ownerLease();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(reply(envelope())).mockReturnValueOnce(next.promise);
    vi.stubGlobal("fetch", fetcher);
    const view = render(<StoreOpsForecastSection scopeKey="owner-a:1" issueId={ISSUE} storeId={STORE} roleId="ops-lead" createAuthenticatedReadGuard={owner.capture} />);
    await screen.findByRole("table"); owner.rotate();
    view.rerender(<StoreOpsForecastSection scopeKey="owner-b:2" issueId={ISSUE} storeId={STORE} roleId="ops-lead" createAuthenticatedReadGuard={owner.capture} />);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /匯出目前顯示/ })).not.toBeInTheDocument();
    const current = envelope(); current.forecast.data.predictionRunId = "run-current-owner";
    await act(async () => { next.resolve(reply(current)); });
    await screen.findByRole("table"); fireEvent.click(screen.getByText("版本與來源"));
    expect(screen.getByText("run-current-owner")).toBeVisible();
    expect(screen.queryByText("run-owned")).not.toBeInTheDocument();
  });

  it.each([403, 503])("rejects a stale HTTP %s without adopting its error body", async status => {
    const response = deferred<Response>(); const owner = ownerLease();
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockReturnValue(response.promise));
    const result = readCurrentForecast({ issueId: ISSUE, storeId: STORE, headers: {},
      guard: owner.capture(() => true), signal: new AbortController().signal });
    await act(async () => { await Promise.resolve(); });
    owner.rotate(); response.resolve(reply({ detail: "previous account response" }, status));
    expect(await result).toEqual({ state: "superseded" });
  });

  it("checks the source read's own owner again before visible-data export", async () => {
    const owner = ownerLease(); const create = vi.fn(() => "blob:owned");
    vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = vi.fn(); });
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(reply(envelope())));
    render(<StoreOpsForecastSection scopeKey="owner-a" issueId={ISSUE} storeId={STORE} roleId="ops-lead" createAuthenticatedReadGuard={owner.capture} />);
    await screen.findByRole("table"); owner.rotate();
    fireEvent.click(screen.getByRole("button", { name: /匯出目前顯示/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /匯出目前顯示/ })).toBeEnabled());
    expect(create).not.toHaveBeenCalled();
  });

  it("exports exactly the four visible rows and preserves zero while escaping formula strings", () => {
    const data = forecast(); data.sourceSnapshotIds = ["@untrusted-source"];
    const csv = visibleForecastCsv(data, "=untrusted-store");
    const rows = csv.replace(/^\uFEFF/, "").trimEnd().split("\r\n");
    expect(rows).toHaveLength(5);
    expect(rows[0]).toContain('"prediction_run_id"');
    expect(rows[1]).toContain('"\'=untrusted-store","4","40","45","50","0","100"');
    expect(rows[1]).toContain('"\'@untrusted-source"');
    expect(csv).not.toContain("artifact_uri");
    expect(csv).not.toContain("daily");
  });

  it("nullable baseline stays unavailable and never becomes a fabricated zero", () => {
    const value = envelope(); value.forecast.data.siteScoreBaselineP50 = null;
    const result = parseCurrentForecast(value, ISSUE, STORE);
    expect(result.state).toBe("ready");
    if (result.state !== "ready") throw new Error("Expected exact recorded DTO");
    expect(result.forecast.siteScoreBaselineP50).toBeNull();
  });
});

describe("visible Forecast CSV leading whitespace and controls", () => {
  it.each([
    ["LF formula", "\n=1+1"], ["space formula", "   =1+1"],
    ["mixed whitespace plus", " \t\r\n+SUM(A1)"], ["C0 minus", "\u0000-1"],
    ["escape at", "\u001b@SUM(A1)"], ["C1 formula", "\u0085=1+1"],
    ["NBSP plus", "\u00a0+1"], ["BOM minus", "\ufeff-1"],
    ["zero width format", "\u200b=1+1"], ["direction format", "\u202e@SUM(A1)"],
    ["original tab", "\tstore"], ["original CR", "\rstore"], ["LF text", "\nstore"],
  ])("quotes %s as text without stripping the original value", (_name, value) => {
    const csv = visibleForecastCsv(forecast(), value);
    const quoted = '"' + ("'" + value).replaceAll('"', '""') + '",';
    expect(csv.split("\r\n" + quoted)).toHaveLength(5);
  });

  it.each(["   store-name", "\u200bstore-name", "store=label", "source-1", "0", "123"])(
    "preserves benign metadata string %s", value => {
      const csv = visibleForecastCsv(forecast(), value);
      expect(csv.split('\r\n"' + value + '",')).toHaveLength(5);
      expect(csv).not.toContain('\r\n"\'' + value + '",');
    },
  );

  it("keeps actual negative numbers numeric and original zero unchanged", () => {
    const data = forecast(); data.bands[0] = { horizonWeeks: 4, p10: -45, p50: -30, p90: -10 };
    data.actualRevenue = 0; data.siteScoreBaselineP50 = -100;
    const csv = visibleForecastCsv(data, "store-owned");
    expect(csv).toContain('"store-owned","4","-45","-30","-10","0","-100"');
    expect(csv).not.toContain('"\'-45"');
    expect(csv).not.toContain('"\'-100"');
  });
});


function forecastWithSavedQuality(violated = true): RecordedForecast {
  const data = forecast();
  data.dataStalenessDays = 1;
  data.featureSnapshotTime = "2026-10-14T00:00:00+00:00";
  data.qualityAtRun = { state: "recorded", reason: null, data: {
    alertId: data.alertId, forecastOutputId: data.forecastOutputId, predictionRunId: data.predictionRunId,
    policy: data.policy, evaluatedAt: data.scoredAt, featureSnapshotTime: data.featureSnapshotTime,
    maxStalenessDays: violated ? 0 : 7, stalenessDays: 1, violated,
    onViolation: "SUPPRESS_HIGH_CONFIDENCE", actualAlertLevel: violated ? "orange" : "red",
    thresholdAlertLevel: "red", alertReasonCode: violated ? "data_quality_stale" : "sitescore_gap",
  } };
  return data;
}

function qualityEnvelope(violated = true) {
  const value = envelope();
  return { ...value, forecast: { ...value.forecast, data: forecastWithSavedQuality(violated) } };
}

it.each([true, false])("shows the saved execution-time guard %s with its actual limit and snapshot, not current freshness", async violated => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(reply(qualityEnvelope(violated))));
  const owner = ownerLease();
  render(<StoreOpsForecastSection scopeKey="owner-a" issueId={ISSUE} storeId={STORE} roleId="ops-lead" createAuthenticatedReadGuard={owner.capture} />);
  const quality = await screen.findByTestId("forecast-quality-at-run");
  expect(within(quality).getByText(violated ? "執行時資料品質限制已觸發" : "執行時未觸發此資料時效限制")).toBeVisible();
  expect(within(quality).getByText(new RegExp(`政策上限 ${violated ? 0 : 7} 天`))).toBeVisible();
  expect(within(quality).getAllByText("2026-10-14T00:00:00+00:00").length).toBeGreaterThan(0);
  expect(within(quality).getByText(/不表示目前資料時效或整體品質合格/)).toBeVisible();
  expect(screen.queryByText("FRESH")).not.toBeInTheDocument();
  expect(screen.queryByText("LOW_CONFIDENCE")).not.toBeInTheDocument();
  expect(screen.getByRole("table", { name: /四個原始預測週期/ })).toBeVisible();
});

const malformedQuality: [string, Record<string, unknown>][] = [
  ["wrong alert", { alertId: "other" }], ["wrong output", { forecastOutputId: "other" }],
  ["wrong run", { predictionRunId: "other" }],
  ["wrong policy", { policy: { id: "other", version: "other", versionId: "other" } }],
  ["string boolean", { violated: "false" }], ["contradictory boolean", { violated: false }],
  ["wrong staleness", { stalenessDays: 3 }], ["contradictory result", { actualAlertLevel: "red" }],
  ["naive snapshot", { featureSnapshotTime: "2026-10-14T00:00:00" }],
];
it.each(malformedQuality)("keeps %s saved-quality evidence unknown while preserving the four verified bands", async (_name, patch) => {
  const value = qualityEnvelope();
  const quality = value.forecast.data.qualityAtRun;
  if (!quality || quality.state !== "recorded") throw new Error("Explicit saved guard fixture required");
  const wire = { ...value, forecast: { ...value.forecast, data: { ...value.forecast.data,
    qualityAtRun: { ...quality, data: { ...quality.data, ...patch } },
  } } };
  vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(reply(wire)));
  const owner = ownerLease();
  render(<StoreOpsForecastSection scopeKey="owner-a" issueId={ISSUE} storeId={STORE} roleId="ops-lead" createAuthenticatedReadGuard={owner.capture} />);
  expect(await screen.findByRole("table", { name: /四個原始預測週期/ })).toBeVisible();
  expect(screen.getByTestId("forecast-quality-unknown")).toHaveTextContent("不代表未觸發或目前資料新鮮");
  expect(screen.queryByTestId("forecast-quality-at-run")).not.toBeInTheDocument();
  expect(screen.queryByText("FRESH")).not.toBeInTheDocument();
});

it("retains unknown quality for an explicit shadow binding without a saved guard or score", async () => {
  const value = envelope(); value.forecast.data.modelBinding.model_stage = "shadow";
  vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(reply(value)));
  const owner = ownerLease();
  render(<StoreOpsForecastSection scopeKey="owner-a" issueId={ISSUE} storeId={STORE} roleId="ops-lead" createAuthenticatedReadGuard={owner.capture} />);
  expect(await screen.findByRole("table", { name: /四個原始預測週期/ })).toBeVisible();
  expect(screen.getByTestId("forecast-quality-unknown")).toBeVisible();
  expect(screen.queryByText("FRESH")).not.toBeInTheDocument();
  expect(screen.queryByText("LOW_CONFIDENCE")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("版本與來源"));
  expect(screen.getByText("shadow")).toBeVisible();
});

it("shares one captured Forecast owner read between the real workspace's top evidence warning and tab", async () => {
  const { DesignStoreOpsWorkspace } = await import("../DesignAlignedWorkspaces");
  const value = qualityEnvelope();
  const issue = { ...value.issue, title: "Current owned forecast issue", storeName: "Owned store", status: "new", severity: "high",
    ownerRoleId: "opsLead", ownerName: "Owner", slaDueAt: "2026-10-16T00:00:00Z",
    createdAt: "2026-10-15T00:00:00Z", updatedAt: "2026-10-15T00:00:00Z", evidenceIds: ["forecast-evidence"], summary: "Current saved source" };
  const snapshot = { issues: [issue], stores: [{ id: STORE, name: "Owned store", city: "Owned city", district: "Owned district",
    manager: "Owner", riskScore: null, lights: { demand: null, operations: null, staffing: null, margin: null } }],
    evidence: [{ id: "forecast-evidence", issueId: ISSUE, kind: "forecastOps", sourceLabel: "Saved Forecast",
      occurredAt: "2026-10-15T00:00:00Z", polarity: "supporting", title: "Saved source", summary: "Saved evidence", confidence: null }],
    auditEvents: [] };
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async input => {
    const url = String(input);
    if (url.startsWith("/api/v1/operator/store-ops/issues?")) return reply(snapshot);
    if (url === `/api/v1/operator/store-ops/issues/${ISSUE}/evidence`) return reply(value);
    throw new Error("Unrelated fetch is not a source");
  });
  vi.stubGlobal("fetch", fetcher);
  const owner = ownerLease();
  render(<DesignStoreOpsWorkspace initialIssueId={ISSUE} forecastScopeKey="owner-a"
    createAuthenticatedReadGuard={owner.capture} onOpenWorkflow={vi.fn()} />);
  const quality = await screen.findByTestId("forecast-quality-at-run");
  const fusion = screen.getByRole("heading", { name: "證據融合" }).closest("section");
  expect(fusion).not.toBeNull();
  expect(fusion).toContainElement(quality);
  expect(quality.nextElementSibling?.className).toContain("evidenceCards");
  fireEvent.click(screen.getByRole("tab", { name: "ForecastOps" }));
  expect(await screen.findByRole("table", { name: /四個原始預測週期/ })).toBeVisible();
  expect(screen.getAllByTestId("forecast-quality-at-run")).toHaveLength(1);
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith(`/${ISSUE}/evidence`))).toHaveLength(1);
  const createObjectURL = vi.fn(() => "blob:should-not-be-created");
  vi.stubGlobal("URL", class extends URL { static createObjectURL = createObjectURL; static revokeObjectURL = vi.fn(); });
  owner.rotate();
  fireEvent.click(screen.getByRole("button", { name: "匯出目前顯示的預測 CSV" }));
  await act(async () => { await Promise.resolve(); });
  expect(createObjectURL).not.toHaveBeenCalled();
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith(`/${ISSUE}/evidence`))).toHaveLength(1);
});

it("exports only the same saved execution-time quality values and leaves missing quality blank", () => {
  const data = forecastWithSavedQuality();
  const csv = visibleForecastCsv(data, STORE);
  expect(csv).toContain('"quality_guard_state_at_run","quality_evaluated_at","quality_max_staleness_days"');
  expect(csv).toContain('"2026-10-14T00:00:00+00:00","recorded","2026-10-15T00:01:00+00:00","0","true","SUPPRESS_HIGH_CONFIDENCE"');
  expect(visibleForecastCsv(forecast(), STORE)).toContain('"unavailable","","","","","","",""');
  expect(csv).not.toContain('"FRESH"');
});


function forecastWithSavedRecommendation(): RecordedForecast {
  const data = forecastWithSavedQuality();
  data.alertAtRun = { state: "recorded", reason: null, data: {
    alertId: data.alertId, level: "orange", reasonCode: "data_quality_stale", openedAt: data.scoredAt,
  } };
  data.recommendationAtAlert = { state: "recorded", reason: null, data: {
    handoffId: "handoff-owned", alertId: data.alertId, forecastOutputId: data.forecastOutputId,
    predictionRunId: data.predictionRunId, policy: data.policy, createdAt: data.scoredAt,
    handoffStatus: "proposed", actionIds: ["launch_local_promotion", "review_price_packaging"],
  } };
  data.rootCause = { state: "unavailable", reason: "ROOT_CAUSE_CANDIDATE_UNPRODUCED",
    causeCandidate: null, evidenceStrength: null, dataConfidence: null };
  return data;
}

function recommendationEnvelope() {
  const value = envelope();
  return { ...value, forecast: { ...value.forecast, data: forecastWithSavedRecommendation() } };
}

function savedRecommendationWorkspaceSnapshot() {
  const value = envelope();
  return { issues: [{ ...value.issue, title: "Saved recommendation issue", storeName: "Owned store", status: "new", severity: "high",
    ownerRoleId: "opsLead", ownerName: "Owner", slaDueAt: "2026-10-16T00:00:00Z",
    createdAt: "2026-10-15T00:00:00Z", updatedAt: "2026-10-15T00:00:00Z", evidenceIds: ["forecast-source"], summary: "Owned saved evidence" }],
    stores: [{ id: STORE, name: "Owned store", city: "Owned city", district: "Owned district", manager: "Owner", riskScore: null,
      lights: { demand: null, operations: null, staffing: null, margin: null } }],
    evidence: [{ id: "forecast-source", issueId: ISSUE, kind: "forecastOps", sourceLabel: "ForecastOps", occurredAt: "2026-10-15T00:00:00Z",
      polarity: "supporting", title: "Owned alert", summary: "Original saved source", confidence: null }], auditEvents: [] };
}

it("reads the actual workspace's same-alert saved actions without manufacturing cause, strength or confidence", async () => {
  const { DesignStoreOpsWorkspace } = await import("../DesignAlignedWorkspaces");
  const value = recommendationEnvelope();
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async input => String(input).includes("/evidence")
    ? reply(value) : reply(savedRecommendationWorkspaceSnapshot()));
  vi.stubGlobal("fetch", fetcher); const owner = ownerLease();
  render(<DesignStoreOpsWorkspace initialIssueId={ISSUE} forecastScopeKey="owner-a" createAuthenticatedReadGuard={owner.capture} onOpenWorkflow={vi.fn()} />);
  await screen.findByRole("table", { name: /四個原始預測週期/ });
  const area = screen.getByTestId("forecast-saved-recommendation");
  expect(within(area).getByText(/launch_local_promotion/)).toBeVisible();
  expect(within(area).getByText(/review_price_packaging/)).toBeVisible();
  expect(within(area).getByText(/handoff-owned/)).toBeVisible();
  expect(within(area).getByText(/保存告警：orange／data_quality_stale/)).toBeVisible();
  expect(within(area).getByText(/自動根因候選、證據強度與資料信心尚未提供/)).toBeVisible();
  expect(within(area).getByText(/不代表因果判定、核准或已執行處置/)).toBeVisible();
  expect(screen.queryByText(/完成根因分類，必要時/)).not.toBeInTheDocument();
  expect(fetcher.mock.calls.filter(([url]) => String(url).includes("/evidence"))).toHaveLength(1);
  for (const [, init] of fetcher.mock.calls) expect(init?.body).toBeUndefined();
});

it("keeps a missing saved handoff unavailable instead of status-based AI advice or fixture causes", async () => {
  const { DesignStoreOpsWorkspace } = await import("../DesignAlignedWorkspaces");
  vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async input => String(input).includes("/evidence")
    ? reply(envelope()) : reply(savedRecommendationWorkspaceSnapshot())));
  const owner = ownerLease();
  render(<DesignStoreOpsWorkspace initialIssueId={ISSUE} forecastScopeKey="owner-a" createAuthenticatedReadGuard={owner.capture} onOpenWorkflow={vi.fn()} />);
  await screen.findByRole("table", { name: /四個原始預測週期/ });
  const area = screen.getByTestId("forecast-saved-recommendation");
  expect(within(area).getByText("此告警尚無可核對的保存建議。")).toBeVisible();
  expect(within(area).getByText(/自動根因候選、證據強度與資料信心尚未提供/)).toBeVisible();
  expect(screen.queryByText(/完成根因分類，必要時/)).not.toBeInTheDocument();
  expect(within(area).queryByText(/Price|0.67|67%/)).not.toBeInTheDocument();
});

const malformedSavedRecommendation: [string, Record<string, unknown>][] = [
  ["other alert", { alertId: "other" }], ["other output", { forecastOutputId: "other" }],
  ["other run", { predictionRunId: "other" }], ["other policy", { policy: { id: "other", version: "other", versionId: "other" } }],
  ["naive time", { createdAt: "2026-10-15T00:00:00" }], ["invented action", { actionIds: ["invented-cause"] }],
  ["duplicate action", { actionIds: ["review_staffing", "review_staffing"] }],
];
it.each(malformedSavedRecommendation)("does not promote %s to saved advice", async (_name, patch) => {
  const { recordedRecommendationAtAlert } = await import("../storeOpsForecastClient");
  const value = recommendationEnvelope(); const original = value.forecast.data.recommendationAtAlert;
  if (!original || original.state !== "recorded") throw new Error("Explicit saved recommendation fixture required");
  const wire = { ...value, forecast: { ...value.forecast, data: { ...value.forecast.data,
    recommendationAtAlert: { ...original, data: { ...original.data, ...patch } },
  } } };
  const parsed = parseCurrentForecast(wire, ISSUE, STORE);
  expect(parsed.state).toBe("ready");
  if (parsed.state !== "ready") throw new Error("Verified forecast bands must remain available");
  expect(recordedRecommendationAtAlert(parsed.forecast)).toEqual({
    state: "unavailable", reason: "RECORDED_RECOMMENDATION_UNAVAILABLE", data: null,
  });
});

it("masks the saved advice immediately across current workspace owner A-B-A epochs", async () => {
  const { DesignStoreOpsWorkspace } = await import("../DesignAlignedWorkspaces");
  const pending = deferred<Response>(); let evidenceReads = 0;
  const a = recommendationEnvelope();
  const b = recommendationEnvelope();
  if (b.forecast.data.recommendationAtAlert?.state !== "recorded") throw new Error("Explicit recommendation required");
  b.forecast.data.recommendationAtAlert.data.handoffId = "handoff-owner-b";
  const nextA = recommendationEnvelope();
  if (nextA.forecast.data.recommendationAtAlert?.state !== "recorded") throw new Error("Explicit recommendation required");
  nextA.forecast.data.recommendationAtAlert.data.handoffId = "handoff-owner-a-fresh";
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async input => {
    if (!String(input).includes("/evidence")) return reply(savedRecommendationWorkspaceSnapshot());
    ++evidenceReads;
    return evidenceReads === 1 ? reply(a) : evidenceReads === 2 ? pending.promise : reply(nextA);
  });
  vi.stubGlobal("fetch", fetcher); const owner = ownerLease();
  const view = render(<DesignStoreOpsWorkspace initialIssueId={ISSUE} forecastScopeKey="owner-a:1" createAuthenticatedReadGuard={owner.capture} onOpenWorkflow={vi.fn()} />);
  await screen.findByText(/建議來源：handoff-owned/);
  owner.rotate();
  view.rerender(<DesignStoreOpsWorkspace initialIssueId={ISSUE} forecastScopeKey="owner-b:2" createAuthenticatedReadGuard={owner.capture} onOpenWorkflow={vi.fn()} />);
  expect(screen.queryByText(/建議來源：handoff-owned/)).not.toBeInTheDocument();
  await waitFor(() => expect(evidenceReads).toBe(2));
  owner.rotate();
  view.rerender(<DesignStoreOpsWorkspace initialIssueId={ISSUE} forecastScopeKey="owner-a:3" createAuthenticatedReadGuard={owner.capture} onOpenWorkflow={vi.fn()} />);
  await screen.findByText(/handoff-owner-a-fresh/);
  await act(async () => { pending.resolve(reply(b)); });
  expect(screen.queryByText(/handoff-owner-b/)).not.toBeInTheDocument();
  expect(screen.queryByText(/建議來源：handoff-owned/)).not.toBeInTheDocument();
  expect(screen.getByText(/handoff-owner-a-fresh/)).toBeVisible();
  expect(evidenceReads).toBe(3);
});
