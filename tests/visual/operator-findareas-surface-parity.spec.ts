import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { acquireOperatorBackendLock, releaseOperatorBackendLock } from "../e2e/_operatorBackendLock";

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
for (const width of [1440, 1024, 390]) {
  test(`Find Areas hierarchy and truthful navigation at ${width}`, async ({ page, request }, info) => {
    await page.setViewportSize({ width, height: 900 });
    page.setDefaultTimeout(30_000);
    await page.addInitScript(() => {
      sessionStorage.setItem("oday.operator.role", "expansion-manager");
      sessionStorage.setItem("oday.operator.subject", "operator-expansion-manager");
      sessionStorage.setItem("oday.operator.tenant", "tenant-a");
    });
    const api = process.env.ODP_API_BASE_URL ?? "http://127.0.0.1:8099";
    expect((await request.post(`${api}/api/v1/operator/network-listings/reset`, { headers })).status()).toBe(200);
    const original = await (await request.get(`${api}/api/v1/operator/network-listings`, { headers })).json();
    expect((await request.get("/api/v1/operator/network-listings", { headers })).status()).toBe(200);
    await page.goto("/operator?ws=network&hz=HZ-01&lens=demand");
    const panel = page.getByTestId("network-panel-find-areas");
    await expect(panel.getByLabel("Selected HeatZone detail")).toContainText("HZ-01");
    await expect(panel.getByLabel("Selected HeatZone detail")).toContainText("L-2024", { timeout: 30_000 });
    await expect(panel.getByTestId("heat-zone-map-loading")).toHaveCount(0, { timeout: 30_000 });
    await expect(panel.getByTestId("heat-zone-map-canvas")).toBeVisible();
    const detail = panel.getByLabel("Selected HeatZone detail");
    const boxes = { panel: await measure(panel), lenses: await measure(panel.getByLabel("HeatZone lenses")), firstLens: await measure(panel.getByLabel("HeatZone lenses").getByRole("button").first()), map: await measure(panel.getByTestId("heat-zone-map-canvas")), tray: await measure(panel.getByLabel("Recommended find area tray")), detail: await measure(detail) };
    await shot(page, info, `${phase}-findareas-${width}`);
    let designGeometry;
    if (process.env.NETWORK_PARITY_DESIGN === "1" && width !== 1024) {
      const design = await page.context().newPage();
      await design.setViewportSize({ width, height: 900 });
      await design.route(/^https?:\/\//, (route) => route.abort());
      await design.goto(pathToFileURL(path.resolve("docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html")).href, { waitUntil: "domcontentloaded" });
      await design.getByRole("button", { name: /林.*營運主管/ }).click();
      await design.getByRole("button", { name: /展店經理.*林曉青/ }).click();
      await design.getByRole("button", { name: /展店與店網.*Network/ }).click();
      await expect(design.getByText(/已切換 Demo 角色/)).toBeHidden({ timeout: 15_000 });
      const reference = design.locator('[data-screen-label="Network 找區域"]');
      await expect(reference).toContainText("為什麼是這一區");
      await shot(design, info, `design-findareas-${width}`);
      designGeometry = await measure(reference);
      await design.close();
    }
    const document = await page.evaluate(() => ({ width: innerWidth, scrollWidth: window.document.documentElement.scrollWidth }));
    await save(info, `${phase}-geometry-${width}.json`, { boxes, document, design: designGeometry });
    const axe = await new AxeBuilder({ page }).include('[data-testid="network-panel-find-areas"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    await save(info, `${phase}-axe-${width}.json`, axe);
    if (phase !== "after") return;
    expect(document.scrollWidth).toBeLessThanOrEqual(width);
    for (const [name, box] of Object.entries(boxes)) {
      expect(box.x, name).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, name).toBeLessThanOrEqual(width);
    }
    expect(boxes.firstLens.height).toBeLessThan(35);
    if (width === 1440) {
      expect(boxes.lenses.width).toBe(206);
      expect(boxes.detail.width).toBe(330);
      expect(boxes.map.height).toBe(424);
      expect(boxes.detail.y).toBe(boxes.lenses.y);
    } else {
      expect(boxes.detail.y).toBeGreaterThanOrEqual(boxes.tray.y + boxes.tray.height);
    }
    await expect(detail.getByTestId("find-areas-facts")).toBeVisible();
    await expect(detail.getByTestId("find-areas-primary")).toBeVisible();
    await expect(detail.getByRole("button", { name: "指派找點任務", exact: true })).toBeDisabled();
    await expect(detail.getByRole("button", { name: "建立物件搜尋條件", exact: true })).toBeDisabled();
    expect(axe.violations).toEqual([]);
    // Native buttons select a different zone and lens by keyboard; URL state restores.
    const zonePick = panel.getByLabel("Recommended find area tray").getByRole("button", { name: /HZ-02/ });
    await zonePick.focus();
    await page.keyboard.press("Enter");
    await expect(detail).toContainText("HZ-02");
    await expect(page).toHaveURL(/hz=HZ-02/);
    const fit = panel.getByLabel("HeatZone lenses").getByRole("button", { name: "品牌適配", exact: true });
    await fit.focus();
    await page.keyboard.press("Enter");
    await expect(fit).toHaveAttribute("aria-pressed", "true");
    await expect(page).toHaveURL(/lens=fit/);
    const navigation: Array<Record<string, unknown>> = [{ action: "selected-fit", url: page.url() }];
    const unexpectedWrites: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/v1/operator/network-") && !["GET", "HEAD", "OPTIONS"].includes(request.method())) {
        unexpectedWrites.push(`${request.method()} ${request.url()}`);
      }
    });
    async function verifyRestoration(action: string, zoneId: string, lens: "demand" | "fit") {
      // Save the raw URL before assertions too, so a failure cannot lose its
      // navigation receipt. A matching fixture ID alone is NOT hydration proof.
      navigation.push({ action, url: page.url() });
      await save(info, `navigation-${width}.json`, navigation);
      await expect.poll(() => new URL(page.url()).searchParams.get("hz")).toBe(zoneId);
      await expect.poll(() => new URL(page.url()).searchParams.get("lens")).toBe(lens);
      const read = await request.get(`${api}/api/v1/operator/network-listings?selectedHeatZoneId=${zoneId}&lens=${lens}`, { headers });
      expect(read.status()).toBe(200);
      const snapshot = await read.json();
      const restoredZone = snapshot.heatZones.find((zone: { id: string }) => zone.id === zoneId);
      expect(restoredZone).toBeTruthy();
      await expect(detail).toContainText(zoneId);
      await expect(detail).toContainText(restoredZone.label, { timeout: 30_000 });
      await expect(panel.getByLabel("HeatZone lenses").getByRole("button", { name: lens === "fit" ? "品牌適配" : "需求熱度", exact: true })).toHaveAttribute("aria-pressed", "true");
      await expect(panel.getByTestId("heat-zone-map-loading")).toHaveCount(0, { timeout: 30_000 });
      await expect(panel.getByTestId("heat-zone-map-canvas")).toBeVisible();
      await expect(page.getByText("network-listings API unavailable; using local fixtures")).toHaveCount(0);
      const geometry = { detail: await measure(detail), map: await measure(panel.getByTestId("heat-zone-map-canvas")), document: await page.evaluate(() => ({ width: innerWidth, scrollWidth: window.document.documentElement.scrollWidth })) };
      expect(geometry.document.scrollWidth).toBeLessThanOrEqual(width);
      for (const box of [geometry.detail, geometry.map]) {
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width);
        expect(box.height).toBeGreaterThan(100);
      }
      await shot(page, info, `after-${action}-${width}`);
      navigation[navigation.length - 1] = { action, url: page.url(), readStatus: read.status(), zoneId, label: restoredZone.label, lens, geometry };
      await save(info, `navigation-${width}.json`, navigation);
    }
    // Reload FIRST, before waiting for the new API snapshot: back/forward must
    // survive a fresh Next router/component boot, including changing the zone.
    await page.reload();
    await verifyRestoration("reload-before-back", "HZ-02", "fit");
    await page.goBack();
    await verifyRestoration("back-lens", "HZ-02", "demand");
    await page.goBack();
    await verifyRestoration("back-zone", "HZ-01", "demand");
    await page.goForward();
    await verifyRestoration("forward-zone", "HZ-02", "demand");
    await page.goForward();
    await verifyRestoration("forward-lens", "HZ-02", "fit");
    await page.reload();
    await verifyRestoration("reload-after-forward", "HZ-02", "fit");
    // Later-spec search remains reachable without covering the map or detail.
    await panel.locator("summary").click();
    await expect(panel.getByTestId("geocoder-query-input")).toBeVisible();
    await shot(page, info, `after-search-open-${width}`);
    const searchAxe = await new AxeBuilder({ page }).include('[data-testid="network-panel-find-areas"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    await save(info, `after-search-axe-${width}.json`, searchAxe);
    expect(searchAxe.violations).toEqual([]);
    await panel.locator("summary").click();
    await panel.getByTestId("find-areas-primary").click();
    await expect(page.getByTestId("network-panel-listings")).toBeVisible();
    await expect(page.getByTestId("listing-zone-filter-chip")).toContainText("HZ-02");
    const final = await request.get(`${api}/api/v1/operator/network-listings`, { headers });
    expect(final.status()).toBe(200);
    const payload = await final.json();
    expect(payload).toEqual(original);
    expect(unexpectedWrites).toEqual([]);
    await save(info, `unexpected-writes-${width}.json`, unexpectedWrites);
    await save(info, `unchanged-receipt-${width}.json`, payload);
  });
}
