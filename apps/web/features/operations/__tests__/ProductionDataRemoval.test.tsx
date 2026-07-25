import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  AdliftReport,
  AuditEvent,
  AvmCase,
  CandidateSiteCard,
  ForecastAlert,
  ForecastOutputSummary,
  HeatZoneScore,
  InterventionSummary,
  ModelReleaseSummary,
  ModelVersionSummary,
  NetPlanScenarioSummary,
  ShellHomeResponse,
  SourceFreshnessEvidence,
} from "@oday-plus/openapi-client";
import type { ApiBinding, BindingState } from "../../../src/lib/api/binding.ts";
import { AdLiftWorkspace } from "../../adlift/AdLiftWorkspace.tsx";
import { AuditWorkspace } from "../../audit/AuditWorkspace.tsx";
import { AvmWorkspace } from "../../avm/AvmWorkspace.tsx";
import { ExpansionWorkspace } from "../../expansion/ExpansionWorkspace.tsx";
import { InterventionWorkspace } from "../../intervention/InterventionWorkspace.tsx";
import { LearningHubWorkspace } from "../../learninghub/LearningHubWorkspace.tsx";
import { HeatZoneMap } from "../../map/HeatZoneMap.tsx";
import { NetPlanWorkspace } from "../../netplan/NetPlanWorkspace.tsx";
import { PriceOpsWorkspace, type LivePricePlan } from "../../priceops/PriceOpsWorkspace.tsx";
import { HomeWorkspace } from "../../shell/HomeWorkspace.tsx";
import type { ApiResource } from "../../shell/resource.ts";
import { OperationsWorkspace } from "../OperationsWorkspace.tsx";
import { resolveProductionMode } from "../ProductionDataState.tsx";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

function binding<T>(items: T[], state: BindingState = items.length ? "ready" : "empty", error?: string): ApiBinding<T> {
  return {
    state,
    items: state === "ready" ? items : [],
    source: state === "ready" ? "api" : "fixture",
    error,
    baseUrl: "https://api.example.com",
    fetchedAt: "2026-07-24T12:00:00Z",
  };
}

describe("non-Operator production data removal", () => {
  it("never allows an explicit POC flag to downgrade a production build", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("ODP_PRODUCT_MODE", "poc");
    expect(resolveProductionMode()).toBe(true);

    vi.stubEnv("ODP_PRODUCT_MODE", "production");
    expect(resolveProductionMode()).toBe(true);
  });

  it("renders only live Operations API rows in production", () => {
    const alert: ForecastAlert = {
      alert_id: "live-alert-901",
      store_id: "live-store-901",
      alert_level: "red",
      status: "open",
    };
    render(<OperationsWorkspace isProduction liveAlerts={binding([alert])} view="alerts" />);

    expect(screen.getByText("live-alert-901")).toBeInTheDocument();
    expect(screen.getByTestId("ops-live-alert-row")).toHaveAttribute("data-testid", "ops-live-alert-row");
    expect(screen.queryByText("Alert center")).not.toBeInTheDocument();
    expect(screen.queryByTestId("alert-drawer")).not.toBeInTheDocument();
  });

  it("renders persisted ForecastOps rows in production", () => {
    const forecast: ForecastOutputSummary = {
      forecast_output_id: "forecast-live-901",
      forecast_version: 3,
      store_id: "store-live-901",
      prediction_run_id: "prediction-live-901",
      p10: 90_000,
      p50: 100_000,
      p90: 112_000,
      w4: { horizon: "w4", p10: 90_000, p50: 100_000, p90: 112_000 },
      w8: { horizon: "w8", p10: 92_000, p50: 103_000, p90: 116_000 },
      w12: { horizon: "w12", p10: 94_000, p50: 106_000, p90: 119_000 },
      w24: { horizon: "w24", p10: 97_000, p50: 110_000, p90: 124_000 },
      trajectory_class: "growing",
      turning_point_probability: 0.2,
      sitescore_gap_ratio: -0.08,
      actual_revenue: 98_000,
      sitescore_baseline_p50: 108_000,
      model_version: "4.0.0",
      engine_name: "statsforecast",
      model_name: "forecastops",
      feature_version: "store-machine-timeseries-view-v1",
      policy_version: "four-light-policy-v1",
      prediction_origin_time: "2026-07-24T08:00:00Z",
      scored_at: "2026-07-24T08:05:00Z",
      source_snapshot_ids: ["pos-live-20260724"],
    };

    render(
      <OperationsWorkspace
        isProduction
        liveForecasts={binding([forecast])}
        view="forecast"
      />,
    );

    expect(screen.getByText("store-live-901")).toBeInTheDocument();
    expect(screen.getByText("statsforecast · store-machine-timeseries-view-v1")).toBeInTheDocument();
    expect(screen.queryByText("Store forecasts")).not.toBeInTheDocument();
  });

  it.each([
    ["empty", undefined],
    ["error", "upstream timeout"],
    ["unconfigured", undefined],
  ] as const)("fails closed for Operations %s without fixture rows", (state, error) => {
    render(
      <OperationsWorkspace
        isProduction
        liveAlerts={state === "unconfigured" ? undefined : binding<ForecastAlert>([], state, error)}
        view="alerts"
      />,
    );

    expect(screen.getByTestId("ops-production-data-state")).toHaveAttribute("data-state", state);
    expect(screen.queryByTestId("ops-live-alerts-table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("alert-drawer")).not.toBeInTheDocument();
  });

  it("rejects a ready binding that is still marked as fixture", () => {
    const fixtureMarkedReady: ApiBinding<ForecastAlert> = {
      ...binding<ForecastAlert>([
        {
          alert_id: "fixture-disguised-as-ready",
          store_id: "fixture-store",
          alert_level: "green",
          status: "open",
        },
      ]),
      source: "fixture",
    };
    render(<OperationsWorkspace isProduction liveAlerts={fixtureMarkedReady} view="alerts" />);

    expect(screen.getByTestId("ops-production-data-state")).toHaveAttribute("data-state", "unconfigured");
    expect(screen.queryByText("fixture-disguised-as-ready")).not.toBeInTheDocument();
  });

  it("renders live NetPlan scenarios without bundled scenario detail", () => {
    const scenario: NetPlanScenarioSummary = {
      scenario_id: "live-scenario-77",
      scenario_name: "Live north plan",
      status: "solved",
      solver_version: "ortools-9.11",
    };
    render(<NetPlanWorkspace isProduction liveScenarios={binding([scenario])} view="scenarios" />);

    expect(screen.getByText("live-scenario-77")).toBeInTheDocument();
    expect(screen.queryByTestId("scenario-drawer")).not.toBeInTheDocument();
  });

  it("renders live Learning Hub releases without bundled model rows", () => {
    const release: ModelReleaseSummary = {
      release_id: "live-release-44",
      model_name: "sitescore",
      to_version: "9.4.0",
      release_type: "CANARY",
    };
    render(<LearningHubWorkspace isProduction liveReleases={binding([release])} view="releases" />);

    expect(screen.getByText("live-release-44")).toBeInTheDocument();
    expect(screen.queryByText("Release decisions")).not.toBeInTheDocument();
  });

  it("renders persisted Learning Hub model versions without bundled models", () => {
    const model: ModelVersionSummary = {
      model_name: "forecastops-live",
      version: "4.0.0",
      artifact_uri: "gs://model-artifacts/forecastops-live/4.0.0/model.bin",
      dataset_snapshot_id: "dataset-live-20260724",
      feature_schema_version: "store-machine-timeseries-view-v1",
      label_version: "forecast-label-v1",
      metrics: { smape: 0.08 },
      stage: "production",
      aliases: ["production"],
      run_id: "mlflow-live-4",
      approved_by: "model-review-board",
      approved_at: "2026-07-24T07:00:00Z",
    };

    render(
      <LearningHubWorkspace
        isProduction
        liveModels={binding([model])}
        view="models"
      />,
    );

    expect(screen.getByText("forecastops-live")).toBeInTheDocument();
    expect(screen.getByText("model-review-board")).toBeInTheDocument();
    expect(screen.queryByText("sitescore-propensity")).not.toBeInTheDocument();
  });

  it("renders live Intervention rows and omits fixed workflow cases", () => {
    const intervention: InterventionSummary = {
      intervention_id: "live-intervention-12",
      store_id: "live-store-12",
      kind: "PRICE",
      status: "OBSERVING",
    };
    render(<InterventionWorkspace isProduction liveInterventions={binding([intervention])} />);

    expect(screen.getByText("live-intervention-12")).toBeInTheDocument();
    expect(screen.queryByTestId("intervention-table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("intervention-drawer")).not.toBeInTheDocument();
  });

  it("renders live Audit events and exposes no fixture export controls", () => {
    const event: AuditEvent = {
      event_id: "live-audit-33",
      event_type: "listing.updated.v1",
      actor: "system",
      action: "update",
      resource: "listing/live-33",
      outcome: "success",
      correlation_id: "corr-live-33",
      occurred_at: "2026-07-24T12:00:00Z",
    };
    render(<AuditWorkspace isProduction liveEvents={binding([event])} view="decisions" />);

    expect(screen.getByTestId("live-drawer-trigger-live-audit-33")).toHaveAttribute(
      "href",
      "/w/audit/decisions/live-audit-33",
    );
    expect(screen.getByText("corr-live-33")).toBeInTheDocument();
    expect(screen.queryByTestId("evidence-export-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("batch-export-panel")).not.toBeInTheDocument();
  });

  it("renders live AVM rows and no bundled valuation table", () => {
    const avmCase: AvmCase = {
      case_id: "live-avm-5",
      store_id: "live-store-5",
      status: "DATA_READY",
      created_by: "finance-user",
      created_at: "2026-07-24T10:00:00Z",
    };
    render(<AvmWorkspace isProduction liveCases={binding([avmCase])} view="cases" />);

    expect(screen.getByText("live-avm-5")).toBeInTheDocument();
    expect(screen.queryByText("case-118")).not.toBeInTheDocument();
  });

  it("keeps live HeatZone API rows but marks an unconfigured production map unavailable", () => {
    const zone: HeatZoneScore = {
      h3_index: "892875a1003ffff",
      score: 82,
      rank: 1,
      unmet_demand: 0.74,
      confidence: 0.91,
      state: "STILL_EXPANDABLE",
    };
    render(<ExpansionWorkspace isProduction liveHeatZones={binding([zone])} view="heatzone" />);

    expect(screen.getByText("892875a1003ffff")).toBeInTheDocument();
    expect(screen.getByTestId("exp-production-map-unavailable")).toBeInTheDocument();
    expect(screen.queryByText("hz-1049")).not.toBeInTheDocument();
    expect(screen.queryByTestId("heat-zone-map-canvas")).not.toBeInTheDocument();
  });

  it("renders only live Candidate API rows in production", () => {
    const candidate: CandidateSiteCard = {
      candidateSiteId: "live-candidate-9",
      address: "台北市測試路 9 號",
      geocodeConfidence: 0.93,
      rent: 120000,
      area: 38,
      feasibilityFlags: [],
      heatZone: "892875a1003ffff",
      status: "screened",
    };
    render(<ExpansionWorkspace isProduction liveCandidates={binding([candidate])} view="candidates" />);

    expect(screen.getByText("live-candidate-9")).toBeInTheDocument();
    expect(screen.queryByTestId("candidate-site-card")).not.toBeInTheDocument();
  });

  it("shows backend-declared stale freshness without substituting fixture lineage", () => {
    const stale: SourceFreshnessEvidence = {
      provider_id: "live-provider-stale",
      source_snapshot_id: "live-stale-snapshot-7",
      data_status: "STALE",
      provider_observed_at: "2026-07-20T12:00:00Z",
      ingested_at: "2026-07-20T12:01:00Z",
      freshness_sla_seconds: 3600,
      correlation_id: "live-stale-correlation-7",
    };
    render(<ExpansionWorkspace isProduction liveFreshness={binding([stale])} view="overview" />);

    expect(screen.getByText("STALE")).toBeInTheDocument();
    expect(screen.getByText("live-stale-snapshot-7")).toBeInTheDocument();
    expect(screen.queryByText("snap-expansion-20260628-0100")).not.toBeInTheDocument();
  });

  it("does not mount local map data when production providers or API provenance are absent", () => {
    render(
      <HeatZoneMap
        candidates={[]}
        dataSource="fixture"
        freshness={{
          status: "UNKNOWN",
          updatedAt: "—",
          modelVersion: "—",
          featureSnapshotTime: "—",
          sourceSnapshotId: "—",
        }}
        listings={[]}
        productionMode
        selectedZoneId=""
        zones={[]}
      />,
    );

    expect(screen.getByTestId("heat-zone-map-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("heat-zone-map-canvas")).not.toBeInTheDocument();
  });

  it.each([
    {
      domain: "AVM",
      renderWorkspace: () => render(<AvmWorkspace isProduction view="cases" />),
      stateId: "avm-production-data-state",
      fixtureText: "vc-5101",
    },
    {
      domain: "Audit",
      renderWorkspace: () => render(<AuditWorkspace isProduction view="decisions" />),
      stateId: "audit-production-data-state",
      fixtureText: "decision-lh-240",
    },
    {
      domain: "Intervention",
      renderWorkspace: () => render(<InterventionWorkspace isProduction />),
      stateId: "intervention-production-data-state",
      fixtureText: "int-3001",
    },
    {
      domain: "Learning Hub",
      renderWorkspace: () => render(<LearningHubWorkspace isProduction view="releases" />),
      stateId: "learning-production-data-state",
      fixtureText: "rel-lh-240-canary",
    },
    {
      domain: "NetPlan",
      renderWorkspace: () => render(<NetPlanWorkspace isProduction view="scenarios" />),
      stateId: "netplan-production-data-state",
      fixtureText: "np-6201",
    },
    {
      domain: "Expansion",
      renderWorkspace: () => render(<ExpansionWorkspace isProduction view="heatzone" />),
      stateId: "exp-production-data-state",
      fixtureText: "hz-1049",
    },
  ])("fails closed for $domain when the production API is unconfigured", ({ renderWorkspace, stateId, fixtureText }) => {
    renderWorkspace();
    expect(screen.getByTestId(stateId)).toHaveAttribute("data-state", "unconfigured");
    expect(screen.queryByText(fixtureText)).not.toBeInTheDocument();
  });

  it("renders only API-backed AdLift rows and derives counts from those rows", () => {
    const report: AdliftReport = {
      report_id: "live-adlift-report-91",
      campaign_id: "live-campaign-91",
      campaign_name: "Live campaign",
      treatment_store_ids: ["live-store-1", "live-store-2"],
      control_store_ids: ["live-control-1"],
      pre_trend_status: "PASS",
      iromi: 1.91,
      evidence_level: "medium",
      recommendation: "CONTINUE",
      causal_claim_allowed: true,
      contamination: [],
      model_version: "live-adlift-v3",
      policy_version: "live-policy-v3",
      generated_at: "2026-07-24T12:00:00Z",
      source_snapshot_ids: ["live-snapshot-91"],
    };
    render(<AdLiftWorkspace isProduction liveReports={binding([report])} />);

    expect(screen.getByText("live-adlift-report-91")).toBeInTheDocument();
    expect(screen.getByText("Live campaign")).toBeInTheDocument();
    expect(screen.getByText("1", { selector: "strong" })).toBeInTheDocument();
    expect(screen.queryByText("adlift-8801")).not.toBeInTheDocument();
    expect(screen.queryByTestId("adlift-table")).not.toBeInTheDocument();
  });

  it.each([
    ["empty", undefined],
    ["error", "adlift API timeout"],
    ["unconfigured", undefined],
  ] as const)("fails closed for AdLift %s without bundled reports", (state, error) => {
    render(
      <AdLiftWorkspace
        isProduction
        liveReports={state === "unconfigured" ? undefined : binding<AdliftReport>([], state, error)}
      />,
    );

    expect(screen.getByTestId("adlift-production-data-state")).toHaveAttribute("data-state", state);
    expect(screen.queryByText("adlift-8801")).not.toBeInTheDocument();
    expect(screen.queryByTestId("adlift-table")).not.toBeInTheDocument();
  });

  it("renders only API-backed PriceOps rows and derives counts from those rows", () => {
    const livePlan: LivePricePlan = {
      plan_id: "live-price-plan-51",
      tenant_id: "live-tenant-51",
      status: "PENDING_APPROVAL",
      items: [{ item_id: "live-item" }],
      created_at: "2026-07-24T12:00:00Z",
      correlation_id: "live-price-correlation-51",
    };
    render(<PriceOpsWorkspace isProduction livePlans={binding([livePlan])} />);

    expect(screen.getByText("live-price-plan-51")).toBeInTheDocument();
    expect(screen.getByText("live-price-correlation-51")).toBeInTheDocument();
    expect(screen.queryByText("price-5101")).not.toBeInTheDocument();
    expect(screen.queryByTestId("priceops-table")).not.toBeInTheDocument();
  });

  it.each([
    ["empty", undefined],
    ["error", "priceops API timeout"],
    ["unconfigured", undefined],
  ] as const)("fails closed for PriceOps %s without bundled plans or fabricated counts", (state, error) => {
    render(
      <PriceOpsWorkspace
        isProduction
        livePlans={state === "unconfigured" ? undefined : binding<LivePricePlan>([], state, error)}
      />,
    );

    expect(screen.getByTestId("priceops-production-data-state")).toHaveAttribute("data-state", state);
    expect(screen.queryByText("price-5101")).not.toBeInTheDocument();
    expect(screen.queryByTestId("priceops-table")).not.toBeInTheDocument();
  });

  it("renders no shell metrics or fabricated counts when the aggregate API is unavailable", () => {
    const home: ApiResource<ShellHomeResponse> = {
      state: "unconfigured",
      data: null,
      source: "none",
      fetchedAt: "2026-07-24T12:00:00Z",
    };
    render(<HomeWorkspace home={home} />);

    expect(screen.getByTestId("home-state")).toBeInTheDocument();
    expect(screen.queryByTestId("home-metrics")).not.toBeInTheDocument();
    expect(screen.queryByTestId("metric-open-tasks")).not.toBeInTheDocument();
  });

  it("renders shell counts exactly as returned by the aggregate API", () => {
    const data: ShellHomeResponse = {
      meta: {
        generatedAt: "2026-07-24T12:00:00Z",
        source: "operator-shell-production",
        role: { id: "opsManager", label: "Operations manager" },
      },
      status: {
        headline: "Live operations state",
        openTasks: 41,
        slaBreached: 6,
        slaAtRisk: 12,
        pendingApprovals: 17,
        unacknowledgedNotifications: 23,
        tone: "warning",
      },
      tasks: [],
      approvals: [],
      decisions: [],
      freshness: [],
      entryPoints: [],
      notifications: [],
      kpis: [],
    };
    const home: ApiResource<ShellHomeResponse> = {
      state: "ready",
      data,
      source: "api",
      baseUrl: "https://api.example.com",
      fetchedAt: "2026-07-24T12:00:00Z",
    };
    render(<HomeWorkspace home={home} />);

    expect(screen.getByTestId("metric-open-tasks-value")).toHaveTextContent("41");
    expect(screen.getByTestId("metric-approvals-value")).toHaveTextContent("17");
    expect(screen.getByTestId("metric-notifications-value")).toHaveTextContent("23");
  });
});
