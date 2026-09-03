import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HeatZoneMergeSplitPanel, type HeatZoneProposal } from "../network/HeatZoneMergeSplitPanel";

const sampleProposal: HeatZoneProposal = {
  proposal_id: "11111111-2222-3333-4444-555555555555",
  zone_id: "MZ-0123456789abcdef",
  tenant_id: "tenant-a",
  composition_kind: "MERGED",
  member_cell_ids: ["cell-1", "cell-2"],
  member_count: 2,
  parent_zone_id: null,
  ndcg_gain: 0.058,
  cannibalization_variance_reduction: 0.24,
  correlation_rho: 0.88,
  disconnect_index: 0.12,
  confidence: 0.88,
  model_version: "heatzone-composition-v1",
  policy_version_id: "heatzone-merge-v1:tenant-a",
  status: "PROPOSED",
  reasons: ["adjacent_high_demand_correlation", "continuous_spatial_absorption"],
  warnings: [],
  created_at: "2026-09-03T12:00:00Z",
};

describe("HeatZoneMergeSplitPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders empty state when no proposals exist", () => {
    render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[]} />);
    expect(screen.getByTestId("empty-proposals")).toBeInTheDocument();
  });

  it("renders proposal details, metrics, and handles preview action", async () => {
    const onPreview = vi.fn().mockResolvedValue({
      proposal: sampleProposal,
      current_active_compositions: [],
      proposed_zone_id: sampleProposal.zone_id,
      proposed_kind: "MERGED",
      proposed_member_cells: ["cell-1", "cell-2"],
      expected_ndcg_gain: 0.058,
      expected_cannibalization_variance_reduction: 0.24,
      correlation_rho: 0.88,
      disconnect_index: 0.12,
      confidence: 0.88,
    });

    render(
      <HeatZoneMergeSplitPanel
        activeRoleId="expansion-manager"
        proposals={[sampleProposal]}
        onPreviewProposal={onPreview}
      />
    );

    expect(screen.getByText("MZ-0123456789abcdef")).toBeInTheDocument();
    expect(screen.getByText("+5.80%")).toBeInTheDocument();
    expect(screen.getByText("-24.0%")).toBeInTheDocument();

    const previewBtn = screen.getByTestId("btn-preview-proposal");
    fireEvent.click(previewBtn);

    await waitFor(() => {
      expect(onPreview).toHaveBeenCalledWith("11111111-2222-3333-4444-555555555555");
    });
    expect(await screen.findByTestId("preview-box")).toBeInTheDocument();
  });

  it("handles operator approve flow", async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);

    render(
      <HeatZoneMergeSplitPanel
        activeRoleId="expansion-manager"
        proposals={[sampleProposal]}
        onApproveProposal={onApprove}
      />
    );

    const openApproveBtn = screen.getByTestId("btn-open-approve");
    fireEvent.click(openApproveBtn);

    expect(screen.getByTestId("approve-modal")).toBeInTheDocument();

    const confirmApproveBtn = screen.getByTestId("btn-confirm-approve");
    fireEvent.click(confirmApproveBtn);

    await waitFor(() => {
      expect(onApprove).toHaveBeenCalledWith("11111111-2222-3333-4444-555555555555", "expansion-manager", undefined);
    });
  });

  it("handles operator reject flow with reason requirement", async () => {
    const onReject = vi.fn().mockResolvedValue(undefined);

    render(
      <HeatZoneMergeSplitPanel
        activeRoleId="expansion-manager"
        proposals={[sampleProposal]}
        onRejectProposal={onReject}
      />
    );

    const openRejectBtn = screen.getByTestId("btn-open-reject");
    fireEvent.click(openRejectBtn);

    expect(screen.getByTestId("reject-modal")).toBeInTheDocument();

    const confirmRejectBtn = screen.getByTestId("btn-confirm-reject");
    fireEvent.click(confirmRejectBtn);
    expect(screen.getByText("請輸入拒絕理由")).toBeInTheDocument();

    const textarea = screen.getByPlaceholderText(/行政區邊界不連續/);
    fireEvent.change(textarea, { target: { value: "商圈邊界待確認" } });

    fireEvent.click(confirmRejectBtn);

    await waitFor(() => {
      expect(onReject).toHaveBeenCalledWith("11111111-2222-3333-4444-555555555555", "expansion-manager", "商圈邊界待確認");
    });
  });
});
