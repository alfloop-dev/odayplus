import { expect, test } from "@playwright/test";
import { cellToBoundary } from "h3-js";
import sharp from "sharp";

test.describe.configure({ mode: "serial" });

test("HeatZone map renders nonblank MapLibre canvas with deck layers and local fallback", async ({ page }) => {
  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone");

  const map = page.getByTestId("heat-zone-map");
  await expect(map).toBeVisible();
  await expect(map).toHaveAttribute("data-selected-zone", "hz-1049");
  await expect(page.getByTestId("heat-zone-map-status")).toContainText("local MapLibre style");
  await expect(page.getByLabel("Map layer controls")).toContainText("H3 HeatZones");
  await expect(page.getByLabel("Map legend")).toContainText("candidate site");

  await expect.poll(async () => page.locator(".maplibregl-canvas").count()).toBeGreaterThan(0);
  await expect.poll(async () => canvasHasVisiblePixels(page, ".maplibregl-canvas")).toBe(true);
  await expect(page.getByTestId("heat-zone-deck-overlay")).toBeVisible();
  await expect.poll(async () => page.getByTestId("heat-zone-map-canvas").locator("canvas").count()).toBeGreaterThan(1);
});

test("HeatZone map selection stays synchronized with ranked list and drawer", async ({ page }) => {
  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone");

  await page.getByTestId("heatzone-row-hz-0773").click();
  await expect(page).toHaveURL(/selected=hz-0773/);
  await expect(page.getByTestId("heat-zone-map")).toHaveAttribute("data-selected-zone", "hz-0773");
  await expect(page.getByTestId("heatzone-row-hz-0773")).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("heatzone-drawer")).toContainText("SUPPRESSED_LOW_CONFIDENCE");
  await expect(page.getByTestId("heatzone-drawer")).toContainText("低信心 guard");
});

test("HeatZone map layer toggles persist through URL reload and sharing", async ({ page }) => {
  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone");

  await page.getByRole("checkbox", { name: "Listings" }).uncheck();
  await page.getByRole("checkbox", { name: "Freshness" }).uncheck();
  await page.getByRole("checkbox", { name: "Risk" }).uncheck();

  await expect(page).toHaveURL(/layers=h3%2Ccandidates%2Cconfidence/);
  await expect(page.getByTestId("heat-zone-map-status")).toContainText("layers h3,candidates,confidence");

  await page.reload();

  await expect(page.getByRole("checkbox", { name: "Listings" })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Freshness" })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Risk" })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: "H3 HeatZones" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Candidate sites" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Confidence" })).toBeChecked();

  const shareUrl = page.url();
  const sharedPage = await page.context().newPage();
  await sharedPage.goto(shareUrl);
  await expect(sharedPage.getByRole("checkbox", { name: "Listings" })).not.toBeChecked();
  await expect(sharedPage.getByRole("checkbox", { name: "Freshness" })).not.toBeChecked();
  await expect(sharedPage.getByRole("checkbox", { name: "Risk" })).not.toBeChecked();
  await expect(sharedPage.getByTestId("heat-zone-map-status")).toContainText("layers h3,candidates,confidence");
  await sharedPage.close();
});

test("HeatZone map direct picking opens the same drawer state as list fallback", async ({ page }) => {
  test.setTimeout(90_000);

  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone");
  await waitForMapProjection(page);

  await page.getByRole("checkbox", { name: "Listings" }).uncheck();
  await page.getByRole("checkbox", { name: "Candidate sites" }).uncheck();
  await clickMapCoordinate(page, [121.4629, 25.0116]);

  await expect(page).toHaveURL(/selected=hz-0881/);
  await expect(page.getByTestId("heat-zone-map")).toHaveAttribute("data-selected-zone", "hz-0881");
  await expect(page.getByTestId("heatzone-row-hz-0881")).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("heatzone-drawer")).toContainText("hz-0881");

  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone&layers=h3,listings,confidence,freshness,risk");
  await waitForMapProjection(page);
  await clickMapCoordinate(page, [121.4636, 25.0122]);

  await expect(page).toHaveURL(/\/w\/expansion\/listings\?selected=lst-9002&drawer=listing/);
  await expect(page.getByTestId("listing-drawer")).toContainText("lst-9002");
  await expect(page.getByTestId("listing-drawer")).toContainText("duplicate_group dg-77");

  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone&layers=h3,candidates,confidence,freshness,risk");
  await waitForMapProjection(page);
  await clickMapCoordinate(page, [121.226, 24.957]);

  await expect(page).toHaveURL(/\/w\/expansion\/candidates\?selected=cs-4109&drawer=candidate/);
  await expect(page.getByTestId("candidate-drawer")).toContainText("cs-4109");
  await expect(page.getByTestId("candidate-drawer")).toContainText("FAILED_HARD_RULE");
});

test("HeatZone deck semantic pixels distinguish layers and selected state", async ({ page }) => {
  test.setTimeout(90_000);

  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone&layers=h3,listings");
  await waitForMapProjection(page);
  await expect.poll(async () => page.getByTestId("heat-zone-map-canvas").locator("canvas").count()).toBeGreaterThan(1);

  const listingBlue = await waitForPixelCount(page, [121.5651, 25.0337], isListingBlue, 34, 20);
  const selectedBoundary = cellToBoundary("894ba0a4e23ffff", true) as [number, number][];
  const selectedDarkBlue = await waitForBoundaryPixelCount(page, selectedBoundary, isSelectedDarkBlue, 24, 30);

  expect(listingBlue).toBeGreaterThan(20);
  expect(selectedDarkBlue).toBeGreaterThan(6);

  await page.getByRole("checkbox", { name: "Listings" }).uncheck();
  const listingBlueAfterToggle = await countMapPixelsNear(page, [121.5651, 25.0337], isListingBlue, 34);

  expect(listingBlueAfterToggle).toBeLessThan(listingBlue / 2);

  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone&layers=h3,confidence");
  await waitForMapProjection(page);
  const confidenceBeforeToggle = await captureMapRegion(page, [121.5638, 25.033], 80);
  await page.getByRole("checkbox", { name: "Confidence" }).uncheck();
  const confidenceAfterToggle = await captureMapRegion(page, [121.5638, 25.033], 80);
  expect(countChangedPixels(confidenceBeforeToggle, confidenceAfterToggle, 18)).toBeGreaterThan(35);

  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone&layers=h3,freshness");
  await waitForMapProjection(page);
  const freshnessBeforeToggle = await captureMapRegion(page, [121.5638, 25.033], 90);
  await page.getByRole("checkbox", { name: "Freshness" }).uncheck();
  const freshnessAfterToggle = await captureMapRegion(page, [121.5638, 25.033], 90);
  expect(countChangedPixels(freshnessBeforeToggle, freshnessAfterToggle, 30)).toBeGreaterThan(60);

  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone&layers=h3,candidates");
  await waitForMapProjection(page);
  const candidatePurple = await waitForMapPixelCount(page, isCandidatePurple, 20);
  expect(candidatePurple).toBeGreaterThan(20);
});

async function waitForMapProjection(page: import("@playwright/test").Page) {
  await expect.poll(async () => page.evaluate(() => typeof window.__odpHeatZoneMapProject)).toBe("function");
}

async function clickMapCoordinate(page: import("@playwright/test").Page, coordinates: [number, number]) {
  const point = await page.evaluate((coords) => window.__odpHeatZoneMapProject?.(coords), coordinates);
  if (!point) throw new Error("HeatZone map projection helper is not available");
  await page.getByTestId("heat-zone-map-canvas").click({ position: point });
}

async function canvasHasVisiblePixels(page: import("@playwright/test").Page, selector: string) {
  return page.locator(selector).first().evaluate((canvas) => {
    const source = canvas as HTMLCanvasElement;
    const context = source.getContext("webgl2") ?? source.getContext("webgl");
    if (!context || source.width === 0 || source.height === 0) return false;

    const width = Math.min(source.width, 120);
    const height = Math.min(source.height, 80);
    const image = new Uint8Array(width * height * 4);
    context.readPixels(0, 0, width, height, context.RGBA, context.UNSIGNED_BYTE, image);
    for (let index = 0; index < image.length; index += 4) {
      const alpha = image[index + 3];
      const red = image[index];
      const green = image[index + 1];
      const blue = image[index + 2];
      if (alpha > 0 && (red !== 255 || green !== 255 || blue !== 255)) {
        return true;
      }
    }
    return false;
  });
}

async function countMapPixelsNear(
  page: import("@playwright/test").Page,
  coordinates: [number, number],
  predicate: (pixel: Rgba) => boolean,
  radius: number,
) {
  const map = page.getByTestId("heat-zone-map-canvas");
  const box = await map.boundingBox();
  if (!box) throw new Error("HeatZone map canvas bounding box is unavailable");
  const point = await page.evaluate((coords) => window.__odpHeatZoneMapProject?.(coords), coordinates);
  if (!point) throw new Error("HeatZone map projection helper is not available");
  const screenshot = await map.screenshot();
  const { data, info } = await sharp(screenshot)
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  const scaleX = info.width / box.width;
  const scaleY = info.height / box.height;
  const centerX = Math.round(point.x * scaleX);
  const centerY = Math.round(point.y * scaleY);
  const scaledRadius = Math.max(2, Math.round(radius * Math.max(scaleX, scaleY)));
  let count = 0;
  for (let y = Math.max(0, centerY - scaledRadius); y <= Math.min(info.height - 1, centerY + scaledRadius); y += 1) {
    for (let x = Math.max(0, centerX - scaledRadius); x <= Math.min(info.width - 1, centerX + scaledRadius); x += 1) {
      const offset = (y * info.width + x) * info.channels;
      if (predicate({ red: data[offset], green: data[offset + 1], blue: data[offset + 2], alpha: data[offset + 3] })) {
        count += 1;
      }
    }
  }
  return count;
}

async function captureMapRegion(
  page: import("@playwright/test").Page,
  coordinates: [number, number],
  radius: number,
) {
  const map = page.getByTestId("heat-zone-map-canvas");
  const box = await map.boundingBox();
  if (!box) throw new Error("HeatZone map canvas bounding box is unavailable");
  const point = await page.evaluate((coords) => window.__odpHeatZoneMapProject?.(coords), coordinates);
  if (!point) throw new Error("HeatZone map projection helper is not available");
  const screenshot = await map.screenshot();
  const { data, info } = await sharp(screenshot)
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  const scaleX = info.width / box.width;
  const scaleY = info.height / box.height;
  const centerX = Math.round(point.x * scaleX);
  const centerY = Math.round(point.y * scaleY);
  const scaledRadius = Math.max(2, Math.round(radius * Math.max(scaleX, scaleY)));
  const pixels: Rgba[] = [];
  for (let y = Math.max(0, centerY - scaledRadius); y <= Math.min(info.height - 1, centerY + scaledRadius); y += 1) {
    for (let x = Math.max(0, centerX - scaledRadius); x <= Math.min(info.width - 1, centerX + scaledRadius); x += 1) {
      const offset = (y * info.width + x) * info.channels;
      pixels.push({ red: data[offset], green: data[offset + 1], blue: data[offset + 2], alpha: data[offset + 3] });
    }
  }
  return pixels;
}

async function waitForPixelCount(
  page: import("@playwright/test").Page,
  coordinates: [number, number],
  predicate: (pixel: Rgba) => boolean,
  radius: number,
  minimum: number,
) {
  let latest = 0;
  await expect.poll(async () => {
    latest = await countMapPixelsNear(page, coordinates, predicate, radius);
    return latest;
  }).toBeGreaterThan(minimum);
  return latest;
}

async function waitForMapPixelCount(
  page: import("@playwright/test").Page,
  predicate: (pixel: Rgba) => boolean,
  minimum: number,
) {
  let latest = 0;
  await expect.poll(async () => {
    latest = await countMapPixels(page, predicate);
    return latest;
  }, { timeout: 15_000 }).toBeGreaterThan(minimum);
  return latest;
}

async function countMapPixels(
  page: import("@playwright/test").Page,
  predicate: (pixel: Rgba) => boolean,
) {
  const screenshot = await page.getByTestId("heat-zone-map-canvas").screenshot();
  const { data, info } = await sharp(screenshot)
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  let count = 0;
  for (let offset = 0; offset < data.length; offset += info.channels) {
    if (predicate({ red: data[offset], green: data[offset + 1], blue: data[offset + 2], alpha: data[offset + 3] })) {
      count += 1;
    }
  }
  return count;
}

async function waitForBoundaryPixelCount(
  page: import("@playwright/test").Page,
  coordinates: [number, number][],
  predicate: (pixel: Rgba) => boolean,
  radius: number,
  minimum: number,
) {
  let latest = 0;
  await expect.poll(async () => {
    latest = await countMapPixelsNearMany(page, coordinates, predicate, radius);
    return latest;
  }, { timeout: 15_000 }).toBeGreaterThan(minimum);
  return latest;
}

async function countMapPixelsNearMany(
  page: import("@playwright/test").Page,
  coordinates: [number, number][],
  predicate: (pixel: Rgba) => boolean,
  radius: number,
) {
  const map = page.getByTestId("heat-zone-map-canvas");
  const box = await map.boundingBox();
  if (!box) throw new Error("HeatZone map canvas bounding box is unavailable");
  const points = await page.evaluate((coords) => coords.map((coord) => window.__odpHeatZoneMapProject?.(coord)), coordinates);
  const screenshot = await map.screenshot();
  const { data, info } = await sharp(screenshot)
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  const scaleX = info.width / box.width;
  const scaleY = info.height / box.height;
  const scaledRadius = Math.max(2, Math.round(radius * Math.max(scaleX, scaleY)));
  let count = 0;
  for (const point of points) {
    if (!point) throw new Error("HeatZone map projection helper is not available");
    const centerX = Math.round(point.x * scaleX);
    const centerY = Math.round(point.y * scaleY);
    for (let y = Math.max(0, centerY - scaledRadius); y <= Math.min(info.height - 1, centerY + scaledRadius); y += 1) {
      for (let x = Math.max(0, centerX - scaledRadius); x <= Math.min(info.width - 1, centerX + scaledRadius); x += 1) {
        const offset = (y * info.width + x) * info.channels;
        if (predicate({ red: data[offset], green: data[offset + 1], blue: data[offset + 2], alpha: data[offset + 3] })) {
          count += 1;
        }
      }
    }
  }
  return count;
}

type Rgba = { red: number; green: number; blue: number; alpha: number };

function countChangedPixels(before: Rgba[], after: Rgba[], threshold: number) {
  const length = Math.min(before.length, after.length);
  let changed = 0;
  for (let index = 0; index < length; index += 1) {
    const delta =
      Math.abs(before[index].red - after[index].red) +
      Math.abs(before[index].green - after[index].green) +
      Math.abs(before[index].blue - after[index].blue);
    if (delta >= threshold) changed += 1;
  }
  return changed;
}

function isListingBlue(pixel: Rgba) {
  return pixel.alpha > 120 && pixel.blue > 120 && pixel.green > 70 && pixel.red < 100 && pixel.blue > pixel.red + 40;
}

function isCandidatePurple(pixel: Rgba) {
  return pixel.alpha > 120 && pixel.red > 80 && pixel.blue > 130 && pixel.green < 120 && pixel.blue > pixel.green + 35;
}

function isSelectedDarkBlue(pixel: Rgba) {
  return pixel.alpha > 150 && pixel.red < 90 && pixel.green < 110 && pixel.blue > 55 && pixel.blue < 190;
}

test("HeatZone map tooltip displays complete metadata on hover", async ({ page }) => {
  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone");
  await expect(page.getByTestId("exp-heatzone-page")).toBeVisible();

  const trigger = page.getByTestId("map-evidence-trigger-hz-1049");
  await trigger.hover();

  const tooltip = page.getByTestId("map-evidence-tooltip");
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toContainText("Score");
  await expect(tooltip).toContainText("91");
  await expect(tooltip).toContainText("STILL_EXPANDABLE");
  await expect(tooltip).toContainText("0.86");
});

test("Fallback Ranked HeatZone table exhibits required column parity", async ({ page }) => {
  await page.goto("/w/expansion/heatzone");

  const table = page.getByLabel("Ranked HeatZone list");
  await expect(table).toBeVisible();

  // Check table headers
  await expect(table.locator("th").nth(0)).toContainText("Rank");
  await expect(table.locator("th").nth(1)).toContainText("Area");
  await expect(table.locator("th").nth(2)).toContainText("Score");
  await expect(table.locator("th").nth(3)).toContainText("State");
  await expect(table.locator("th").nth(4)).toContainText("Confidence");
  await expect(table.locator("th").nth(5)).toContainText("Listings");
  await expect(table.locator("th").nth(6)).toContainText("Action");

  // Check content inside a row
  const firstRow = table.locator("tbody tr").first();
  await expect(firstRow.locator("td").nth(0)).toContainText("#1");
  await expect(firstRow.locator("td").nth(1)).toContainText("台北市信義區");
  await expect(firstRow.locator("td").nth(2)).toContainText("91 (80-100)");
  await expect(firstRow.locator("td").nth(3)).toContainText("STILL_EXPANDABLE");
  await expect(firstRow.locator("td").nth(4)).toContainText("0.86");
  await expect(firstRow.locator("td").nth(5)).toContainText("8");
  await expect(firstRow.locator("td").nth(6)).toContainText("打開 Drawer");
});

test("No-geometry query parameter renders the inline warning", async ({ page }) => {
  await page.goto("/w/expansion/heatzone?noGeometry=true");

  // Verify map warning is visible
  const warning = page.getByTestId("map-geometry-warning");
  await expect(warning).toBeVisible();
  await expect(warning).toContainText("地圖 geometry 尚未可用；列表仍可用於審查");
});

test("HeatZoneScoreCard drawer displays full score breakdown and evidence details", async ({ page }) => {
  await page.goto("/w/expansion/heatzone?selected=hz-1049&drawer=zone");

  const drawer = page.getByTestId("heatzone-drawer");
  await expect(drawer).toBeVisible();

  // Verify Score Breakdown
  await expect(drawer.getByText("Score Breakdown")).toBeVisible();
  await expect(drawer.getByText("Unmet Demand")).toBeVisible();
  await expect(drawer.locator("dd").nth(0)).toContainText("0.9100");
  await expect(drawer.locator("dd").nth(1)).toContainText("0.7800");

  // Verify Evidence Details
  await expect(drawer.getByText("Evidence Details")).toBeVisible();
  await expect(drawer.getByText("POI count")).toBeVisible();
  await expect(drawer.getByText("Median rent")).toBeVisible();
  await expect(drawer.getByText("NT$ 85,000")).toBeVisible();

  // Verify Version/Audit and Next actions
  await expect(drawer.getByText("Confidence & Quality")).toBeVisible();
  await expect(drawer.getByText("建立實勘/研究")).toBeVisible();
  await expect(drawer.getByText("重新計算")).toBeVisible();
  await expect(drawer.getByText("導出證據")).toBeVisible();
});
