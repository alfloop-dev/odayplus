import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { RebalancePanel, type RebalancePanelProps } from "../RebalancePanel";
import type { RebalanceQueueRow } from "../../networkFindAreasViewModel";

afterEach(cleanup);
const row: RebalanceQueueRow = {
  id: "RB-801", storeId: "STR-801", storeName: "來源門市", status: "watching",
  statusLabel: "重配候選", summary: "來源摘要", tone: "watch",
};
function mount(overrides: Partial<RebalanceQueueRow> = {}, props: Partial<RebalancePanelProps> = {}) {
  const handlers = { onCompleteAvm: vi.fn(), onRequestAvm: vi.fn(), onSelectScenario: vi.fn(), onSolveNetPlan: vi.fn(), onSubmitReview: vi.fn() };
  render(<RebalancePanel {...handlers} rows={[{ ...row, ...overrides }]} {...props} />);
  return handlers;
}
describe("Package 10 Rebalance source semantics", () => {
  it("keeps authoritative zero metrics, AVM and trend values", () => {
    mount({ status: "avmready", monthlyRevenueLabel: "NT$0/月", utilizationLabel: "0%", avmP50: 0, avmP10: 0, avmP90: 0, trend: [50, 0], lightHistory: ["G", "R"], sourceIssueId: "ISS-SOURCE" });
    const signals = screen.getByRole("region", { name: "營運訊號" });
    expect(signals).toHaveTextContent("NT$0/月");
    expect(signals).toHaveTextContent("0%");
    const trend = screen.getByRole("img", { name: "90 天營收趨勢（來源相對值）：50、0" });
    expect(trend.lastElementChild).toHaveStyle({ height: "0%" });
    expect(screen.getByTestId("rebalance-avm-RB-801")).toHaveTextContent("$0");
    expect(screen.getByTestId("rebalance-detail-RB-801")).toHaveTextContent("ISS-SOURCE");
  });
  it("states missing trend/history/metrics without fabricating source data", () => {
    mount({ healthNote: "來源簡註" });
    expect(screen.getByTestId("rebalance-detail-RB-801")).toHaveTextContent("來源摘要");
    expect(screen.queryByRole("img", { name: /90 天營收趨勢/ })).toBeNull();
    expect(screen.getByRole("region", { name: "營運訊號" })).toHaveTextContent("尚未提供趨勢資料");
    expect(screen.getByTestId("rebalance-detail-RB-801")).not.toHaveTextContent("ISS-");
    expect(screen.queryByTestId("rebalance-avm-RB-801")).toBeNull();
  });
  it("preserves the authorized handler and suppresses duplicate pending writes", () => {
    const handlers = mount({}, { busyAction: "RB-801:request-avm" });
    const primary = screen.getByTestId("rebalance-primary-action");
    expect(primary).toBeDisabled();
    expect(primary).toHaveTextContent("處理中");
    fireEvent.click(primary);
    expect(handlers.onRequestAvm).not.toHaveBeenCalled();
  });
  it("announces API errors while retaining source records and action state", () => {
    const handlers = mount({}, { apiError: "來源寫入失敗（409）" });
    expect(screen.getByRole("alert")).toHaveTextContent("409");
    fireEvent.click(screen.getByTestId("rebalance-primary-action"));
    expect(handlers.onRequestAvm).toHaveBeenCalledWith("RB-801");
    expect(screen.getByTestId("rebalance-detail-RB-801")).toHaveTextContent("來源門市");
  });
  it("states the empty read without presenting AVM or relocation controls", () => {
    mount({}, { rows: [] });
    expect(screen.getByTestId("network-panel-rebalance")).toHaveTextContent("目前沒有重配候選門市。");
    expect(screen.queryByTestId("rebalance-primary-action")).toBeNull();
  });
  it("keeps governance gating outside the collapsed detailed plan", () => {
    mount({ status: "netplanreview", selectedScenarioId: "move", netPlanScenarios: [{ id: "move", name: "Move", roi: "0%", inv: "0", payback: "—", risk: "—", time: "—", modelledConstraintClasses: ["CAPITAL"], unmodelledConstraintClasses: [] }] });
    const disclosure = screen.getByTestId("rebalance-plan-disclosure");
    expect(disclosure).not.toHaveAttribute("open");
    expect(within(disclosure).getByText("執行計畫與完整限制揭露")).toBeInTheDocument();
    const blocker = screen.getByTestId("rebalance-blocked-alert");
    expect(disclosure.contains(blocker)).toBe(false);
    expect(blocker).toHaveTextContent("未完整申報");
    expect(screen.getByTestId("rebalance-primary-action")).toBeDisabled();
    expect(screen.getByTestId("rebalance-boundary-RB-801")).toHaveAttribute("data-relocation-executed", "false");
  });
  it("defines every referenced CSS-module key", () => {
    const source = readFileSync("features/operator/network/RebalancePanel.tsx", "utf8");
    const css = readFileSync("features/operator/networkFindAreas.module.css", "utf8");
    for (const match of source.matchAll(/styles\.([A-Za-z0-9_]+)/g)) expect(css, match[1]).toMatch(new RegExp(`\\.${match[1]}(?:[\\s:{.#\\[])`));
  });
});
