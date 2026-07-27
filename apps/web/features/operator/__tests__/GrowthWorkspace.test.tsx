import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

  it("never labels live production model output as mock", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    render(
      <GrowthWorkspace
        apiData={{
          availability: "ready",
          freshness: {
            ...FIXTURE_FRESHNESS,
            modelVersion: "growth-production-v2",
            sourceSnapshotId: "live-growth-snapshot-24",
          },
          fromApi: true,
          items: [{
            ...GROWTH_ITEMS[0],
            id: "live-growth-action-1",
            segmentId: "live-segment-1",
          }],
          recommendations: [{
            ...PRICEOPS_RECOMMENDATIONS[0],
            id: "live-recommendation-1",
            segmentId: "live-segment-1",
          }],
          segments: [{
            ...SEGMENTS[0],
            id: "live-segment-1",
            name: "Live production segment",
          }],
        }}
        basePath="/operator"
        searchParams={{ gtab: "segments" }}
      />,
    );

    expect(screen.getByText(/分群與模型版本來自 live API/)).toBeInTheDocument();
    expect(screen.queryByText(/mock/i)).not.toBeInTheDocument();
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

  it("renders the Package 10 Growth composition and lifecycle labels", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");

    render(
      <GrowthWorkspace
        apiData={{
          availability: "fixture",
          freshness: FIXTURE_FRESHNESS,
          fromApi: false,
          items: GROWTH_ITEMS,
          recommendations: PRICEOPS_RECOMMENDATIONS,
          segments: SEGMENTS,
        }}
        basePath="/operator"
        searchParams={{}}
      />,
    );

    const entryCards = screen.getByTestId("growth-entry-cards");
    expect(entryCards).toHaveAttribute("data-screen-label", "Growth 建立入口");
    expect(within(entryCards).getAllByRole("link")).toHaveLength(3);
    expect(screen.getByRole("link", { name: "活動與機會" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "會員分群" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "PriceOps 定價" })).toBeInTheDocument();

    const stepper = screen.getByTestId("growth-lifecycle-stepper");
    for (const label of ["機會", "草稿", "核准", "排程", "執行", "觀察", "成效", "結案"]) {
      expect(within(stepper).getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("優惠券")).toBeInTheDocument();
    expect(screen.getAllByText("AdLift").length).toBeGreaterThan(0);
  });

  it("renders the Package 10 PriceOps nine-column workbench", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");

    render(
      <GrowthWorkspace
        apiData={{
          availability: "fixture",
          freshness: FIXTURE_FRESHNESS,
          fromApi: false,
          items: GROWTH_ITEMS,
          recommendations: PRICEOPS_RECOMMENDATIONS,
          segments: SEGMENTS,
        }}
        basePath="/operator"
        searchParams={{ gtab: "priceops" }}
      />,
    );

    const table = screen.getByTestId("growth-recommendation-table");
    for (const heading of [
      "門市／分群",
      "時窗",
      "目前價",
      "建議價",
      "預期利用率",
      "預期營收",
      "毛利風險",
      "回滾條件",
    ]) {
      expect(within(table).getByRole("columnheader", { name: heading })).toBeInTheDocument();
    }
    expect(within(table).getAllByText("建立定價草稿")).toHaveLength(3);
  });

  it("keeps Growth approval decisions in Govern", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const pending = {
      ...GROWTH_ITEMS[4],
      status: "PENDING_APPROVAL" as const,
      approvalId: "APR-601",
    };

    render(
      <GrowthWorkspace
        apiData={{
          availability: "fixture",
          freshness: FIXTURE_FRESHNESS,
          fromApi: false,
          items: [pending],
          recommendations: PRICEOPS_RECOMMENDATIONS,
          segments: SEGMENTS,
        }}
        basePath="/operator"
        searchParams={{ item: pending.id }}
      />,
    );

    const panel = screen.getByTestId("growth-approval-panel");
    expect(within(panel).getByText("APR-601")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "查看 →" })).toHaveAttribute(
      "href",
      "/operator?ws=govern",
    );
    expect(screen.queryByTestId("growth-approve")).not.toBeInTheDocument();
    expect(screen.queryByTestId("growth-reject")).not.toBeInTheDocument();
  });

  it("advances lifecycle only after the transition API succeeds", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const approved = {
      ...GROWTH_ITEMS[4],
      status: "APPROVED" as const,
    };
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({
        id: approved.id,
        status: "SCHEDULED",
        correlation_id: "corr-transition-1",
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <GrowthWorkspace
        apiData={{
          availability: "fixture",
          freshness: FIXTURE_FRESHNESS,
          fromApi: false,
          items: [approved],
          recommendations: PRICEOPS_RECOMMENDATIONS,
          segments: SEGMENTS,
        }}
        basePath="/operator"
        searchParams={{ item: approved.id }}
      />,
    );

    fireEvent.click(screen.getByTestId("growth-transition-action"));

    await waitFor(() =>
      expect(screen.getByTestId("growth-item-detail")).toHaveTextContent("已排程"),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/operator/growth/actions/${approved.id}/transition`,
      expect.objectContaining({ method: "POST" }),
    );
  });
});
