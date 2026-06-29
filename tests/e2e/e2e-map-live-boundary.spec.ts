import { expect, test } from "@playwright/test";

const liveBoundaryQuery = new URLSearchParams({
  selected: "hz-1049",
  drawer: "zone",
  mapTileUrl: "mock://tiles/{z}/{x}/{y}.png",
  geocoderUrl: "mock://geocoder/search",
  mapAttribution: "Approved Mock Tiles",
  mapTermsUrl: "https://example.test/map-terms",
});

test("ODP-MAP-E2E-001 displays live tile/geocoder boundary config, attribution, and terms", async ({ page }) => {
  await page.goto(`/w/expansion/heatzone?${liveBoundaryQuery.toString()}`);

  await expect(page.getByTestId("map-boundary-config")).toContainText("Tiles: configured");
  await expect(page.getByTestId("map-boundary-config")).toContainText("Geocoder: configured");
  await expect(page.getByTestId("map-boundary-config")).toContainText("Approved Mock Tiles");
  await expect(page.getByRole("link", { name: "Terms" })).toHaveAttribute("href", "https://example.test/map-terms");
  await expect(page.getByTestId("heat-zone-map-status")).toContainText("live tile endpoint configured");
  await expect(page.getByTestId("heatzone-row-hz-1049")).toHaveAttribute("aria-current", "true");
});

test("ODP-MAP-E2E-001 tile outage keeps ranking and detail fallback usable", async ({ page }) => {
  const query = new URLSearchParams(liveBoundaryQuery);
  query.set("mapFault", "tile");
  query.set("mapCorrelationId", "corr-map-tile-001");

  await page.goto(`/w/expansion/heatzone?${query.toString()}`);

  await expect(page.getByTestId("map-boundary-alert")).toContainText("Tile outage");
  await expect(page.getByTestId("map-boundary-alert")).toContainText("corr-map-tile-001");
  await expect(page.getByTestId("map-boundary-alert")).toContainText("List and ranking fallback remain available.");
  await page.getByTestId("heatzone-row-hz-0773").click();
  await expect(page.getByTestId("heatzone-drawer")).toContainText("hz-0773");
  await expect(page.getByTestId("heatzone-drawer")).toContainText("SUPPRESSED_LOW_CONFIDENCE");
});

test("ODP-MAP-E2E-001 geocoder outage keeps list workflow usable", async ({ page }) => {
  const query = new URLSearchParams(liveBoundaryQuery);
  query.set("geocoderFault", "1");
  query.set("mapCorrelationId", "corr-map-geocoder-001");

  await page.goto(`/w/expansion/heatzone?${query.toString()}`);

  await expect(page.getByTestId("map-boundary-alert")).toContainText("Geocoder outage");
  await expect(page.getByTestId("map-boundary-alert")).toContainText("corr-map-geocoder-001");
  await page.getByTestId("exp-nav-listings").click();
  await expect(page.getByTestId("listing-drawer")).toContainText("lst-9001");
  await expect(page.getByTestId("listing-drawer")).toContainText("GEOCODED");
});
