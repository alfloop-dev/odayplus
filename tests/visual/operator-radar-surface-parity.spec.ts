import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { acquireOperatorBackendLock, releaseOperatorBackendLock } from "../e2e/_operatorBackendLock";

// Real isolated fixture service reads, no intercepted product responses.
test.describe.configure({ timeout: 120_000 });
test.beforeAll(acquireOperatorBackendLock);
test.afterAll(releaseOperatorBackendLock);
test.use({ extraHTTPHeaders: {} });
const phase = process.env.NETWORK_PARITY_CAPTURE_PHASE ?? "after";
const headers = { "x-subject-id": "operator-expansion-manager", "x-roles": "expansion_user,site_reviewer", "x-operator-role": "expansion-manager", "x-tenant-id": "tenant-a" };
async function measure(locator: Locator) {
  return locator.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const css = getComputedStyle(el);
    return { x: r.x, y: r.y, width: r.width, height: r.height, scrollWidth: el.scrollWidth, columns: css.gridTemplateColumns, overflowX: css.overflowX };
  });
}
async function artifact(info: TestInfo, name: string) {
  const target = path.join(process.env.NETWORK_PARITY_EVIDENCE_DIR ?? info.outputDir, name);
  await mkdir(path.dirname(target), { recursive: true });
  return target;
}
async function save(info: TestInfo, name: string, content: unknown) {
  await writeFile(await artifact(info, name), JSON.stringify(content, null, 2));
}
async function shot(page: Page, info: TestInfo, name: string) {
  await page.evaluate(() => scrollTo(0, 0));
  const clip = await page.evaluate(() => ({ x: 0, y: 0, width: innerWidth, height: document.documentElement.scrollHeight }));
  await page.screenshot({ path: await artifact(info, `${name}.png`), fullPage: true, clip, animations: "disabled" });
}
for (const width of [1440, 390]) {
  test(`Radar sources, list, detail and empty filter at ${width}`, async ({ page, request }, info) => {
    await page.setViewportSize({ width, height: 900 });
    page.setDefaultTimeout(30_000);
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
      sessionStorage.setItem("oday.operator.tenant", "tenant-a");
    });
    const api = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
    expect((await request.post(`${api}/api/v1/operator/network-listings/reset`, { headers })).status()).toBe(200);
    const snapshot = await request.get(`${api}/api/v1/operator/network-listings`, { headers });
    expect(snapshot.status()).toBe(200);
    const original = await snapshot.json();
    // Warm the real BFF before measuring its hydrated data.
    expect((await request.get("/api/v1/operator/network-listings", { headers })).status()).toBe(200);
    await page.goto("/operator?ws=network");
    await page.getByTestId("network-tab-1").click();
    const panel = page.getByTestId("network-panel-listings");
    await panel.getByTestId("listing-filter-all").click();
    const row = panel.getByTestId("listing-row-L-2024");
    await expect(row).toContainText("Clean", { timeout: 15_000 });
    await expect(panel.getByTestId("intake-inbox-loading")).toBeHidden();
    await row.click();
    const layout = panel.getByLabel("來源篩選").locator("..");
    const detail = panel.getByLabel("Listing detail");
    const boxes = { panel: await measure(panel), layout: await measure(layout), sources: await measure(panel.getByLabel("Listing sources")), source: await measure(panel.getByLabel("Listing sources").locator("article").first()), filters: await measure(panel.getByLabel("來源篩選")), inbox: await measure(panel.getByTestId("network-listing-table").locator("..")), detail: await measure(detail), search: await measure(panel.getByTestId("intake-search-input")), searchControls: await measure(panel.getByTestId("intake-filter-method").locator("..")) };
    await shot(page, info, `${phase}-radar-${width}`);
    let designGeometry;
    if (process.env.NETWORK_PARITY_DESIGN === "1") {
      const design = await page.context().newPage();
      await design.setViewportSize({ width, height: 900 });
      await design.route(/^https?:\/\//, (route) => route.abort());
      await design.goto(pathToFileURL(path.resolve("docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html")).href, { waitUntil: "domcontentloaded" });
      await design.getByRole("button", { name: /林.*營運主管/ }).click();
      await design.getByRole("button", { name: /展店經理.*林曉青/ }).click();
      await design.getByRole("button", { name: /展店與店網.*Network/ }).click();
      await design.getByRole("button", { name: /^物件雷達\s+Listing Radar/ }).dispatchEvent("click");
      await expect(design.getByText(/已切換 Demo 角色/)).toBeHidden({ timeout: 15_000 });
      const reference = design.locator('[data-screen-label="Network 物件雷達"]');
      await expect(reference).toContainText("物件收件匣");
      await shot(design, info, `design-radar-${width}`);
      designGeometry = await measure(reference);
      await design.close();
    }
    const document = await page.evaluate(() => ({ width: innerWidth, scrollWidth: window.document.documentElement.scrollWidth }));
    await save(info, `${phase}-geometry-${width}.json`, { boxes, document, design: designGeometry });
    const axe = await new AxeBuilder({ page }).include('[data-testid="network-panel-listings"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    await save(info, `${phase}-axe-${width}.json`, axe);
    if (phase !== "after") {
      await panel.getByTestId("listing-zone-filter-chip").click();
      await panel.getByLabel("來源篩選").getByRole("button", { name: /^仲介/ }).click();
      await expect(panel.getByTestId("network-listing-table")).toBeHidden();
      await shot(page, info, `${phase}-empty-${width}`);
      return;
    }
    expect(document.scrollWidth).toBeLessThanOrEqual(width);
    await expect(page.getByRole("main")).toHaveCount(1);
    for (const [name, box] of Object.entries(boxes)) {
      expect(box.x, name).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, name).toBeLessThanOrEqual(width);
      if (name !== "filters") expect(box.scrollWidth, name).toBeLessThanOrEqual(Math.ceil(box.width));
    }
    expect(boxes.search.height).toBeLessThan(45);
    if (width === 1440) {
      expect(boxes.sources.columns.split(" ")).toHaveLength(6);
      expect(boxes.source.width).toBeLessThan(240);
      expect(boxes.filters.width).toBe(180);
      expect(boxes.detail.width).toBe(348);
      expect(boxes.inbox.x).toBe(boxes.filters.x + 194);
      expect(boxes.detail.x).toBe(boxes.inbox.x + boxes.inbox.width + 14);
      expect(Math.abs(boxes.search.y - boxes.searchControls.y)).toBeLessThanOrEqual(2);
    } else {
      expect(boxes.detail.y).toBeGreaterThanOrEqual(boxes.inbox.y + boxes.inbox.height);
      expect(boxes.filters.overflowX).toBe("auto");
    }
    expect(axe.violations).toEqual([]);
    // Keyboard selection must not trigger a durable conversion.
    await row.getByRole("button", { name: /查看.*L-2024/ }).focus();
    await page.keyboard.press("Enter");
    await expect(detail).toContainText("L-2024");
    await expect(panel.getByRole("button", { name: "地圖", exact: true })).toBeDisabled();
    // Select a source with no rows in HZ-01; stale off-filter detail must retire.
    await panel.getByTestId("listing-zone-filter-chip").click();
    await panel.getByLabel("來源篩選").getByRole("button", { name: /^仲介/ }).click();
    await expect(panel.getByTestId("network-listing-table")).toBeHidden();
    await expect(detail).toContainText("此篩選下沒有物件");
    await expect(detail.getByTestId("listing-detail-primary")).toHaveCount(0);
    await shot(page, info, `${phase}-empty-${width}`);
    const final = await request.get(`${api}/api/v1/operator/network-listings`, { headers });
    expect(final.status()).toBe(200);
    const finalPayload = await final.json();
    expect(finalPayload).toEqual(original);
    await save(info, `unchanged-receipt-${width}.json`, finalPayload);
  });
}
