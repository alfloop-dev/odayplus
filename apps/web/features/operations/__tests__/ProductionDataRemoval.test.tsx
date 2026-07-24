import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  AuditEvent,
  AvmCase,
  CandidateSiteCard,
  ForecastAlert,
  HeatZoneScore,
  InterventionSummary,
  ModelReleaseSummary,
  NetPlanScenarioSummary,
} from "@oday-plus/openapi-client";
import type { ApiBinding, BindingState } from "../../../src/lib/api/binding.ts";
import { AuditWorkspace } from "../../audit/AuditWorkspace.tsx";
import { AvmWorkspace } from "../../avm/AvmWorkspace.tsx";
import { ExpansionWorkspace } from "../../expansion/ExpansionWorkspace.tsx";
import { InterventionWorkspace } from "../../intervention/InterventionWorkspace.tsx";
import { LearningHubWorkspace } from "../../learninghub/LearningHubWorkspace.tsx";
import { HeatZoneMap } from "../../map/HeatZoneMap.tsx";
import { NetPlanWorkspace } from "../../netplan/NetPlanWorkspace.tsx";
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
  it("honors the explicit product mode before the production build default", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("ODP_PRODUCT_MODE", "poc");
    expect(resolveProductionMode()).toBe(false);

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
});
