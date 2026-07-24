import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
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
} from "../NetworkFindAreasWorkspace";

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
    expect(gate).toHaveAttribute("data-status", "empty");
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
    expect(inspectNetworkListingsSnapshot({ ...listingSnapshot, listings: [] })).toBe("empty");
    expect(inspectNetworkScoringSnapshot({ ...scoringSnapshot, scorecards: [] })).toBe("empty");
    expect(inspectNetworkRebalanceSnapshot({ ...rebalanceSnapshot, stores: [] })).toBe("empty");
    expect(inspectNetworkReviewsSnapshot({ ...reviewsSnapshot, reviews: [] })).toBe("empty");
  });
});
