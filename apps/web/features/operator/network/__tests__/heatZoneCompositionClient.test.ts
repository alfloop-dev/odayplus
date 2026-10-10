import { afterEach, describe, expect, it, vi } from "vitest";
import { buildHeatZoneCompositionClient } from "../heatZoneCompositionClient";

const valid = {
  proposal_id: "p1", zone_id: "z1", tenant_id: "tenant-a", composition_kind: "MERGED", status: "PROPOSED",
  member_cell_ids: ["a", "b"], member_count: 2, ndcg_gain: 0, cannibalization_variance_reduction: 0,
  correlation_rho: 0.8, disconnect_index: 0, confidence: 0.8, model_version: "m1", policy_version_id: "v1",
  created_at: "2026-10-09T00:00:00Z", reasons: [], warnings: [],
};

describe("Spatial proposal list availability", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([403, 500])("rejects HTTP %s instead of returning empty", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status })));
    await expect(buildHeatZoneCompositionClient().fetchProposals()).rejects.toThrow(`HTTP ${status}`);
  });

  it.each([{}, { items: null }, { items: {} }, null])("rejects an invalid list envelope %j", async (body) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(body)));
    await expect(buildHeatZoneCompositionClient().fetchProposals()).rejects.toThrow("回應格式不正確");
  });

  it("preserves an authoritative empty list and persona-bound no-store request", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ items: [] })));
    await expect(buildHeatZoneCompositionClient("pm-audit").fetchProposals("PROPOSED")).resolves.toEqual([]);
    expect(fetch).toHaveBeenCalledWith("/api/v1/heatzones/merge-split/proposals?status=PROPOSED", expect.objectContaining({
      cache: "no-store", headers: expect.objectContaining({ "X-Operator-Role": "pm-audit" }),
    }));
  });

  it.each([null, {}, { ...valid, proposal_id: "" }, { ...valid, status: "SUCCESS" },
    { ...valid, ndcg_gain: "0" }, { ...valid, reasons: null }, { ...valid, warnings: [3] },
    { ...valid, member_cell_ids: null }, { ...valid, member_count: 3 },
    { ...valid, child_partitions: [null] }, { ...valid, approved_by: {} },
    { ...valid, composition_kind: "SPLIT_CHILD" },
    { ...valid, composition_kind: "SPLIT_CHILD", child_partitions: [["a"], ["a"]], child_zone_ids: ["c1", "c2"] },
  ])("rejects an unsafe proposal item %j", async (item) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ items: [item] })));
    await expect(buildHeatZoneCompositionClient().fetchProposals()).rejects.toThrow("回應格式不正確");
  });

  it("rejects duplicate decision identities", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ items: [valid, valid] })));
    await expect(buildHeatZoneCompositionClient().fetchProposals()).rejects.toThrow("回應格式不正確");
  });

  it("preserves zeros and valid complete split topology", async () => {
    const split = { ...valid, proposal_id: "p2", composition_kind: "SPLIT_CHILD", child_partitions: [["a"], ["b"]], child_zone_ids: ["c1", "c2"] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ items: [valid, split] })));
    await expect(buildHeatZoneCompositionClient().fetchProposals()).resolves.toEqual([valid, split]);
  });

  it("rejects detail and preview identities that differ from the requested proposal", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(valid)));
    await expect(buildHeatZoneCompositionClient().getProposal("p2")).rejects.toThrow("提案回應格式不正確");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ proposal: valid })));
    await expect(buildHeatZoneCompositionClient().previewProposal("p2")).rejects.toThrow("提案預覽回應格式不正確");
  });

  it("propagates a transport failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network unavailable")));
    await expect(buildHeatZoneCompositionClient().fetchProposals()).rejects.toThrow("network unavailable");
  });
});
