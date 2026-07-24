import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GrowthWorkspace } from "../GrowthWorkspace";
import {
  FIXTURE_FRESHNESS,
  GROWTH_ITEMS,
  PRICEOPS_RECOMMENDATIONS,
  SEGMENTS,
} from "../growthViewModel";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

describe("GrowthWorkspace API loading", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("mounts the existing Growth API loader in production", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const liveFreshness = {
      ...FIXTURE_FRESHNESS,
      modelVersion: "growth-production-v2",
      sourceSnapshotId: "live-growth-snapshot-24",
    };
    const liveSegments = [{
      ...SEGMENTS[0],
      id: "live-segment-1",
      name: "Live production segment",
    }];
    const liveRecommendations = [{
      ...PRICEOPS_RECOMMENDATIONS[0],
      id: "live-recommendation-1",
      segmentId: "live-segment-1",
      title: "Live production recommendation",
    }];
    const liveItems = [{
      ...GROWTH_ITEMS[0],
      id: "live-growth-action-1",
      segmentId: "live-segment-1",
      sourceRecommendationId: "live-recommendation-1",
      name: "Live production action",
    }];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.endsWith("/freshness")
        ? liveFreshness
        : url.endsWith("/segments")
          ? { items: liveSegments }
          : url.includes("/recommendations")
            ? { items: liveRecommendations }
            : { items: liveItems };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<GrowthWorkspace basePath="/operator" searchParams={{}} />);

    expect(await screen.findByTestId("growth-workspace")).toBeInTheDocument();
    const requestedUrls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(requestedUrls).toEqual(expect.arrayContaining([
      "/api/v1/operator/growth/freshness",
      "/api/v1/operator/growth/segments",
      "/api/v1/operator/growth/recommendations",
      "/api/v1/operator/growth/actions",
    ]));
    expect(screen.getByTestId("growth-data-status")).not.toHaveTextContent("fixture");
    expect(screen.getAllByText("Live production action").length).toBeGreaterThan(0);
    expect(screen.queryByText(GROWTH_ITEMS[0].name)).not.toBeInTheDocument();
  });

  it("fails closed when a Growth read is unavailable", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<GrowthWorkspace basePath="/operator" searchParams={{}} />);

    await waitFor(() =>
      expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"),
    );
    expect(screen.queryByTestId("growth-workspace")).not.toBeInTheDocument();
  });

  it("blocks seed and partial Growth API responses in production", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.endsWith("/freshness")
        ? { ...FIXTURE_FRESHNESS, source: "fixture-growth" }
        : { items: [] };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }));

    render(<GrowthWorkspace basePath="/operator" searchParams={{}} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    expect(gate).toHaveAttribute("data-status", "seed");
    expect(screen.queryByTestId("growth-workspace")).not.toBeInTheDocument();
    expect(screen.queryByText(GROWTH_ITEMS[0].name)).not.toBeInTheDocument();
  });

  it("retains the embedded fixture workspace in local mode", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<GrowthWorkspace basePath="/operator" searchParams={{}} />);

    expect(await screen.findByTestId("growth-workspace")).toHaveAttribute(
      "data-source",
      "fixture",
    );
    expect(screen.getAllByText(GROWTH_ITEMS[0].name).length).toBeGreaterThan(0);
  });
});
