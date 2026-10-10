import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

const splitProposal: HeatZoneProposal = {
  ...sampleProposal,
  proposal_id: "99999999-8888-7777-6666-555555555555",
  composition_kind: "SPLIT_CHILD",
  zone_id: "MZ-fedcba9876543210",
  parent_zone_id: "MZ-fedcba9876543210",
  member_cell_ids: ["cell-a", "cell-b", "cell-c"],
  member_count: 3,
  child_partitions: [
    ["cell-a", "cell-b"],
    ["cell-c"],
  ],
  child_zone_ids: ["MZ-1111111111111111", "MZ-2222222222222222"],
  split_density_ratio: 3.2,
  reasons: ["side_labelled_absorption_density_ratio_3.20"],
};

describe("HeatZoneMergeSplitPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders empty state when no proposals exist", () => {
    render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[]} />);
    expect(screen.getByTestId("empty-proposals")).toBeInTheDocument();
  });

  it("does not label a pending list as empty or offer stale detail actions", () => {
    render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal]} isLoading onApproveProposal={vi.fn()} />);
    expect(screen.getByTestId("loading-proposals")).toHaveAttribute("role", "status");
    expect(screen.queryByTestId("empty-proposals")).not.toBeInTheDocument();
    expect(screen.queryByTestId("proposal-detail")).not.toBeInTheDocument();
    expect(screen.getByTestId("proposal-status-filter")).toBeDisabled();
    expect(screen.getByTestId("proposal-status-filter")).toHaveTextContent("尚未確認");
  });

  it("renders a failed read separately from empty and exposes retry", () => {
    const reload = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal]} apiError="HTTP 403" onReloadProposals={reload} />);
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP 403");
    expect(screen.queryByTestId("empty-proposals")).not.toBeInTheDocument();
    expect(screen.queryByTestId("proposal-detail")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新載入提案" }));
    expect(reload).toHaveBeenCalledTimes(1);
    rerender(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[]} />);
    expect(screen.getByTestId("empty-proposals")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
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

    expect(within(screen.getByTestId("proposal-detail")).getByText("MZ-0123456789abcdef")).toBeInTheDocument();
    expect(screen.getByText("+5.80%")).toBeInTheDocument();
    expect(screen.getByText("-24.0%")).toBeInTheDocument();

    const previewBtn = screen.getByTestId("btn-preview-proposal");
    fireEvent.click(previewBtn);

    await waitFor(() => {
      expect(onPreview).toHaveBeenCalledWith("11111111-2222-3333-4444-555555555555");
    });
    expect(await screen.findByTestId("preview-box")).toBeInTheDocument();
  });

  it("hides the decision controls from a role the server would refuse", () => {
    render(
      <HeatZoneMergeSplitPanel
        activeRoleId="pm-audit"
        proposals={[sampleProposal]}
        onApproveProposal={vi.fn()}
      />
    );

    expect(screen.queryByTestId("btn-open-approve")).not.toBeInTheDocument();
    expect(screen.queryByTestId("btn-open-reject")).not.toBeInTheDocument();
    expect(screen.getByTestId("composition-decision-denied")).toBeInTheDocument();
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
      // No decider is sent: the API takes the operator from the authenticated
      // principal, and a body naming one is rejected.
      expect(onApprove).toHaveBeenCalledWith("11111111-2222-3333-4444-555555555555", undefined);
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
    expect(confirmRejectBtn).toBeDisabled();
    fireEvent.click(confirmRejectBtn);
    expect(onReject).not.toHaveBeenCalled();

    const textarea = screen.getByPlaceholderText(/行政區邊界不連續/);
    fireEvent.change(textarea, { target: { value: "商圈邊界待確認" } });

    fireEvent.click(confirmRejectBtn);

    await waitFor(() => {
      expect(onReject).toHaveBeenCalledWith("11111111-2222-3333-4444-555555555555", "商圈邊界待確認");
    });
  });

  it("shows every child a split divides into, not just its member cells", () => {
    // A split is approved as one decision that retires the parent, so the
    // operator has to see where each cell ends up before approving it. The
    // member list alone says which cells are involved, not the topology.
    render(
      <HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[splitProposal]} />
    );

    const children = screen.getByTestId("split-children");
    expect(children).toBeInTheDocument();
    expect(children).toHaveTextContent("分割後子熱區 (2)");
    expect(children).toHaveTextContent("MZ-1111111111111111");
    expect(children).toHaveTextContent("MZ-2222222222222222");
    expect(children).toHaveTextContent("cell-a");
    expect(children).toHaveTextContent("cell-c");
    expect(children).toHaveTextContent("核准一次即同時建立以上全部子熱區");
    expect(screen.getByTestId("split-density")).toHaveTextContent("實績吸收密度比3.20 倍");
  });

  it.each([
    [0, "0.00 倍"],
    [null, "資料未提供"],
    [undefined, "資料未提供"],
    [Number.NaN, "資料未提供"],
  ])("shows source split density %s without a positive fallback", (ratio, label) => {
    render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[{ ...splitProposal, split_density_ratio: ratio }]} />);
    expect(screen.getByTestId("split-density")).toHaveTextContent(label);
  });

  it.each(["APPROVED", "APPLIED", "REJECTED"] as const)("does not present %s split topology as an undecided future action", (status) => {
    render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[{ ...splitProposal, status }]} />);
    const children = screen.getByTestId("split-children");
    expect(children).not.toHaveTextContent("核准一次即同時建立");
    expect(children).toHaveTextContent(status === "REJECTED" ? "本次決策不建立子熱區" : "現況以拓撲讀回為準");
    expect(screen.queryByTestId("btn-open-approve")).not.toBeInTheDocument();
    expect(screen.queryByTestId("btn-open-reject")).not.toBeInTheDocument();
  });

  it("keeps detail inside the selected filter", () => {
    render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal, { ...splitProposal, status: "REJECTED" }]} />);
    fireEvent.click(screen.getByTestId(`proposal-item-${sampleProposal.proposal_id}`));
    fireEvent.change(screen.getByTestId("proposal-status-filter"), { target: { value: "REJECTED" } });
    expect(screen.getByTestId("proposal-detail")).toHaveTextContent(splitProposal.zone_id);
    expect(screen.queryByTestId("btn-open-approve")).not.toBeInTheDocument();
  });

  it("disables unavailable callbacks and exposes source warnings/zeros", () => {
    render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[{ ...sampleProposal, ndcg_gain: 0, warnings: ["boundary-review-required"] }]} />);
    expect(screen.getByTestId("btn-preview-proposal")).toBeDisabled();
    expect(screen.getByTestId("btn-open-approve")).toBeDisabled();
    expect(screen.getByTestId("btn-open-reject")).toBeDisabled();
    expect(screen.getByText("boundary-review-required")).toBeVisible();
    expect(screen.getByText("+0.00%")).toBeVisible();
    expect(screen.getByTestId(`proposal-item-${sampleProposal.proposal_id}`)).toHaveAttribute("aria-pressed", "true");
  });

  it("shows null preview as an error, not success", async () => {
    render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal]} onPreviewProposal={vi.fn().mockResolvedValue(null)} />);
    fireEvent.click(screen.getByTestId("btn-preview-proposal"));
    expect(await screen.findByRole("alert")).toHaveTextContent("無可用的提案預覽回應");
    expect(screen.queryByTestId("preview-box")).not.toBeInTheDocument();
  });

  it("does not attach an old preview to a replacement proposal", async () => {
    let resolvePreview!: (value: unknown) => void;
    const preview = vi.fn(() => new Promise((resolve) => { resolvePreview = resolve; }));
    const { rerender } = render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal]} onPreviewProposal={preview as any} />);
    fireEvent.click(screen.getByTestId("btn-preview-proposal"));
    expect(screen.getByTestId("proposal-status-filter")).toBeDisabled();
    rerender(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[splitProposal]} onPreviewProposal={preview as any} />);
    resolvePreview({ proposal: sampleProposal, proposed_member_cells: [], current_active_compositions: [], expected_ndcg_gain: 0 });
    await waitFor(() => expect(screen.getByTestId("btn-preview-proposal")).toBeEnabled());
    expect(screen.queryByTestId("preview-box")).not.toBeInTheDocument();
    expect(screen.getByTestId("proposal-detail")).toHaveTextContent(splitProposal.zone_id);
  });

  it("preserves rejection input on failed write and prevents selection while pending", async () => {
    const reject = vi.fn().mockRejectedValue(new Error("409 conflict"));
    render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal, splitProposal]} onRejectProposal={reject} />);
    fireEvent.click(screen.getByTestId("btn-open-reject"));
    fireEvent.change(screen.getByPlaceholderText(/行政區邊界不連續/), { target: { value: "保留理由" } });
    expect(screen.getByTestId(`proposal-item-${splitProposal.proposal_id}`)).toBeDisabled();
    fireEvent.click(screen.getByTestId("btn-confirm-reject"));
    expect(await screen.findByRole("alert")).toHaveTextContent("409 conflict");
    expect(screen.getByPlaceholderText(/行政區邊界不連續/)).toHaveValue("保留理由");
    expect(screen.getByTestId("reject-modal")).toBeVisible();
  });

  it("pins the decision to the opened proposal and does not submit a replacement", () => {
    const approve = vi.fn();
    const view = render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal]} onApproveProposal={approve} />);
    fireEvent.click(screen.getByTestId("btn-open-approve"));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "原提案備註" } });
    view.rerender(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[splitProposal]} onApproveProposal={approve} />);
    expect(screen.getByRole("dialog")).toHaveTextContent(sampleProposal.proposal_id);
    expect(screen.getByRole("textbox")).toHaveValue("原提案備註");
    expect(screen.getByTestId("btn-confirm-approve")).toBeDisabled();
    fireEvent.click(screen.getByTestId("btn-confirm-approve"));
    expect(approve).not.toHaveBeenCalled();
  });

  it.each(["APPROVED", "REJECTED"] as const)("blocks confirmation when the target becomes %s", (status) => {
    const reject = vi.fn();
    const view = render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal]} onRejectProposal={reject} />);
    fireEvent.click(screen.getByTestId("btn-open-reject"));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "原拒絕理由" } });
    view.rerender(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[{ ...sampleProposal, status }]} onRejectProposal={reject} />);
    expect(screen.getByTestId("btn-confirm-reject")).toBeDisabled();
    expect(screen.getByRole("textbox")).toHaveValue("原拒絕理由");
    fireEvent.click(screen.getByTestId("btn-confirm-reject"));
    expect(reject).not.toHaveBeenCalled();
  });

  it("closes an old persona's modal and does not acknowledge its late write", async () => {
    let resolve!: (value: { readbackConfirmed: boolean }) => void;
    const approve = vi.fn(() => new Promise<{ readbackConfirmed: boolean }>((done) => { resolve = done; }));
    const view = render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal]} onApproveProposal={approve} />);
    fireEvent.click(screen.getByTestId("btn-open-approve"));
    fireEvent.click(screen.getByTestId("btn-confirm-approve"));
    view.rerender(<HeatZoneMergeSplitPanel activeRoleId="pm-audit" proposals={[sampleProposal]} onApproveProposal={approve} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () => { resolve({ readbackConfirmed: true }); });
    expect(screen.queryByTestId("feedback-message")).not.toBeInTheDocument();
    expect(screen.queryByTestId("btn-open-approve")).not.toBeInTheDocument();
  });

  it("freezes inputs, cancel and duplicate confirmation throughout a pending decision", async () => {
    let resolve!: (value: { readbackConfirmed: boolean }) => void;
    const approve = vi.fn(() => new Promise<{ readbackConfirmed: boolean }>((done) => { resolve = done; }));
    render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal]} onApproveProposal={approve} />);
    fireEvent.click(screen.getByTestId("btn-open-approve"));
    fireEvent.click(screen.getByTestId("btn-confirm-approve"));
    fireEvent.click(screen.getByTestId("btn-confirm-approve"));
    expect(approve).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-busy", "true");
    await act(async () => { resolve({ readbackConfirmed: true }); });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByTestId("feedback-message")).toHaveTextContent("最新提案狀態已讀回確認");
  });

  it("separates an acknowledged POST from unknown readback and prevents repeat decisions", async () => {
    const approve = vi.fn().mockResolvedValue({ readbackConfirmed: false });
    const reload = vi.fn().mockResolvedValue(undefined);
    render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal]} onApproveProposal={approve} onReloadProposals={reload} />);
    fireEvent.click(screen.getByTestId("btn-open-approve"));
    fireEvent.click(screen.getByTestId("btn-confirm-approve"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByTestId("feedback-message")).toHaveTextContent("請重新載入提案，勿重複提交");
    expect(screen.getByTestId("btn-open-approve")).toBeDisabled();
    expect(screen.getByTestId("btn-open-reject")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重新載入提案" }));
    expect(reload).toHaveBeenCalledTimes(1);
    expect(approve).toHaveBeenCalledTimes(1);
  });

  it("blocks an open confirmation while the list authority is unavailable", () => {
    const approve = vi.fn();
    const view = render(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal]} onApproveProposal={approve} />);
    fireEvent.click(screen.getByTestId("btn-open-approve"));
    view.rerender(<HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal]} isLoading onApproveProposal={approve} />);
    expect(screen.getByTestId("btn-confirm-approve")).toBeDisabled();
    fireEvent.click(screen.getByTestId("btn-confirm-approve"));
    expect(approve).not.toHaveBeenCalled();
  });

  it("does not offer a child breakdown for a merge", () => {
    render(
      <HeatZoneMergeSplitPanel activeRoleId="expansion-manager" proposals={[sampleProposal]} />
    );
    expect(screen.queryByTestId("split-children")).not.toBeInTheDocument();
  });
});
