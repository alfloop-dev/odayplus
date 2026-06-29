import { expect, test } from "@playwright/test";

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
  test.setTimeout(60_000);

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
