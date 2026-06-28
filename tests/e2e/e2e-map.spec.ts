import { expect, test } from "@playwright/test";

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
  await expect.poll(async () => page.locator("canvas.deck-canvas").count()).toBeGreaterThan(0);
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
