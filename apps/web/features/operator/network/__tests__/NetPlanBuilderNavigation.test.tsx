import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { lazy, Suspense, type ComponentType } from "react";
import { NetworkFindAreasWorkspace } from "../../NetworkFindAreasWorkspace";
import type { CanonicalBuilder } from "../netplanBuilderClient";

const navigation = vi.hoisted(() => ({ search: "ws=network&tab=rebalance&view=first" }));
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/operator", useRouter: () => router,
  useSearchParams: () => new URLSearchParams(navigation.search),
}));
// Load the actual deferred panel; no builder/transport/session helper is mocked.
vi.mock("next/dynamic", () => ({ default: (loader: () => Promise<unknown>) => {
  const Component = lazy(async () => {
    const loaded = await loader();
    return { default: ((loaded as { default?: unknown })?.default ?? loaded) as ComponentType<Record<string, unknown>> };
  });
  return (props: Record<string, unknown>) => <Suspense fallback={null}><Component {...props} /></Suspense>;
} }));
vi.mock("../HeatZoneMap", () => ({ HeatZoneMap: () => <div data-testid="unused-WebGL-renderer" /> }));

const allowedTabs = ["rebalance"];
const guard = (current: () => boolean) => async () => current();
function transportBuilder(budget: number): CanonicalBuilder {
  return {
    scenarioId: "route-owned-scenario", scenarioName: "Declared route transport fixture",
    planningHorizon: "2026-H2", status: "draft", problemHash: "a".repeat(64),
    editable: true, canSolve: true, policyAvailable: false, disclosurePolicyVersionId: null,
    requiresNewDraft: false, isStale: false, solve: null, statusHistory: [],
    modelVersion: "transport-model", featureVersion: "transport-features",
    solverVersion: "transport-solver", policyVersion: "transport-policy",
    constraints: { max_budget: budget, min_expected_gross_margin: null, min_capacity_delta: null,
      max_average_risk: null, min_action_counts: {}, max_action_counts: {} },
    optionsByEntity: { "route-owned-store": [{ entity_id: "route-owned-store", action: "KEEP",
      expected_gross_margin: 100, budget_cost: 0, risk_score: 0.1, capacity_delta: 0,
      source_snapshot_ids: ["declared-transport-source"], notes: [] }] },
  };
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "Content-Type": "application/json" },
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); window.sessionStorage.clear(); });

it("rebuilds the real Workspace builder lease after same-persona URL changes and Back", async () => {
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
  vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "production");
  window.sessionStorage.clear();
  navigation.search = "ws=network&tab=rebalance&view=first";
  let builderReads = 0;
  const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const path = new URL(String(input), window.location.origin).pathname;
    if (path.endsWith("/netplan/builder")) {
      builderReads += 1;
      return response(transportBuilder(1200 + builderReads));
    }
    if (path === "/api/v1/operator/network-rebalance") return response({ source: "api", stores: [{
      id: "route-owned-store", storeId: "route-owned-store", storeName: "Declared route store",
      status: "avmready", summary: "Transport-only canonical queue row", netPlanScenarios: [],
    }] });
    return response({ detail: "No unrelated transport fixture" }, 503);
  });
  vi.stubGlobal("fetch", fetcher);
  const element = () => <NetworkFindAreasWorkspace activeRoleId="expansion-manager"
    allowedTabIds={allowedTabs} createAuthenticatedReadGuard={guard} />;
  const view = render(element());
  expect(await screen.findByDisplayValue("1201")).toBeVisible();
  navigation.search = "ws=network&tab=rebalance&view=second";
  view.rerender(element());
  expect(await screen.findByDisplayValue("1202")).toBeVisible();
  navigation.search = "ws=network&tab=rebalance&view=first";
  view.rerender(element());
  expect(await screen.findByDisplayValue("1203")).toBeVisible();
  await waitFor(() => expect(builderReads).toBe(3));
  expect(fetcher.mock.calls.every(([, init]) => init?.method === undefined || init.method === "GET")).toBe(true);
});
