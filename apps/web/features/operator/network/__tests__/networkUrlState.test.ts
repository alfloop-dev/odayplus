import { describe, expect, it } from "vitest";
import {
  buildFindAreasHref,
  buildNetworkTabHref,
  parseFindAreasUrlState,
  parseNetworkTabIndex,
  serializeNetworkTab,
} from "../networkUrlState";

describe("Network URL state", () => {
  it("restores validated Find Areas selection and lens with fail-safe defaults", () => {
    expect(parseFindAreasUrlState(new URLSearchParams("hz=HZ-02&lens=fit"))).toEqual({ heatZoneId: "HZ-02", lens: "fit" });
    expect(parseFindAreasUrlState(new URLSearchParams("hz=%3Cscript%3E&lens=unknown"))).toEqual({ heatZoneId: null, lens: "demand" });
    expect(parseFindAreasUrlState(new URLSearchParams())).toEqual({ heatZoneId: null, lens: "demand" });
  });

  it("changes only Find Areas hints, retaining tabs, repeated query and hash", () => {
    const existing = new URLSearchParams("ws=network&tab=areas&hz=HZ-01&lens=demand&flag=a&flag=b");
    expect(buildFindAreasHref("/operator", "HZ-02", "fit", existing, "#context")).toBe(
      "/operator?ws=network&tab=areas&hz=HZ-02&lens=fit&flag=a&flag=b#context",
    );
    expect(existing.get("hz")).toBe("HZ-01");
  });

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
