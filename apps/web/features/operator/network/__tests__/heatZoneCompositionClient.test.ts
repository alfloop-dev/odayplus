import { afterEach, describe, expect, it, vi } from "vitest";
import { buildHeatZoneCompositionClient } from "../heatZoneCompositionClient";

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

  it("propagates a transport failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network unavailable")));
    await expect(buildHeatZoneCompositionClient().fetchProposals()).rejects.toThrow("network unavailable");
  });
});
