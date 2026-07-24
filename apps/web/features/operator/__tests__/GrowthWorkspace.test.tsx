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
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.endsWith("/freshness")
        ? FIXTURE_FRESHNESS
        : url.endsWith("/segments")
          ? { items: SEGMENTS }
          : url.includes("/recommendations")
            ? { items: PRICEOPS_RECOMMENDATIONS }
            : { items: GROWTH_ITEMS };
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
});
