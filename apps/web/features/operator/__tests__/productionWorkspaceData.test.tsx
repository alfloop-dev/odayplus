import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DesignStoreOpsWorkspace,
  DesignTodayWorkspace,
  inspectStoreOpsApiPayload,
} from "../DesignAlignedWorkspaces";
import {
  inspectNetworkListingsSnapshot,
  inspectNetworkRebalanceSnapshot,
  inspectNetworkReviewsSnapshot,
  inspectNetworkScoringSnapshot,
  resolveNetworkCountState,
  resolveNetworkFindAreasLoadState,
  resolveNetworkTabGateState,
} from "../NetworkFindAreasWorkspace";
import { resolveGovernanceDecisionAuthority } from "../GovernanceWorkspace";
import { normalizeGovernanceActionAuthority } from "../governance/governanceLoader";
import {
  operatorLoadFailureFromError,
  operatorLoadFailureFromResponse,
} from "../operatorDataMode";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("production workspace data contracts", () => {
  it("keeps the legacy Today fixture surface unavailable in production", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");

    render(<DesignTodayWorkspace onQueueSelect={vi.fn()} />);

    expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute(
      "data-status",
      "empty",
    );
    expect(screen.queryByText(/林承翰/)).not.toBeInTheDocument();
    expect(screen.queryByText("Kiosk 離線＋遠端重啟失敗")).not.toBeInTheDocument();
  });

  it("retains the legacy Today fixtures for local POC mode", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");

    render(<DesignTodayWorkspace onQueueSelect={vi.fn()} />);

    expect(screen.getByText(/林承翰/)).toBeInTheDocument();
    expect(screen.getByText("Kiosk 離線＋遠端重啟失敗")).toBeInTheDocument();
  });

  it("blocks an incomplete Store Ops response without displaying an issue fixture", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        source: "operator-store-ops-production",
        issues: [],
        stores: [],
        evidence: [],
        auditEvents: [],
        fourLightSummary: [],
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    render(
      <DesignStoreOpsWorkspace
        onOpenWorkflow={vi.fn()}
      />,
    );

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-status", "empty"));
    expect(screen.queryByText("付款機前卡住＋付款失敗＋Google 負評")).not.toBeInTheDocument();
  });

  it("classifies Store Ops seed, empty, and usable API payloads", () => {
    const livePayload = {
      source: "operator-store-ops-production",
      issues: [{ id: "LIVE-ISSUE-1" }],
      stores: [{ id: "LIVE-STORE-1" }],
      evidence: [],
      auditEvents: [],
    };
    expect(inspectStoreOpsApiPayload(livePayload)).toBe("ready");
    expect(inspectStoreOpsApiPayload({ ...livePayload, source: undefined })).toBe("ready");
    expect(inspectStoreOpsApiPayload({ ...livePayload, source: "fixture-store-ops" })).toBe("seed");
    expect(inspectStoreOpsApiPayload({ ...livePayload, issues: [] })).toBe("empty");
  });

  it("requires non-seed, non-empty Network and Listing API snapshots", () => {
    const listingSnapshot = {
      source: "api" as const,
      heatZones: [{ id: "LIVE-HZ-1" }],
      listingSources: [{ id: "LIVE-SOURCE-1" }],
      listings: [{ id: "LIVE-LISTING-1" }],
      candidates: [],
      siteReviews: [],
    } as any;
    const scoringSnapshot = {
      source: "api" as const,
      modelVersion: "sitescore-production-v1",
      candidates: [{ id: "LIVE-CANDIDATE-1" }],
      scorecards: [{ candidateId: "LIVE-CANDIDATE-1" }],
      batchResults: [],
      compare: { columns: [{ id: "LIVE-CANDIDATE-1" }], metrics: [], recommendation: null, empty: false },
      compareSet: [],
    } as any;
    const rebalanceSnapshot = {
      source: "api" as const,
      stores: [{ id: "LIVE-STORE-1" }],
    } as any;
    const reviewsSnapshot = {
      source: "api" as const,
      reviews: [{ id: "LIVE-REVIEW-1" }],
    } as any;

    expect(inspectNetworkListingsSnapshot(listingSnapshot)).toBe("ready");
    expect(inspectNetworkScoringSnapshot(scoringSnapshot)).toBe("ready");
    expect(inspectNetworkRebalanceSnapshot(rebalanceSnapshot)).toBe("ready");
    expect(inspectNetworkReviewsSnapshot(reviewsSnapshot)).toBe("ready");

    expect(inspectNetworkListingsSnapshot({ ...listingSnapshot, source: "fixture" })).toBe("seed");
    expect(
      inspectNetworkListingsSnapshot({ ...listingSnapshot, heatZones: [], listings: [], candidates: [] }),
    ).toBe("empty");
    // Withheld zone aggregates do not void authorized listing rows.
    expect(inspectNetworkListingsSnapshot({ ...listingSnapshot, heatZones: [] })).toBe("ready");
    // Malformed collections and seed payloads remain refused.
    expect(inspectNetworkListingsSnapshot({ ...listingSnapshot, heatZones: undefined })).toBe("empty");
    expect(inspectNetworkListingsSnapshot({ ...listingSnapshot, source: undefined })).toBe("empty");
    expect(inspectNetworkScoringSnapshot({ ...scoringSnapshot, scorecards: [] })).toBe("empty");
    expect(inspectNetworkRebalanceSnapshot({ ...rebalanceSnapshot, stores: [] })).toBe("empty");
    expect(inspectNetworkReviewsSnapshot({ ...reviewsSnapshot, reviews: [] })).toBe("empty");
  });

  it("does not let unrelated Network snapshots gate Listing Radar intake", () => {
    expect(resolveNetworkTabGateState({
      activeTab: 1,
      fixturesAllowed: false,
      findAreasLoadState: "error",
      scoringLoadState: "loading",
      reviewsLoadState: "empty",
      rebalanceLoadState: "error",
    })).toBeNull();
  });

  it("keeps unavailable data scoped to the tab that owns it", () => {
    const states = {
      fixturesAllowed: false,
      findAreasLoadState: "ready" as const,
      scoringLoadState: "error" as const,
      reviewsLoadState: "loading" as const,
      rebalanceLoadState: "empty" as const,
    };

    expect(resolveNetworkTabGateState({ activeTab: 0, ...states })).toBeNull();
    expect(resolveNetworkTabGateState({ activeTab: 2, ...states })).toBe("error");
    expect(resolveNetworkTabGateState({ activeTab: 5, ...states })).toBe("loading");
    expect(resolveNetworkTabGateState({ activeTab: 6, ...states })).toBe("empty");
  });

  it("gates Find Areas only on the scoped operator snapshot it renders", () => {
    const states = {
      fixturesAllowed: false,
      scoringLoadState: "error" as const,
      reviewsLoadState: "error" as const,
      rebalanceLoadState: "error" as const,
    };
    expect(resolveNetworkTabGateState({ activeTab: 0, findAreasLoadState: "ready", ...states })).toBeNull();
    expect(resolveNetworkTabGateState({ activeTab: 0, findAreasLoadState: "empty", ...states })).toBe("empty");
    expect(resolveNetworkTabGateState({ activeTab: 0, findAreasLoadState: "error", ...states })).toBe("error");
    expect(resolveNetworkTabGateState({ activeTab: 0, findAreasLoadState: "seed", ...states })).toBe("seed");
  });

  it("reports Find Areas empty when a ready scoped snapshot withholds HeatZones", () => {
    expect(resolveNetworkFindAreasLoadState("ready", 0)).toBe("empty");
    expect(resolveNetworkFindAreasLoadState("ready", 2)).toBe("ready");
    for (const state of ["error", "seed", "empty", "loading"] as const) {
      expect(resolveNetworkFindAreasLoadState(state, 0)).toBe(state);
    }
  });

  it("classifies a received 403 envelope as an authenticated denial with its correlation ID", async () => {
    const refused = new Response(
      JSON.stringify({
        detail: "role does not permit view on operator_network",
        error: { code: "forbidden", message: "role does not permit view on operator_network", correlation_id: "corr-real-403" },
      }),
      { status: 403, headers: { "Content-Type": "application/json", "X-Correlation-Id": "corr-header" } },
    );
    // The label deliberately contains "network": the path must not decide the kind.
    const failure = await operatorLoadFailureFromResponse(refused, "network-listings", "corr-request");
    expect(failure).toMatchObject({ correlationId: "corr-real-403", httpStatus: 403, kind: "forbidden" });
    expect(failure.technicalDetail).toContain("network-listings returned 403 forbidden");

    const bare = await operatorLoadFailureFromResponse(new Response("denied", { status: 403 }), "network-scoring", "corr-request");
    expect(bare).toMatchObject({ correlationId: "corr-request", httpStatus: 403, kind: "forbidden" });

    const storeOps = await operatorLoadFailureFromResponse(
      new Response(JSON.stringify({ detail: "STORE_OPS_LIVE_DATA_UNAVAILABLE" }), { status: 503 }),
      "store-ops",
    );
    expect(storeOps).toMatchObject({ httpStatus: 503, kind: "server" });

    expect(operatorLoadFailureFromError(new TypeError("Failed to fetch"), "network-listings").kind).toBe("network");
  });

  it("only counts collections that were actually read", () => {
    expect(resolveNetworkCountState("ready", null, false)).toEqual({ known: true });
    // An authorized empty 200 is an authoritative zero.
    expect(resolveNetworkCountState("empty", null, false)).toEqual({ known: true });
    expect(resolveNetworkCountState("error", { kind: "forbidden", httpStatus: 403 }, false)).toEqual({
      known: false,
      label: "未授權",
      reason: "unread",
    });
    expect(resolveNetworkCountState("error", { kind: "server", httpStatus: 503 }, false)).toMatchObject({
      known: false,
      label: "無法取得",
    });
    expect(resolveNetworkCountState("loading", null, false)).toMatchObject({ known: false, reason: "pending" });
    expect(resolveNetworkCountState("seed", null, false)).toMatchObject({ known: false, reason: "unread" });
    expect(resolveNetworkCountState("error", null, true)).toEqual({ known: true });
  });

  it("derives Governance decision authority from the server, never the persona", () => {
    const readAdmin = normalizeGovernanceActionAuthority({
      verified: true,
      systemRoles: ["auditor", "platform_admin"],
      decide: false,
      exportEvidence: false,
    });
    expect(resolveGovernanceDecisionAuthority({ callerAllows: true, fixturesAllowed: false, serverAuthority: readAdmin }))
      .toEqual({ canDecide: false, canExport: false, state: "denied", systemRoles: ["auditor", "platform_admin"] });

    const manager = normalizeGovernanceActionAuthority({
      verified: true,
      systemRoles: ["operations_manager"],
      decide: true,
      exportEvidence: true,
    });
    expect(resolveGovernanceDecisionAuthority({ callerAllows: true, fixturesAllowed: false, serverAuthority: manager }))
      .toMatchObject({ canDecide: true, canExport: true, state: "granted" });
    // The caller may narrow but never widen.
    expect(resolveGovernanceDecisionAuthority({ callerAllows: false, fixturesAllowed: false, serverAuthority: manager }))
      .toMatchObject({ canDecide: false, state: "denied" });

    // Missing, unverified or malformed authority confirms nothing in production.
    for (const value of [undefined, null, "yes", { decide: true }, { verified: "true", decide: true }]) {
      expect(
        resolveGovernanceDecisionAuthority({
          callerAllows: true,
          fixturesAllowed: false,
          serverAuthority: normalizeGovernanceActionAuthority(value),
        }),
      ).toMatchObject({ canDecide: false, canExport: false });
    }
    expect(normalizeGovernanceActionAuthority({ verified: true, decide: "true", exportEvidence: 1 }))
      .toEqual({ verified: true, systemRoles: [], decide: false, exportEvidence: false });
  });
});
