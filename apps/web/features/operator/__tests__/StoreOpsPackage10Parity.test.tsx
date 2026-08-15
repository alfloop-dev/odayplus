import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DesignStoreOpsWorkspace } from "../DesignAlignedWorkspaces";

describe("Package 10 Store Ops parity", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise(() => {})));
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    window.sessionStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("renders the full-store four-light summary and dense three-part workspace", () => {
    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);

    expect(document.querySelector('[data-screen-label="Store Ops 門市營運"]')).toBeInTheDocument();
    expect(document.querySelector('[data-screen-label="Store Ops 全店四燈摘要"]')).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /紅燈 2 需立即處置/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /黃燈 1 持續觀察/ })).toBeInTheDocument();
    expect(screen.getByLabelText("門市 Issue queue")).toBeInTheDocument();
    expect(screen.getByLabelText("ISS-1024 detail")).toBeInTheDocument();
    expect(screen.getByLabelText("Action rail")).toBeInTheDocument();
    expect(screen.getByText("TREND · API SNAPSHOT")).toBeInTheDocument();
  });

  it("applies and clears Package 10 quick filters against the Issue queue", async () => {
    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "設備風險" }));

    await waitFor(() => {
      expect(screen.getAllByText("冷氣遠端重啟等待核准").length).toBeGreaterThan(0);
      expect(screen.queryByText("補班日人力不足觀察中")).not.toBeInTheDocument();
    });
    expect(screen.getByText(/已套用「設備風險」/)).toBeInTheDocument();
    expect(screen.getByText("目前篩選：設備風險")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "清除" })[0]);

    await waitFor(() => {
      expect(screen.getAllByText("補班日人力不足觀察中").length).toBeGreaterThan(0);
      expect(screen.queryByText("目前篩選：設備風險")).not.toBeInTheDocument();
    });
  });

  it("filters lifecycle groups and exposes evidence detail tabs", async () => {
    render(<DesignStoreOpsWorkspace onOpenWorkflow={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /觀察／成效 1/ }));
    await waitFor(() => {
      expect(screen.getAllByText("補班日人力不足觀察中").length).toBeGreaterThan(0);
      expect(screen.queryByText("冷氣遠端重啟等待核准")).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /全部 3/ }));
    fireEvent.click(screen.getByRole("button", { name: /晚間負評與清潔分數同步惡化/ }));
    fireEvent.click(screen.getByRole("tab", { name: "ForecastOps" }));

    expect(screen.getByRole("tab", { name: "ForecastOps" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/28 天門市營運營收預測與異常帶/)).toBeInTheDocument();
  });
});
