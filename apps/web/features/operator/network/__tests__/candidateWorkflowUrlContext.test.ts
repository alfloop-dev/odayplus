import { describe, expect, it } from "vitest";
import { buildNetworkTabHref, candidateWorkflowNavigationParams, isCandidateWorkflowTab, parseCandidateSelection } from "../networkUrlState";

describe("exact candidate read chain URL contract", () => {
  it.each([2, 3, 4, 5])("retains exact context across tab %s, reload and history query", (tab) => {
    const params = candidateWorkflowNavigationParams(tab, new URLSearchParams("ws=network&candidate=second&zone=old&flag=a&flag=b"), "second");
    const restored = new URL(buildNetworkTabHref("/operator", tab, params), "https://example.test");
    expect(parseCandidateSelection(restored.searchParams)).toEqual({ id: "second", invalid: false });
    expect(restored.searchParams.has("zone")).toBe(false);
    expect(restored.searchParams.getAll("flag")).toEqual(["a", "b"]);
    expect(isCandidateWorkflowTab(tab)).toBe(true);
  });
  it.each([0, 1, 6, 7])("clears exact context on exit to tab %s", (tab) => {
    const params = candidateWorkflowNavigationParams(tab, new URLSearchParams("ws=network&candidate=second&zone=old"), "second");
    expect(params.has("candidate")).toBe(false); expect(params.has("zone")).toBe(false);
    expect(isCandidateWorkflowTab(tab)).toBe(false);
  });
  it("makes explicit zone navigation own Candidates and clear a previous exact candidate", () => {
    const params = candidateWorkflowNavigationParams(2, new URLSearchParams("candidate=second"), "second", { zone: "requested-zone" });
    expect(params.get("zone")).toBe("requested-zone"); expect(params.has("candidate")).toBe(false);
  });
  it("replaces context from a new exact selection and clears stale intake state", () => {
    const params = candidateWorkflowNavigationParams(3, new URLSearchParams("candidate=first&selected=old&dialog=detail&receipt=old&zone=old"), "first", { candidate: "second" });
    expect(params.get("candidate")).toBe("second");
    for (const key of ["selected", "dialog", "receipt", "zone"]) expect(params.has(key)).toBe(false);
  });
  it("does not revive stale or invalid candidate query when no current context exists", () => {
    const params = candidateWorkflowNavigationParams(3, new URLSearchParams("candidate=first&candidate=second"), null);
    expect(params.has("candidate")).toBe(false);
    expect(parseCandidateSelection(new URLSearchParams("candidate=%20%20"))).toEqual({ id: null, invalid: true });
  });
});
