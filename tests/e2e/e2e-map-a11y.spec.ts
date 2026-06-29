import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("ODP-MAP-A11Y-001 supports keyboard HeatZone selection, layer controls, and drawer close focus return", async ({ page }) => {
  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone");

  await expect(page.getByTestId("heatzone-drawer")).toContainText("hz-1049");

  const lowConfidenceRow = page.getByTestId("heatzone-row-hz-0773");
  await lowConfidenceRow.focus();
  await expect(lowConfidenceRow).toBeFocused();
  await page.keyboard.press("Enter");

  await expect(page).toHaveURL(/selected=hz-0773/);
  await expect(page.getByTestId("heatzone-drawer")).toContainText("SUPPRESSED_LOW_CONFIDENCE");
  await expect(page.getByTestId("heatzone-row-hz-0773")).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("heat-zone-map-status")).toContainText("layers h3,listings,candidates,confidence,freshness,risk");

  const listingsToggle = page.getByTestId("map-layer-keyboard-listings");
  await listingsToggle.focus();
  await expect(listingsToggle).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/layers=h3%2Ccandidates%2Cconfidence%2Cfreshness%2Crisk/);
  await page.reload();
  await expect(page.getByTestId("heat-zone-map-status")).toContainText("layers h3,candidates,confidence,freshness,risk");

  await lowConfidenceRow.focus();
  await page.keyboard.press("Escape");

  await expect(page.getByTestId("heatzone-drawer")).toHaveCount(0);
  await expect(page).not.toHaveURL(/drawer=zone/);
  await expect(lowConfidenceRow).toBeFocused();
});

test("ODP-MAP-A11Y-001 passes axe scan on HeatZone map route", async ({ page }) => {
  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone");

  await expect(page.getByTestId("heat-zone-map")).toBeVisible();
  const results = await new AxeBuilder({ page })
    .include('[data-testid="exp-heatzone-page"]')
    .analyze();

  expect(results.violations).toEqual([]);
});
