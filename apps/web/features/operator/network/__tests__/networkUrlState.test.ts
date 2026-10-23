import { describe, expect, it } from "vitest";
import {
  buildNetworkTabHref,
  parseNetworkTabIndex,
  serializeNetworkTab,
} from "../networkUrlState";

describe("Network URL state", () => {
  it("cold-opens Listing Radar from its canonical tab slug", () => {
    expect(
      parseNetworkTabIndex("ws=network&tab=radar"),
    ).toBe(1);
  });

  it("restores every canonical tab and accepts legacy aliases", () => {
    expect(parseNetworkTabIndex("tab=areas")).toBe(0);
    expect(parseNetworkTabIndex("tab=candidates")).toBe(2);
    expect(parseNetworkTabIndex("tab=score")).toBe(3);
    expect(parseNetworkTabIndex("tab=compare")).toBe(4);
    expect(parseNetworkTabIndex("tab=review")).toBe(5);
    expect(parseNetworkTabIndex("tab=rebalance")).toBe(6);
    expect(parseNetworkTabIndex("tab=listing-radar")).toBe(1);
    expect(parseNetworkTabIndex("tab=unknown")).toBe(0);
  });

  it("changes only tab while preserving unrelated query parameters", () => {
    const existing = new URLSearchParams(
      "ws=network&tab=areas&tenant=tw&selected=IN-3011&flag=a&flag=b",
    );
    const next = serializeNetworkTab(1, existing);

    expect(next.get("ws")).toBe("network");
    expect(next.get("tab")).toBe("radar");
    expect(next.get("tenant")).toBe("tw");
    expect(next.get("selected")).toBe("IN-3011");
    expect(next.getAll("flag")).toEqual(["a", "b"]);
  });

  it("builds reloadable history entries without dropping the hash", () => {
    const existing = new URLSearchParams(
      "ws=network&tab=radar&tenant=tw",
    );

    expect(
      buildNetworkTabHref("/operator", 5, existing, "#intake/IN-3011"),
    ).toBe(
      "/operator?ws=network&tab=review&tenant=tw#intake/IN-3011",
    );
  });
});

describe("exact candidate URLs", () => {
  it("removes stale intake selection and conflicting zone while preserving inbox filters", async () => {
    const { candidateNavigationParams, parseCandidateSelection } = await import("../networkUrlState");
    const params = candidateNavigationParams("candidate/with space", new URLSearchParams(
      "ws=network&selected=old-intake&dialog=decide&receipt=old-receipt&section=promotion&field=rent&decision=revise&compare=true&zone=wrong&sourceId=recorded&flag=a&flag=b",
    ));
    expect(parseCandidateSelection(params)).toEqual({ id: "candidate/with space", invalid: false });
    for (const key of ["selected", "dialog", "receipt", "section", "field", "decision", "compare", "zone"]) expect(params.has(key)).toBe(false);
    expect(params.get("sourceId")).toBe("recorded");
    expect(params.getAll("flag")).toEqual(["a", "b"]);
    expect(params.toString()).toContain("candidate=candidate%2Fwith+space");
  });

  it.each(["candidate=first&candidate=second", "candidate="])("refuses ambiguous or empty exact selection: %s", async (query) => {
    const { parseCandidateSelection } = await import("../networkUrlState");
    expect(parseCandidateSelection(new URLSearchParams(query))).toEqual({ id: null, invalid: true });
  });

  it("restores the exact ID on reload without reusing the intake selected key", async () => {
    const { candidateNavigationParams, parseCandidateSelection } = await import("../networkUrlState");
    const href = buildNetworkTabHref("/operator", 2, candidateNavigationParams("recorded-candidate", new URLSearchParams("ws=network")));
    const restored = new URL(href, "https://operator.example.test");
    expect(parseCandidateSelection(restored.searchParams)).toEqual({ id: "recorded-candidate", invalid: false });
    expect(restored.searchParams.has("selected")).toBe(false);
  });
});
