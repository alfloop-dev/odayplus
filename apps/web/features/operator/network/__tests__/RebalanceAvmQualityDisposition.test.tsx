/**
 * The console's half of the AVM quality disposition.
 *
 * The service re-derives a stored valuation card's quality claim from the
 * report the card names, and hands the console a named disposition alongside
 * the confidence. That is only half a fix. A card whose confidence was
 * downgraded to `low`, or withdrawn entirely, still renders under the heading
 * "AVM 估值（service output）" -- which reads as a valuation the service stands
 * behind. An operator comparing two cards sees one number differ and no reason
 * given, and `低` next to a price is a weak valuation, not an unmeasured one.
 *
 * The server-side disposition is asserted independently in
 * `tests/integration/test_operator_canonical_wiring.py`. What is asserted here
 * is that the screen says which of the three things it is showing, because a
 * downgraded card presented as ordinary service output has already misinformed
 * the operator regardless of what the payload contained.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RebalancePanel } from "../RebalancePanel";
import type { RebalanceQueueRow } from "../../networkFindAreasViewModel";

afterEach(cleanup);

function row(overrides: Partial<RebalanceQueueRow> = {}): RebalanceQueueRow {
  return {
    id: "RB-801",
    storeId: "RB-801",
    storeName: "南港三重店",
    status: "avmready",
    statusLabel: "AVM Ready",
    summary: "低效門市重配",
    tone: "watch",
    avmP10: 2_340_000,
    avmP50: 2_860_000,
    avmP90: 3_420_000,
    avmConf: "high",
    netPlanScenarios: [],
    ...overrides,
  };
}

function renderPanel(overrides: Partial<RebalanceQueueRow> = {}) {
  return render(
    <RebalancePanel
      onCompleteAvm={vi.fn()}
      onRequestAvm={vi.fn()}
      onSelectScenario={vi.fn()}
      onSolveNetPlan={vi.fn()}
      onSubmitReview={vi.fn()}
      rows={[row(overrides)]}
    />,
  );
}

describe("rebalance AVM card quality disposition", () => {
  it("presents a measured card as ordinary service output with no notice", () => {
    renderPanel({ avmQualityScoreStatus: "measured", avmQualityDisposition: undefined });

    expect(screen.getByTestId("rebalance-avm-RB-801")).toHaveTextContent(
      "AVM 估值（service output）",
    );
    expect(screen.getByTestId("rebalance-avm-confidence-RB-801")).toHaveTextContent("high");
    expect(screen.queryByTestId("rebalance-avm-quality-RB-801")).toBeNull();
  });

  /**
   * The reopen's defect, at the screen. The price is still the historical
   * record and stays on the card; what must not survive is the impression that
   * the service measured the inputs behind it.
   */
  it("names the legacy downgrade instead of presenting it as service output", () => {
    renderPanel({
      avmConf: "low",
      avmQualityScoreStatus: "legacy_unknown",
      avmQualityDisposition: "legacy_unknown_downgraded",
    });

    const card = screen.getByTestId("rebalance-avm-RB-801");
    expect(card).toHaveTextContent("歷史卡片 · 品質未量測");
    expect(card).not.toHaveTextContent("AVM 估值（service output）");
    expect(screen.getByTestId("rebalance-avm-quality-RB-801")).toHaveAttribute(
      "data-quality-disposition",
      "legacy_unknown_downgraded",
    );
    // The historical price the operator was shown is still the record.
    expect(card).toHaveTextContent("2,860,000");
  });

  it("claims nothing for a card whose report cannot be resolved", () => {
    renderPanel({
      avmConf: undefined,
      avmQualityScoreStatus: undefined,
      avmQualityDisposition: "unverifiable_report_reference",
    });

    const card = screen.getByTestId("rebalance-avm-RB-801");
    expect(card).toHaveTextContent("來源報告無法驗證");
    // Not an em dash standing in for a value the console never had: the card
    // has to say it is making no claim, because "—" reads as "not provided".
    expect(screen.getByTestId("rebalance-avm-confidence-RB-801")).toHaveTextContent("不宣稱");
    expect(screen.getByTestId("rebalance-avm-quality-RB-801")).toHaveAttribute(
      "data-quality-disposition",
      "unverifiable_report_reference",
    );
  });
});
