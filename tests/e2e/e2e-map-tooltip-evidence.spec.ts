import { expect, test } from "@playwright/test";

test("ODP-MAP-E2E-006 exposes HeatZone evidence through hover tooltip", async ({ page }) => {
  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone");

  await page.getByTestId("map-evidence-trigger-hz-0773").hover();

  const tooltip = page.getByTestId("map-evidence-tooltip");
  await expect(tooltip).toContainText("hz-0773 evidence");
  await expect(tooltip).toContainText("Score");
  await expect(tooltip).toContainText("69");
  await expect(tooltip).toContainText("State");
  await expect(tooltip).toContainText("SUPPRESSED_LOW_CONFIDENCE");
  await expect(tooltip).toContainText("Confidence");
  await expect(tooltip).toContainText("0.62");
  await expect(tooltip).toContainText("geocode confidence 低於 0.7");
  await expect(tooltip).toContainText("comparable sample size 低");
  await expect(tooltip).toContainText("FRESH");
  await expect(tooltip).toContainText("snap-expansion-20260628-0100");
  await expect(tooltip).toContainText("hz-score-v2.1.0");
  await expect(tooltip).toContainText("2026-06-28T01:00:00Z");
});

test("ODP-MAP-E2E-006 keeps the same evidence keyboard reachable with fallback text", async ({ page }) => {
  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone");

  await expect(page.getByTestId("map-evidence-tooltip")).toContainText("hz-1049 evidence");
  await page.getByTestId("map-evidence-trigger-hz-0881").focus();
  await page.keyboard.press("Enter");

  const tooltip = page.getByTestId("map-evidence-tooltip");
  const fallback = page.getByTestId("map-evidence-fallback");
  await expect(tooltip).toHaveAttribute("role", "tooltip");
  await expect(page.getByTestId("map-evidence-trigger-hz-0881")).toHaveAttribute("aria-describedby", "map-evidence-tooltip");
  await expect(page.getByTestId("map-evidence-trigger-hz-0881")).toHaveAttribute("aria-pressed", "true");

  for (const value of [
    "hz-0881 evidence",
    "Score",
    "84",
    "UNDER_REALIZED",
    "Confidence",
    "0.74",
    "公車站點資料 PARTIAL",
    "FRESH",
    "snap-expansion-20260628-0100",
    "hz-score-v2.1.0",
    "2026-06-28T01:00:00Z",
  ]) {
    await expect(tooltip).toContainText(value);
    await expect(fallback).toContainText(value);
  }
});
