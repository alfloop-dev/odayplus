import { expect, test } from "@playwright/test";

function resilienceUrl(state: string, correlationId = `corr-map-${state}-001`) {
  const query = new URLSearchParams({
    selected: "hz-1049",
    drawer: "zone",
    mapState: state,
    mapCorrelationId: correlationId,
  });
  return `/w/expansion/heatzone?${query.toString()}`;
}

test("ODP-MAP-E2E-005 exposes loading state while ranking fallback is usable", async ({ page }) => {
  await page.goto(resilienceUrl("loading"));

  await expect(page.getByTestId("map-state-panel")).toContainText("Map loading");
  await expect(page.getByTestId("map-state-panel")).toContainText("list fallback is already usable");
  await page.getByTestId("heatzone-row-hz-0881").click();
  await expect(page.getByTestId("heatzone-drawer")).toContainText("hz-0881");
});

test("ODP-MAP-E2E-005 exposes empty and no-geometry states without blocking detail drawers", async ({ page }) => {
  await page.goto(resilienceUrl("empty"));

  await expect(page.getByTestId("map-state-panel")).toContainText("No map data");
  await expect(page.getByTestId("map-state-panel")).toContainText("No map layer records matched");
  await page.getByTestId("heatzone-row-hz-0773").click();
  await expect(page.getByTestId("heatzone-drawer")).toContainText("SUPPRESSED_LOW_CONFIDENCE");

  await page.goto(resilienceUrl("no-geometry", "corr-map-no-geometry-001"));
  await expect(page.getByTestId("map-state-panel")).toContainText("No geometry fallback");
  await expect(page.getByTestId("map-state-panel")).toContainText("corr-map-no-geometry-001");
  await expect(page.getByTestId("heatzone-row-hz-1049")).toHaveAttribute("aria-current", "true");
});

test("ODP-MAP-E2E-005 exposes map error correlation id and preserves list workflow", async ({ page }) => {
  await page.goto(resilienceUrl("error", "corr-map-error-001"));

  await expect(page.getByTestId("map-state-panel")).toContainText("Map error");
  await expect(page.getByTestId("map-state-panel")).toContainText("corr-map-error-001");
  await page.goto("/w/expansion/listings");
  await expect(page.getByTestId("listing-drawer")).toContainText("lst-9001");
});

test("ODP-MAP-E2E-005 exposes partial layer failure and keeps candidate detail usable", async ({ page }) => {
  await page.goto(resilienceUrl("partial", "corr-map-partial-001"));

  await expect(page.getByTestId("map-state-panel")).toContainText("Partial map layer failure");
  await expect(page.getByTestId("map-state-panel")).toContainText("Listings layer failed");
  await expect(page.getByTestId("map-state-panel")).toContainText("corr-map-partial-001");
  await page.goto("/w/expansion/candidates?selected=cs-4107&drawer=candidate");
  await expect(page.getByTestId("candidate-drawer")).toContainText("cs-4107");
});
