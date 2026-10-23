import { describe, expect, it } from "vitest";
import { heatZoneTaskHref, parseHeatZoneSelection } from "../heatZoneNavigation";

describe("exact HeatZone Task Center navigation contract", () => {
  it.each(["zone-a", "HZ/台北?A&B#%", " opaque zone "])("roundtrips the complete owner identity %s", (id) => {
    const href = heatZoneTaskHref({ workspace: "network", tab: "areas", entityId: id, heatZoneId: id });
    expect(href).not.toBeNull();
    const url = new URL(href!, "https://console.example");
    expect(url.pathname).toBe("/operator");
    expect(url.searchParams.get("ws")).toBe("network");
    expect(url.searchParams.get("tab")).toBe("areas");
    expect(url.searchParams.getAll("entity")).toEqual([id]);
    expect(url.searchParams.getAll("heatZone")).toEqual([id]);
    expect(parseHeatZoneSelection(url.searchParams, 0)).toEqual({ requested: true, id, invalid: false });
    expect(url.searchParams.has("candidate")).toBe(false);
    expect(url.searchParams.has("zone")).toBe(false);
  });

  it("does not create an explicit target for an ordinary Find Areas visit", () => {
    expect(parseHeatZoneSelection(new URLSearchParams("ws=network&tab=areas&lens=supply"), 0))
      .toEqual({ requested: false, id: null, invalid: false });
  });

  it.each([1, 2, 3, 4, 5, 6, 7])("does not consume the identity on Network tab %s", (tab) => {
    expect(parseHeatZoneSelection(new URLSearchParams("heatZone=zone-a&entity=zone-a&zone=candidate-zone"), tab))
      .toEqual({ requested: false, id: null, invalid: false });
  });

  it.each([
    "heatZone=zone-a", "entity=zone-a", "heatZone=&entity=zone-a", "heatZone=zone-a&entity=",
    "heatZone=%20&entity=%20", "heatZone=zone-a&entity=zone-b",
    "heatZone=zone-a&heatZone=zone-b&entity=zone-a", "heatZone=zone-a&entity=zone-a&entity=zone-b",
    "heatZone=zone-a&heatZone=&entity=zone-a", "heatZone=zone-a&entity=zone-a&entity=",
  ])("retains an explicit invalid target instead of a default zone: %s", (query) => {
    expect(parseHeatZoneSelection(new URLSearchParams(query), 0)).toEqual({ requested: true, id: null, invalid: true });
  });

  it("accepts identical duplicates under the existing Network identity convention", () => {
    expect(parseHeatZoneSelection(new URLSearchParams("heatZone=zone-a&heatZone=zone-a&entity=zone-a&entity=zone-a"), 0))
      .toEqual({ requested: true, id: "zone-a", invalid: false });
  });

  it.each([
    null, [], {}, { workspace: "network", tab: "candidates", entityId: "zone-a", heatZoneId: "zone-a" },
    { workspace: "store", tab: "areas", entityId: "zone-a", heatZoneId: "zone-a" },
    { workspace: "network", tab: "areas", entityId: "zone-a" },
    { workspace: "network", tab: "areas", entityId: "zone-a", heatZoneId: "zone-b" },
    { workspace: "network", tab: "areas", entityId: "", heatZoneId: "" },
    { workspace: "network", tab: "areas", entityId: " ", heatZoneId: " " },
    { workspace: "network", tab: "areas", entityId: 1, heatZoneId: 1 },
  ])("refuses a target that lacks exact native HeatZone identity: %j", (target) => {
    expect(heatZoneTaskHref(target)).toBeNull();
  });
});
