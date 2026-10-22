import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CanonicalMonitoringPanel } from "../governance/CanonicalMonitoringPanel";
import { evaluateCanonicalMonitoring, parseCanonicalMonitoringItem, readCanonicalMonitoring,
  type CanonicalMonitoringGuardFactory, type CanonicalMonitoringSelection } from "../governance/canonicalMonitoringClient";

// Explicit response double: not real model inference, production IAM or UAT.
function row() {
  return { evaluation_id: "owned-evaluation", audit_event_id: "owned-audit", requested_by: "actual-server-subject",
    model_name: "forecast_revenue_interval", model_version: "1.0.0", dataset_snapshot_id: "training-source",
    created_at: "2026-10-16T04:00:00+00:00", signal_type: "PREDICTION_DRIFT",
    observed_metrics: { prediction_drift_share: 0 }, drift_detected: false,
    decision_policy_version_id: "original-policy:global-deployment", reference_snapshot_id: "forecast-record-set:reference",
    current_snapshot_id: "forecast-record-set:current", cohort_key: "forecastops:owned-tenant:owned-store:revenue:w4",
    audit_delivery: "pending_or_delivered_separately", recommended_action: "CONTINUE_OBSERVING",
    source_provenance: { schema: "forecast.canonical-monitoring.v1", tenant_id: "owned-tenant", store_id: "owned-store",
      release_id: "owned-release", release_revision: 2, model_name: "forecast_revenue_interval", model_version: "1.0.0",
      model_dataset_snapshot_id: "training-source", horizon_days: 28, monitoring_window: "24h",
      release_executed_at: "2026-10-16T01:00:00+00:00", release_window_end: "2026-10-17T01:00:00+00:00",
      assessed_at: "2026-10-16T04:00:00+00:00", reference_ids: ["reference-output"], current_ids: ["current-output"],
      reference_coverage: ["2026-10-16T02:00:00+00:00", "2026-10-16T02:00:00+00:00"],
      current_coverage: ["2026-10-16T03:00:00+00:00", "2026-10-16T03:00:00+00:00"],
      source_digest: "a".repeat(64), policy_digest: "b".repeat(64), policy: { policy_version_id: "original-policy:global-deployment", policy_label: "original-policy", tenant_id: "global-deployment", policy_kind: "model_performance_drift", parameters: {}, declared_inputs: ["prediction_outputs"] },
      reference_record_set_id: "forecast-record-set:reference", current_record_set_id: "forecast-record-set:current",
      signal: "prediction_distribution_drift", mature_error_available: false, full_window_observed: false,
      source_manifest: ["reference-output", "current-output"].map((id, index) => ({ forecast_output_id: id,
        prediction_run_id: `owned-run-${index}`, prediction_value: 0, sha256: "c".repeat(64),
        scored_at: `2026-10-16T0${index + 2}:00:00+00:00`,
        binding: { model_name: "forecast_revenue_interval", model_version: "1.0.0", dataset_snapshot_id: "training-source",
          model_id: "forecast_revenue_interval:1.0.0", model_service: "forecastops", model_stage: "production",
          feature_schema_version: "actual-feature-v1", label_version: "actual-label-v1", model_engine: "registered-estimator",
          artifact_sha256: "d".repeat(64), registry_version: "owned-registry-1", source_run_id: "owned-model-run",
          mlflow_run_id: "owned-model-run", model_approved_by: "actual-recorded-reviewer",
          model_approved_at: "2026-10-16T00:00:00+00:00", binding_resolved_at: "2026-10-16T01:00:00+00:00" } })) } };
}
const envelope = () => ({ release_id: "owned-release", state: "available", items: [row()], unavailable_count: 0, reason: null });
const unavailable = () => ({ release_id: "owned-release", state: "unavailable", items: [], unavailable_count: 0, reason: "CANONICAL_MONITORING_EVALUATION_UNAVAILABLE" });
const factory: CanonicalMonitoringGuardFactory = current => async () => current();

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("canonical release monitoring current-source client and original drawer content", () => {
  it("keeps actual zero and explicit period coverage distinct from mature error/full-window success", () => {
    const item = parseCanonicalMonitoringItem(row(), "owned-release");
    expect(item?.driftShare).toBe(0);
    expect(item?.referenceIds).toEqual(["reference-output"]);
    expect(item?.releaseRevision).toBe(2);
    expect(item?.recommendedAction).toBe("CONTINUE_OBSERVING");
  });

  it.each(["release", "revision", "overlap", "policy", "coverage", "manifest", "number", "digest", "mature", "date", "binding", "value"])("rejects malformed or cross-owner %s metadata without repairing it", kind => {
    const value = row();
    if (kind === "release") value.source_provenance.release_id = "other-release";
    if (kind === "revision") value.source_provenance.release_revision = 0;
    if (kind === "overlap") value.source_provenance.current_ids = ["reference-output"];
    if (kind === "policy") value.decision_policy_version_id = "other-policy";
    if (kind === "coverage") value.source_provenance.current_coverage[1] = "2026-10-18T01:00:00+00:00";
    if (kind === "manifest") value.source_provenance.source_manifest[0].forecast_output_id = "other-output";
    if (kind === "number") value.observed_metrics.prediction_drift_share = Number.NaN;
    if (kind === "digest") value.source_provenance.source_digest = "not-a-digest";
    if (kind === "mature") value.source_provenance.mature_error_available = true;
    if (kind === "date") value.source_provenance.assessed_at = "2026-02-31T00:00:00Z";
    if (kind === "binding") value.source_provenance.source_manifest[0].binding.artifact_sha256 = "bad";
    if (kind === "value") value.source_provenance.source_manifest[0].prediction_value = Number.POSITIVE_INFINITY;
    expect(parseCanonicalMonitoringItem(value, "owned-release")).toBeNull();
  });

  it("requires captured authenticated scope and rechecks across response and JSON", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(envelope()), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    const abort = new AbortController();
    expect((await readCanonicalMonitoring("owned-release", "ops-lead", undefined, abort.signal)).state).toBe("unavailable");
    expect(fetch).not.toHaveBeenCalled();
    const guard = vi.fn().mockResolvedValueOnce(true).mockResolvedValueOnce(true).mockResolvedValueOnce(false);
    expect((await readCanonicalMonitoring("owned-release", "ops-lead", guard, abort.signal)).state).toBe("superseded");
    expect(guard).toHaveBeenCalledTimes(3);
  });

  it.each(["http", "json", "seed"])("shows %s read failures without fallback observations", async kind => {
    vi.stubGlobal("fetch", vi.fn(async () => kind === "http" ? new Response("{}", { status: 503 })
      : kind === "json" ? new Response("not-json", { status: 200 })
      : new Response(JSON.stringify({ ...envelope(), source: "seed" }), { status: 200 })));
    expect((await readCanonicalMonitoring("owned-release", "ops-lead", async () => true, new AbortController().signal)).state).toBe("error");
  });

  it("submits only selected saved IDs and verifies the matching receipt", async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ item: row() }), { status: 201 }));
    vi.stubGlobal("fetch", fetch);
    const selection: CanonicalMonitoringSelection = { store_id: "owned-store", horizon_days: 28, reference_ids: ["reference-output"], current_ids: ["current-output"] };
    const result = await evaluateCanonicalMonitoring("owned-release", "ops-lead", selection, async () => true, new AbortController().signal);
    expect(result.state).toBe("saved");
    expect(fetch).toHaveBeenCalledTimes(1);
    const init = fetch.mock.calls[0][1];
    expect(JSON.parse(String(init?.body))).toEqual(selection);
    expect(init?.credentials).toBe("same-origin");
    expect(init?.cache).toBe("no-store");
    expect(String(init?.body)).not.toMatch(/observed_metrics|guardrails|evaluated_by|as_of|release_revision/);
  });

  it("renders actual saved drift and missing maturity without an internal JSON dump", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(envelope()), { status: 200 })));
    const { container } = render(<CanonicalMonitoringPanel releaseId="owned-release" roleId="ops-lead" scopeKey="owner-a" createAuthenticatedReadGuard={factory} />);
    expect(await screen.findByText(/本次未偵測到分布漂移/)).toHaveTextContent("0 /");
    expect(screen.getByText(/成熟期預測誤差尚未提供/)).toBeInTheDocument();
    expect(screen.getByText(/仍由原發布流程核准/)).toBeInTheDocument();
    expect(screen.queryByText("HEALTHY")).not.toBeInTheDocument();
    expect(container.querySelector("pre")).toBeNull();
    expect(screen.getByText("original-policy:global-deployment")).toBeInTheDocument();
    expect(screen.getByText("2026-10-16T04:00:00+00:00")).toBeInTheDocument();
    expect(container.querySelector("form, input, textarea, select")).toBeNull();
    expect(screen.queryByRole("button", { name: /計算|評估|canonical|horizon/ })).not.toBeInTheDocument();
    expect(screen.getByText("稽核來源").closest("details")).not.toHaveAttribute("open");
  });

  it("only rereads saved evaluations after a read failure and never creates a monitoring command", async () => {
    let reads = 0;
    const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") throw new Error("The production drawer must not create evaluations");
      return ++reads === 2 ? new Response("{}", { status: 503 }) : new Response(JSON.stringify(envelope()), { status: 200 });
    });
    vi.stubGlobal("fetch", fetch);
    const { container } = render(<CanonicalMonitoringPanel releaseId="owned-release" roleId="ops-lead" scopeKey="owner-a" createAuthenticatedReadGuard={factory} />);
    await screen.findByText(/本次未偵測到分布漂移/);
    expect(container.querySelector("form, input, textarea, select")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重新讀取監測狀態" }));
    await screen.findByRole("alert");
    expect(screen.queryByText(/本次未偵測到分布漂移/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新讀取監測狀態" }));
    await screen.findByText(/本次未偵測到分布漂移/);
    expect(reads).toBe(3);
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it("hides a completed old owner read across role/scope A-B-A changes", async () => {
    let resolveOld: (value: Response) => void = () => {};
    const deferred = new Promise<Response>(resolve => { resolveOld = resolve; });
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async () => ++calls === 1 ? deferred : new Response(JSON.stringify(unavailable()), { status: 200 })));
    const { rerender } = render(<CanonicalMonitoringPanel releaseId="owned-release" roleId="ops-lead" scopeKey="owner-a" createAuthenticatedReadGuard={factory} />);
    await waitFor(() => expect(calls).toBe(1));
    rerender(<CanonicalMonitoringPanel releaseId="owned-release" roleId="pm-audit" scopeKey="owner-b" createAuthenticatedReadGuard={factory} />);
    await screen.findByText(/尚無本發布可驗證/);
    rerender(<CanonicalMonitoringPanel releaseId="owned-release" roleId="ops-lead" scopeKey="owner-a" createAuthenticatedReadGuard={factory} />);
    await screen.findByText(/尚無本發布可驗證/);
    await act(async () => { resolveOld(new Response(JSON.stringify(envelope()), { status: 200 })); });
    expect(screen.queryByText(/本次未偵測到分布漂移/)).not.toBeInTheDocument();
    expect(screen.queryByText(/owned-audit/)).not.toBeInTheDocument();
  });

  it("keeps the internal selected-ID command port scoped and never displays its stale receipt in the read-only drawer", async () => {
    let complete: (value: Response) => void = () => {};
    const command = new Promise<Response>(resolve => { complete = resolve; });
    let commandOwnerCurrent = true;
    const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => init?.method === "POST"
      ? command : new Response(JSON.stringify(unavailable()), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    const { rerender } = render(<CanonicalMonitoringPanel releaseId="owned-release" roleId="ops-lead" scopeKey="owner-a" createAuthenticatedReadGuard={factory} />);
    await screen.findByText(/尚無本發布可驗證/);
    const selection: CanonicalMonitoringSelection = { store_id: "owned-store", horizon_days: 28,
      reference_ids: ["reference-output"], current_ids: ["current-output"] };
    const pending = evaluateCanonicalMonitoring("owned-release", "ops-lead", selection,
      async () => commandOwnerCurrent, new AbortController().signal);
    await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1));
    commandOwnerCurrent = false;
    rerender(<CanonicalMonitoringPanel releaseId="owned-release" roleId="pm-audit" scopeKey="owner-b" createAuthenticatedReadGuard={factory} />);
    await screen.findByText(/尚無本發布可驗證/);
    await act(async () => { complete(new Response(JSON.stringify({ item: row() }), { status: 201 })); });
    expect((await pending).state).toBe("superseded");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/監測已保存/)).not.toBeInTheDocument();
    expect(screen.queryByText(/owned-audit/)).not.toBeInTheDocument();
  });

});
