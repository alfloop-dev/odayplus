import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GrowthWorkspace } from "../../../../features/operator/GrowthWorkspace";
import { FIXTURE_FRESHNESS, PRICEOPS_RECOMMENDATIONS, SEGMENTS } from "../../../../features/operator/growthViewModel";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

const data = (live: boolean) => ({
  availability: live ? "ready" as const : "fixture" as const,
  fromApi: live,
  freshness: { ...FIXTURE_FRESHNESS, modelVersion: live ? "verified-model-version" : FIXTURE_FRESHNESS.modelVersion },
  items: [], segments: SEGMENTS, recommendations: PRICEOPS_RECOMMENDATIONS,
});
const props = { basePath: "/operator", searchParams: { gtab: "priceops" } };

describe("F7 PriceOps evidence integrity", () => {
  it.each([true, false])("live API recommendations stay read-only (production=%s)", (production) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", String(production));
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    render(<GrowthWorkspace {...props} apiData={data(true)} />);
    expect(screen.getByTestId("growth-recommendation-table")).toBeInTheDocument();
    expect(screen.getByTestId("priceops-writeback-btn")).toBeDisabled();
    fireEvent.click(screen.getByTestId("priceops-writeback-btn"));
    expect(screen.getByTestId("priceops-writeback-unavailable")).toHaveTextContent("未執行");
    expect(screen.queryByTestId("priceops-scenario-workbench")).not.toBeInTheDocument();
    expect(screen.queryByTestId("priceops-baseline-band")).not.toBeInTheDocument();
    expect(screen.queryByTestId("priceops-alternative-band")).not.toBeInTheDocument();
    expect(screen.queryByTestId("priceops-audit-status")).not.toBeInTheDocument();
    expect(screen.queryByText(/brand-pricing-policy-v1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/priceops-exhaustive-ladder-v1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/決策成功寫回與審計/)).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("local simulation explicitly disclaims server writeback, model execution and durable audit", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    render(<GrowthWorkspace {...props} apiData={data(false)} />);
    const workbench = screen.getByTestId("priceops-scenario-workbench");
    expect(within(workbench).getByText(/非模型執行證據/)).toBeInTheDocument();
    expect(screen.getByTestId("priceops-baseline-band")).toBeInTheDocument();
    expect(screen.getByTestId("priceops-alternative-band")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("priceops-writeback-btn"));
    const status = screen.getByTestId("priceops-audit-status");
    expect(status).toHaveTextContent("DEMONSTRATION");
    expect(status).toHaveTextContent("未回寫伺服器、未建立持久稽核");
    expect(status).toHaveTextContent("非已驗證身分");
    expect(status).not.toHaveTextContent("決策成功寫回與審計");
  });

  it("a fixture receipt cannot survive a switch to live data", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const { rerender } = render(<GrowthWorkspace {...props} apiData={data(false)} />);
    fireEvent.click(screen.getByTestId("priceops-writeback-btn"));
    expect(screen.getByTestId("priceops-audit-status")).toBeInTheDocument();
    rerender(<GrowthWorkspace {...props} apiData={data(true)} />);
    expect(screen.queryByTestId("priceops-audit-status")).not.toBeInTheDocument();
    expect(screen.queryByTestId("priceops-scenario-workbench")).not.toBeInTheDocument();
    expect(screen.getByTestId("priceops-writeback-btn")).toBeDisabled();
  });

  it("fixture permission alone does not allow simulation over API-backed rows", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    render(<GrowthWorkspace {...props} apiData={{ ...data(true), availability: "fixture" }} />);
    expect(screen.queryByTestId("priceops-scenario-workbench")).not.toBeInTheDocument();
    expect(screen.getByTestId("priceops-writeback-btn")).toBeDisabled();
  });
});
